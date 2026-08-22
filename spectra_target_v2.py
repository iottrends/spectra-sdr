#!/usr/bin/env python3

#
# Spectra SDR - LiteX Target v2 (CPU-less PCIe DMA <-> AD9364 + USB)
#

import os
import argparse
import math

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer

from spectra_platform import Platform

from litex.gen import *
from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.led import LedChaser
from litex.soc.interconnect import stream
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import *
from litex.soc.integration.soc import SoCRegion

from litex.soc.cores.hyperbus import HyperRAM as LiteHyperBus

# LitePCIe
from litepcie.phy.s7pciephy import S7PCIEPHY
from litepcie.core import LitePCIeEndpoint, LitePCIeMSI
from litepcie.frontend.dma import LitePCIeDMA
from litepcie.frontend.wishbone import LitePCIeWishboneMaster
from litepcie.common import dma_layout
from litepcie.software import generate_litepcie_software

from litex.soc.cores.icap import ICAP
from litex.soc.cores.xadc import XADC
from litex.soc.cores.dna  import DNA


# ========================= AD9364 SPI Master =========================

class AD9364SPIMaster(LiteXModule):
    def __init__(self, pads, data_width=24, clk_divider=8):
        self._control = CSRStorage(fields=[
            CSRField("start",  size=1, offset=0, pulse=True),
            CSRField("length", size=8, offset=8, reset=24)
        ])
        self._status = CSRStatus(fields=[CSRField("done", size=1, offset=0)])
        self._mosi = CSRStorage(data_width)
        self._miso = CSRStatus(data_width)

        start  = self._control.fields.start
        length = self._control.fields.length
        done   = self._status.fields.done

        chip_select = Signal()
        shift       = Signal()
        clk_count   = Signal(int(math.log2(clk_divider)) + 1)
        clk_set     = Signal()
        clk_clr     = Signal()

        self.sync += [
            clk_count.eq(clk_count + 1),
            If(clk_set, pads.clk.eq(chip_select)),
            If(clk_clr, pads.clk.eq(0), clk_count.eq(0)),
        ]
        self.comb += [
            clk_set.eq(clk_count == (clk_divider // 2 - 1)),
            clk_clr.eq(clk_count == (clk_divider - 1)),
        ]

        cnt = Signal(8)
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(start, NextState("WAIT_CLK")),
            done.eq(1),
            NextValue(cnt, 0),
        )
        fsm.act("WAIT_CLK",
            If(clk_clr, NextState("SHIFT")),
        )
        fsm.act("SHIFT",
            If(cnt == length,
                NextState("END")
            ).Else(
                NextValue(cnt, cnt + clk_clr)
            ),
            chip_select.eq(1),
            shift.eq(1),
        )
        fsm.act("END",
            If(clk_set, NextState("IDLE")),
            shift.eq(1),
        )

        self.comb += pads.cs_n.eq(~chip_select)

        mosi_shift = Signal(data_width)
        miso_shift = Signal(data_width)
        self.sync += [
            If(start,              mosi_shift.eq(self._mosi.storage)),
            If(clk_clr & shift,    mosi_shift.eq(Cat(Signal(), mosi_shift[:-1]))),
            If(clk_set,            pads.mosi.eq(mosi_shift[-1])),
            If(clk_set & shift,    miso_shift.eq(Cat(pads.miso, miso_shift[:-1]))),
        ]
        self.comb += self._miso.status.eq(miso_shift)


# ========================= AD9364 PHY =========================

AD9361PHY_2R2T_MODE = 0
AD9361PHY_1R1T_MODE = 1

def phy_layout():
    return stream.EndpointDescription([
        ("ia", 12), ("qa", 12),
        ("ib", 12), ("qb", 12),
    ])


class AD9364PHY(LiteXModule):
    def __init__(self, pads):
        self.sink    = sink   = stream.Endpoint(phy_layout())
        self.source  = source = stream.Endpoint(phy_layout())
        self.control = CSRStorage(fields=[
            CSRField("mode", size=1, offset=0, values=[
                ("``0b0``", "2R2T mode (8-way interleaving)"),
                ("``0b1``", "1R1T mode (4-way interleaving)"),
            ]),
            CSRField("loopback", size=1, offset=1, values=[
                ("``0b0``", "Normal operation"),
                ("``0b1``", "Loopback TX to RX internally"),
            ]),
        ])

        # Control signals (CDC sys → rfic)
        mode     = Signal()
        loopback = Signal()
        self.specials += [
            MultiReg(self.control.fields.mode,     mode,     odomain="rfic"),
            MultiReg(self.control.fields.loopback, loopback, odomain="rfic"),
        ]

        # ---- RX PHY ----

        # DATA_CLK → rfic clock domain via IBUFDS + BUFG
        rx_clk_ibufds = Signal()
        self.specials += [
            Instance("IBUFDS",
                i_I  = pads.rx_clk_p,
                i_IB = pads.rx_clk_n,
                o_O  = rx_clk_ibufds,
            ),
            Instance("BUFG",
                i_I = rx_clk_ibufds,
                o_O = ClockSignal("rfic"),
            ),
            AsyncResetSynchronizer(ClockDomain("rfic"), ResetSignal("sys")),
        ]

        # RX_FRAME via IBUFDS + IDDR
        rx_frame_ibufds = Signal()
        rx_frame        = Signal()
        self.specials += [
            Instance("IBUFDS",
                i_I  = pads.rx_frame_p,
                i_IB = pads.rx_frame_n,
                o_O  = rx_frame_ibufds,
            ),
            Instance("IDDR",
                p_DDR_CLK_EDGE = "SAME_EDGE_PIPELINED",
                i_C  = ClockSignal("rfic"),
                i_CE = 1, i_S = 0, i_R = 0,
                i_D  = rx_frame_ibufds,
                o_Q1 = rx_frame,
                o_Q2 = Open(),
            ),
        ]

        # RX frame counter — reset to 1 on RX_FRAME rising edge
        rx_count   = Signal(2)
        rx_frame_d = Signal()
        self.sync.rfic += [
            rx_frame_d.eq(rx_frame),
            rx_count.eq(rx_count + 1),
            If(rx_frame & ~rx_frame_d,
                Case(mode, {
                    AD9361PHY_1R1T_MODE: rx_count[0].eq(1),
                    AD9361PHY_2R2T_MODE: rx_count   .eq(1),
                })
            ),
        ]

        # RX data lanes: 6× IBUFDS + IDDR → half-word I (rising) / Q (falling)
        rx_data_ibufds = Signal(6)
        rx_data_half_i = Signal(6)
        rx_data_half_q = Signal(6)
        for i in range(6):
            self.specials += [
                Instance("IBUFDS",
                    i_I  = pads.rx_data_p[i],
                    i_IB = pads.rx_data_n[i],
                    o_O  = rx_data_ibufds[i],
                ),
                Instance("IDDR",
                    p_DDR_CLK_EDGE = "SAME_EDGE_PIPELINED",
                    i_C  = ClockSignal("rfic"),
                    i_CE = 1, i_S = 0, i_R = 0,
                    i_D  = rx_data_ibufds[i],
                    o_Q1 = rx_data_half_i[i],
                    o_Q2 = rx_data_half_q[i],
                ),
            ]

        # Assemble 12-bit I/Q words: single sync block, shift trick builds MSB/LSB correctly
        # count[1]==0 → IA/QA (cycles 0,1), count[1]==1 → IB/QB (cycles 2,3)
        # Each cycle: new 6-bit half → [0:6], previous [0:6] promoted → [6:12]
        rx_data_ia = Signal(12)
        rx_data_qa = Signal(12)
        rx_data_ib = Signal(12)
        rx_data_qb = Signal(12)
        self.sync.rfic += [
            Case(rx_count[1], {
                0b0: [
                    rx_data_ia[0: 6].eq(rx_data_half_i),
                    rx_data_ia[6:12].eq(rx_data_ia[0:6]),
                    rx_data_qa[0: 6].eq(rx_data_half_q),
                    rx_data_qa[6:12].eq(rx_data_qa[0:6]),
                ],
                0b1: [
                    rx_data_ib[0: 6].eq(rx_data_half_i),
                    rx_data_ib[6:12].eq(rx_data_ib[0:6]),
                    rx_data_qb[0: 6].eq(rx_data_half_q),
                    rx_data_qb[6:12].eq(rx_data_qb[0:6]),
                ],
            }),
        ]

        # Output valid one cycle after rx_count==3 so all words are fully assembled
        self.sync.rfic += [
            source.valid.eq(0),
            If(rx_count == 0,
                source.valid.eq(1),
                source.ia.eq(rx_data_ia),
                source.qa.eq(rx_data_qa),
                source.ib.eq(rx_data_ib),
                source.qb.eq(rx_data_qb),
            ),
        ]

        # TX → RX loopback (bypasses PHY when enabled)
        self.sync.rfic += [
            If(loopback,
                source.valid.eq(sink.valid & sink.ready),
                source.ia.eq(sink.ia),
                source.qa.eq(sink.qa),
                source.ib.eq(sink.ib),
                source.qb.eq(sink.qb),
            ),
        ]

        # ---- TX PHY ----

        # Accept new sample every 4 rfic clocks
        tx_count = Signal(2)
        self.sync.rfic += tx_count.eq(tx_count + 1)
        self.comb += sink.ready.eq(tx_count == 0)

        # Latch TX samples; zero-fill when invalid to avoid spurs
        tx_data_ia = Signal(12)
        tx_data_qa = Signal(12)
        tx_data_ib = Signal(12)
        tx_data_qb = Signal(12)
        self.sync.rfic += [
            If(sink.ready,
                tx_data_ia.eq(0), tx_data_qa.eq(0),
                tx_data_ib.eq(0), tx_data_qb.eq(0),
                If(sink.valid,
                    tx_data_ia.eq(sink.ia), tx_data_qa.eq(sink.qa),
                    tx_data_ib.eq(sink.ib), tx_data_qb.eq(sink.qb),
                ),
            ),
        ]

        # FB_CLK output: ODDR toggling 1/0 + OBUFDS
        tx_clk_oddr = Signal()
        self.specials += [
            Instance("ODDR",
                p_DDR_CLK_EDGE = "SAME_EDGE",
                i_C  = ClockSignal("rfic"),
                i_CE = 1, i_S = 0, i_R = 0,
                i_D1 = 1, i_D2 = 0,
                o_Q  = tx_clk_oddr,
            ),
            Instance("OBUFDS",
                i_I  = tx_clk_oddr,
                o_O  = pads.tx_clk_p,
                o_OB = pads.tx_clk_n,
            ),
        ]

        # TX_FRAME: correct interleaving pattern for 1R1T and 2R2T
        tx_frame     = Signal()
        tx_frame_ddr = Signal()
        self.comb += [
            If(mode == AD9361PHY_1R1T_MODE,
                Case(tx_count, {  # 4-way: frame toggles every cycle
                    0b00: tx_frame.eq(1),
                    0b01: tx_frame.eq(0),
                    0b10: tx_frame.eq(1),
                    0b11: tx_frame.eq(0),
                }),
            ).Else(              # 2R2T: frame high for cycles 0,1 then low for 2,3
                Case(tx_count, {
                    0b00: tx_frame.eq(1),
                    0b01: tx_frame.eq(1),
                    0b10: tx_frame.eq(0),
                    0b11: tx_frame.eq(0),
                }),
            ),
        ]
        self.specials += [
            Instance("ODDR",
                p_DDR_CLK_EDGE = "SAME_EDGE",
                i_C  = ClockSignal("rfic"),
                i_CE = 1, i_S = 0, i_R = 0,
                i_D1 = tx_frame, i_D2 = tx_frame,
                o_Q  = tx_frame_ddr,
            ),
            Instance("OBUFDS",
                i_I  = tx_frame_ddr,
                o_O  = pads.tx_frame_p,
                o_OB = pads.tx_frame_n,
            ),
        ]

        # TX data lanes: mux 6-bit MSB/LSB halves of I/Q onto 6 LVDS pairs via ODDR
        tx_data_half_i = Signal(6)
        tx_data_half_q = Signal(6)
        tx_data_obufds = Signal(6)
        self.comb += [
            Case(tx_count, {
                0b00: [tx_data_half_i.eq(tx_data_ia[6:12]), tx_data_half_q.eq(tx_data_qa[6:12])],
                0b01: [tx_data_half_i.eq(tx_data_ia[0: 6]), tx_data_half_q.eq(tx_data_qa[0: 6])],
                0b10: [tx_data_half_i.eq(tx_data_ib[6:12]), tx_data_half_q.eq(tx_data_qb[6:12])],
                0b11: [tx_data_half_i.eq(tx_data_ib[0: 6]), tx_data_half_q.eq(tx_data_qb[0: 6])],
            }),
        ]
        for i in range(6):
            self.specials += [
                Instance("ODDR",
                    p_DDR_CLK_EDGE = "SAME_EDGE",
                    i_C  = ClockSignal("rfic"),
                    i_CE = 1, i_S = 0, i_R = 0,
                    i_D1 = tx_data_half_i[i],
                    i_D2 = tx_data_half_q[i],
                    o_Q  = tx_data_obufds[i],
                ),
                Instance("OBUFDS",
                    i_I  = tx_data_obufds[i],
                    o_O  = pads.tx_data_p[i],
                    o_OB = pads.tx_data_n[i],
                ),
            ]


# ========================= AD9364 Core =========================

def _sign_extend(s, dw):
    return Cat(s, Replicate(s[-1], dw - len(s)))


class AD9364Core(LiteXModule):
    def __init__(self, rfic_pads, spi_pads):
        self.sink   = stream.Endpoint(dma_layout(64))
        self.source = stream.Endpoint(dma_layout(64))

        self.phy = AD9364PHY(rfic_pads)
        self.spi = AD9364SPIMaster(spi_pads)

        # Clock domain crossings: sys <-> rfic
        self.tx_cdc = stream.ClockDomainCrossing(
            layout=dma_layout(64), cd_from="sys", cd_to="rfic")
        self.rx_cdc = stream.ClockDomainCrossing(
            layout=dma_layout(64), cd_from="rfic", cd_to="sys")

        # AD9364 reset control (ad9364_reset CSR).
        # rst_n is held low (RESETB asserted) while sys is settling (PLL not
        # yet locked on the 40MHz TCXO). The instant sys comes out of reset,
        # it self-releases once, with no software involvement. After that,
        # it's a plain read/write bit: reads always reflect the live pin
        # state, and software can assert/release it again at any time
        # without a full FPGA reconfigure — see reset_ad9364() in
        # scripts/ad9364_init.py.
        self.reset = CSR()

        rst_n_r = Signal(reset=0)  # drives the physical pin directly
        sw_ctrl = Signal(reset=0)  # sticky: has software ever written this bit?

        self.sync += [
            If(ResetSignal("sys"),
                rst_n_r.eq(0),
                sw_ctrl.eq(0),
            ).Elif(self.reset.re,
                rst_n_r.eq(self.reset.r),  # software wrote — it owns the bit now
                sw_ctrl.eq(1),
            ).Elif(~sw_ctrl,
                rst_n_r.eq(1),              # auto-release, once, before software ever writes
            )
            # else: software already owns it, no new write this cycle -> hold.
        ]

        self.comb += [
            self.reset.w.eq(rst_n_r),  # readback always mirrors the live pin state
            rfic_pads.rst_n.eq(rst_n_r),
            rfic_pads.enable.eq(1),                    # keep chip enabled.
            rfic_pads.txnrx.eq(0),                     # 0 = FDD mode (TX and RX simultaneous).
        ]

        self.comb += [
            # Host → AD9364 (TX path: sys → rfic)
            self.sink.connect(self.tx_cdc.sink),
            self.tx_cdc.source.ready.eq(self.phy.sink.ready),
            self.phy.sink.valid.eq(self.tx_cdc.source.valid),
            self.phy.sink.ia.eq(self.tx_cdc.source.data[ 0:12]),
            self.phy.sink.qa.eq(self.tx_cdc.source.data[16:28]),
            self.phy.sink.ib.eq(self.tx_cdc.source.data[32:44]),
            self.phy.sink.qb.eq(self.tx_cdc.source.data[48:60]),

            # AD9364 → Host (RX path: rfic → sys, sign-extend 12→16 bit)
            self.rx_cdc.sink.valid.eq(self.phy.source.valid),
            self.rx_cdc.sink.data[ 0:16].eq(_sign_extend(self.phy.source.ia, 16)),
            self.rx_cdc.sink.data[16:32].eq(_sign_extend(self.phy.source.qa, 16)),
            self.rx_cdc.sink.data[32:48].eq(_sign_extend(self.phy.source.ib, 16)),
            self.rx_cdc.sink.data[48:64].eq(_sign_extend(self.phy.source.qb, 16)),
            self.rx_cdc.source.connect(self.source),
        ]


# ========================= USB Wishbone Bridge =========================

def usb_cmd_layout():
    return stream.EndpointDescription([
        ("opcode", 8), ("addr", 32), ("wdata", 32),
    ])


def usb_resp_layout():
    return stream.EndpointDescription([
        ("status", 32), ("rdata", 32),
    ])


class USBWishboneBridge(LiteXModule):
    """
    Bridges 12-byte command frames from EP3 OUT (via usb_cmd_cdc, already in
    sys domain) onto a Wishbone master, and returns 8-byte response frames
    to EP3 IN (via usb_resp_cdc). Same FSM shape as LitePCIeWishboneMaster,
    but with an explicit watchdog: unlike the PCIe crossbar (which always
    acks), a USB command can name any address, so a bad one must not be able
    to hang the bridge forever.

    Opcodes : 0x01 = READ32, 0x02 = WRITE32
    Status  : 0x00 = OK, 0x01 = BUS_ERROR (timeout), 0x02 = BAD_OPCODE
    """
    def __init__(self, cmd, resp, timeout_cycles=256):
        self.bus = self.wishbone = wishbone.Interface()

        timer   = Signal(max=timeout_cycles + 1)
        timeout = Signal()
        self.comb += timeout.eq(timer == timeout_cycles)

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(cmd.valid,
                NextValue(timer, 0),
                If(cmd.opcode == 0x01,
                    NextState("READ"),
                ).Elif(cmd.opcode == 0x02,
                    NextState("WRITE"),
                ).Else(
                    NextState("BAD_OPCODE"),
                ),
            ),
        )
        fsm.act("READ",
            self.bus.adr.eq(cmd.addr[2:]),
            self.bus.sel.eq(0xf),
            self.bus.we.eq(0),
            self.bus.cyc.eq(1),
            self.bus.stb.eq(1),
            NextValue(timer, timer + 1),
            If(self.bus.ack,
                NextState("RESP_OK"),
            ).Elif(timeout,
                NextState("RESP_ERR"),
            ),
        )
        fsm.act("WRITE",
            self.bus.adr.eq(cmd.addr[2:]),
            self.bus.dat_w.eq(cmd.wdata),
            self.bus.sel.eq(0xf),
            self.bus.we.eq(1),
            self.bus.cyc.eq(1),
            self.bus.stb.eq(1),
            NextValue(timer, timer + 1),
            If(self.bus.ack,
                NextState("RESP_OK"),
            ).Elif(timeout,
                NextState("RESP_ERR"),
            ),
        )
        fsm.act("RESP_OK",
            resp.valid.eq(1),
            resp.status.eq(0x00000000),
            resp.rdata.eq(self.bus.dat_r),
            If(resp.ready,
                cmd.ready.eq(1),
                NextState("IDLE"),
            ),
        )
        fsm.act("RESP_ERR",
            resp.valid.eq(1),
            resp.status.eq(0x00000001),
            resp.rdata.eq(0),
            If(resp.ready,
                cmd.ready.eq(1),
                NextState("IDLE"),
            ),
        )
        fsm.act("BAD_OPCODE",
            resp.valid.eq(1),
            resp.status.eq(0x00000002),
            resp.rdata.eq(0),
            If(resp.ready,
                cmd.ready.eq(1),
                NextState("IDLE"),
            ),
        )


# ========================= CRG =========================

class _CRG(Module):
    def __init__(self, platform, sys_clk_freq, ulpi_pads):
        self.rst = Signal()
        self.clock_domains.cd_sys    = ClockDomain()
        self.clock_domains.cd_rfic   = ClockDomain()
        self.clock_domains.cd_idelay = ClockDomain()
        self.clock_domains.cd_usb    = ClockDomain()

        # 40 MHz TCXO → PLL → cd_sys (125 MHz) + cd_idelay (200 MHz)
        clk40 = platform.request("clk40")
        self.submodules.pll = pll = S7PLL(speedgrade=-2)
        self.comb += pll.reset.eq(self.rst)
        pll.register_clkin(clk40, 40e6)
        pll.create_clkout(self.cd_sys,    sys_clk_freq)
        pll.create_clkout(self.cd_idelay, 200e6)
        self.submodules.idelayctrl = S7IDELAYCTRL(self.cd_idelay)

        # USB3320 outputs 60 MHz on ULPI_CLK (R3) → cd_usb
        self.specials += Instance("BUFG", i_I=ulpi_pads.clk, o_O=ClockSignal("usb"))
        platform.add_period_constraint(ulpi_pads.clk, 1e9 / 60e6)

        # cd_rfic is driven by AD9364 DATA_CLK via IBUFDS→BUFG in AD9364PHY


# ========================= BaseSoC =========================

class BaseSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(125e6), **kwargs):
        platform = Platform()

        kwargs["cpu_type"]             = None
        kwargs["integrated_sram_size"] = 0
        kwargs["with_uart"]            = False
        kwargs["ident_version"]        = True

        ulpi_pads = platform.request("ulpi", 0)
        self.submodules.crg = _CRG(platform, sys_clk_freq, ulpi_pads)
        SoCCore.__init__(self, platform, sys_clk_freq,
                         ident="Spectra SDR SoC", **kwargs)

        # LEDs
        self.submodules.leds = LedChaser(
            pads=platform.request_all("user_led"),
            sys_clk_freq=sys_clk_freq,
        )

        # HyperRAM 8 MB
        self.submodules.hyperram = LiteHyperBus(platform.request("hyperram"))
        self.bus.add_slave(
            name="main_ram",
            slave=self.hyperram.bus,
            region=SoCRegion(
                origin=self.mem_map.get("main_ram", 0x40000000),
                size=0x800000,
            ),
        )

        # PCIe Gen2 x2
        self.submodules.pcie_phy = S7PCIEPHY(
            platform,
            platform.request("pcie_x2"),
            data_width=64,
            bar0_size=0x20000,
        )
        self.submodules.pcie_endpoint = LitePCIeEndpoint(self.pcie_phy)

        # BAR0-to-Wishbone bridge: without this, PCIe can only reach pcie_dma0's
        # own MMIO registers (descriptor pointers, doorbells) via the crossbar
        # port LitePCIeDMA requests for itself. Nothing else on the CSR bus
        # (ad9364 SPI, hyperram, leds, xadc, dna, ...) is reachable over PCIe
        # without a generic Wishbone master bridging BAR0 into self.bus — which
        # is what this adds. jtagbone remains the only other Wishbone master.
        self.submodules.pcie_wishbone = LitePCIeWishboneMaster(self.pcie_endpoint)
        self.bus.add_master(name="pcie_wishbone", master=self.pcie_wishbone.wishbone)

        self.submodules.pcie_dma0 = LitePCIeDMA(
            self.pcie_phy,
            self.pcie_endpoint,
            with_buffering=True,
            buffering_depth=8192,
            with_loopback=False,
        )
        # CSRs on pcie_dma0 are auto-discovered from the submodule hierarchy by
        # LiteX's CSRBankArray during finalize() — no explicit add_csr() needed
        # (SoCCore.add_csr(name) was removed from this LiteX version).

        # AD9364 RFIC
        self.submodules.ad9364 = AD9364Core(
            platform.request("ad9364_rfic"),
            platform.request("ad9364_spi"),
        )
        # Same as above: CSRs on ad9364 are auto-discovered, no add_csr() needed.

        # USB IQ Device (USB3320 ULPI → LUNA-generated Verilog)
        # CDC FIFOs bridge sys (125 MHz) ↔ usb (60 MHz) domains.
        # PCIe/USB selection for the AD9364 IQ stream is wired below, driven by
        # live PCIe link status — see "IQ stream mux" after this instance.
        platform.add_source(os.path.join(os.path.dirname(__file__), "usb_iq_device.v"))

        usb_rx_cdc = stream.ClockDomainCrossing(dma_layout(64), cd_from="sys", cd_to="usb")
        usb_tx_cdc = stream.ClockDomainCrossing(dma_layout(64), cd_from="usb", cd_to="sys")
        self.submodules += usb_rx_cdc, usb_tx_cdc

        # EP3 command/response CDC — usb_cmd_cdc.source and usb_resp_cdc.sink
        # are both in sys domain, feeding/fed by USBWishboneBridge below.
        usb_cmd_cdc  = stream.ClockDomainCrossing(usb_cmd_layout(),  cd_from="usb", cd_to="sys")
        usb_resp_cdc = stream.ClockDomainCrossing(usb_resp_layout(), cd_from="sys", cd_to="usb")
        self.submodules += usb_cmd_cdc, usb_resp_cdc

        self.specials += Instance("usb_iq_device",
            # Clock/reset (Amaranth uses usb_clk/usb_rst domain ports)
            i_usb_clk = ClockSignal("usb"),
            i_usb_rst = ResetSignal("usb"),
            i_clk     = ClockSignal("usb"),
            i_rst     = ResetSignal("usb"),

            # ULPI bus
            i_ulpi_clk    = ClockSignal("usb"),
            i_ulpi_data_i = ulpi_pads.data,
            o_ulpi_data_o = ulpi_pads.data,     # tristate handled below
            o_ulpi_data_oe= Signal(name="ulpi_data_oe"),
            i_ulpi_dir    = ulpi_pads.dir,
            i_ulpi_nxt    = ulpi_pads.nxt,
            o_ulpi_stp    = ulpi_pads.stp,
            o_ulpi_rst    = ulpi_pads.rst,

            # IQ RX: AD9364 RX → sys→usb CDC → USB EP1 IN → PC
            i_rx_data     = usb_rx_cdc.source.data,
            i_rx_valid    = usb_rx_cdc.source.valid,
            o_rx_ready    = usb_rx_cdc.source.ready,

            # IQ TX: PC → USB EP2 OUT → usb→sys CDC → AD9364 TX
            o_tx_data     = usb_tx_cdc.sink.data,
            o_tx_valid    = usb_tx_cdc.sink.valid,
            i_tx_ready    = usb_tx_cdc.sink.ready,

            # Register command: PC → USB EP3 OUT → usb→sys CDC → USBWishboneBridge
            o_cmd_opcode  = usb_cmd_cdc.sink.opcode,
            o_cmd_addr    = usb_cmd_cdc.sink.addr,
            o_cmd_wdata   = usb_cmd_cdc.sink.wdata,
            o_cmd_valid   = usb_cmd_cdc.sink.valid,
            i_cmd_ready   = usb_cmd_cdc.sink.ready,

            # Register response: USBWishboneBridge → sys→usb CDC → USB EP3 IN → PC
            i_resp_status = usb_resp_cdc.source.status,
            i_resp_rdata  = usb_resp_cdc.source.rdata,
            i_resp_valid  = usb_resp_cdc.source.valid,
            o_resp_ready  = usb_resp_cdc.source.ready,

            # Status
            o_usb_connected = Signal(name="usb_connected"),
        )

        # USB control-plane bridge: EP3 command/response → Wishbone master,
        # giving USB the same CSR/Wishbone access PCIe already has via
        # pcie_wishbone (AD9364 SPI, HyperRAM, XADC, DNA, all other CSRs).
        # IQ endpoints (EP1/EP2) and the PCIe path/IQ mux are untouched.
        self.submodules.usb_wishbone = USBWishboneBridge(usb_cmd_cdc.source, usb_resp_cdc.sink)
        self.bus.add_master(name="usb_wishbone", master=self.usb_wishbone.wishbone)

        # IQ stream mux: PCIe drives AD9364 whenever the PCIe link is trained and
        # up; otherwise USB does. Driven by the PHY's own link-up status (already
        # resynchronized into sys by S7PCIEPHY.add_resync()), not a CSR — so this
        # works with zero host-software cooperation even when only USB is wired
        # (e.g. a carrier board with no PCIe lanes connected, where the link never
        # trains and pcie_link_up simply never asserts). When both are wired,
        # PCIe wins as soon as it trains, matching "prefer PCIe when available".
        pcie_link_up = self.pcie_phy._link_status.fields.status
        self.comb += [
            If(pcie_link_up,
                self.pcie_dma0.source.connect(self.ad9364.sink),   # PCIe → RFIC (TX)
                self.ad9364.source.connect(self.pcie_dma0.sink),   # RFIC → PCIe (RX)
                usb_tx_cdc.source.ready.eq(0),                     # hold USB TX off
            ).Else(
                usb_tx_cdc.source.connect(self.ad9364.sink),       # USB → RFIC (TX)
                self.ad9364.source.connect(usb_rx_cdc.sink),       # RFIC → USB (RX)
                self.pcie_dma0.source.ready.eq(0),                 # hold PCIe reader off
            ),
        ]

        # JTAGBone: JTAG → Wishbone bridge for CSR access without PCIe
        self.add_jtagbone()

        # On-chip utilities
        self.icap = ICAP()
        self.icap.add_reload()
        self.xadc = XADC()
        self.dna  = DNA()
        self.icap.add_timing_constraints(platform, sys_clk_freq, self.crg.cd_sys.clk)
        self.dna.add_timing_constraints(platform, sys_clk_freq, self.crg.cd_sys.clk)

        # MSI: wire DMA IRQs → LitePCIeMSI → pcie_phy.msi
        self.submodules.pcie_msi = LitePCIeMSI()
        self.comb += self.pcie_msi.source.connect(self.pcie_phy.msi)
        self.interrupts = {
            "PCIE_DMA0_WRITER": self.pcie_dma0.writer.irq,
            "PCIE_DMA0_READER": self.pcie_dma0.reader.irq,
        }
        for i, (name, irq) in enumerate(sorted(self.interrupts.items())):
            self.comb += self.pcie_msi.irqs[i].eq(irq)
            self.add_constant(name + "_INTERRUPT", i)


# ========================= Main =========================

def main():
    parser = argparse.ArgumentParser(description="Spectra SDR v2 LiteX SoC")
    parser.add_argument("--build", action="store_true", help="Build bitstream")
    parser.add_argument("--load",  action="store_true", help="Load bitstream")
    builder_args(parser)
    soc_core_args(parser)
    args = parser.parse_args()

    soc = BaseSoC(sys_clk_freq=int(125e6), **soc_core_argdict(args))
    builder = Builder(soc, **builder_argdict(args))
    builder.build(run=args.build)

    generate_litepcie_software(soc, os.path.join(builder.output_dir, "software"))

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(
            os.path.join(builder.gateware_dir, soc.build_name + ".bit"))


if __name__ == "__main__":
    main()
