#!/usr/bin/env python3
"""
Spectra SDR — USB IQ Device (LUNA/Amaranth)

USB 2.0 HS bulk device using USB3320 ULPI PHY.
  EP1 IN  (FPGA → PC) : IQ RX stream, 512-byte packets
  EP2 OUT (PC → FPGA) : IQ TX stream, 512-byte packets

Run this script directly to generate usb_iq_device.v:
    python3 usb_iq_device.py

The generated Verilog is instantiated by spectra_target_v2.py via
platform.add_source() + Migen Instance().
"""

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

from amaranth.hdl           import *
from amaranth.back          import verilog

from usb_protocol.emitters              import DeviceDescriptorCollection
from luna.gateware.interface.ulpi       import UTMITranslator
from luna.gateware.usb.usb2.device      import USBDevice
from luna.gateware.usb.usb2.endpoints.stream import (
    USBMultibyteStreamInEndpoint,
    USBStreamOutEndpoint,
)

# luna 0.2.3 ULPIInterface has flat signals for rst/nxt/stp but UTMITranslator
# accesses them as sub-records (.o/.i). Patch the layout to match what the
# translator actually expects.
from amaranth.hdl.rec import Record, DIR_FANIN, DIR_FANOUT

class ULPIInterface(Record):
    LAYOUT = [
        ('data', [('i', 8, DIR_FANIN), ('o', 8, DIR_FANOUT), ('oe', 1, DIR_FANOUT)]),
        ('clk',  [('o', 1, DIR_FANOUT)]),
        ('nxt',  [('i', 1, DIR_FANIN)]),
        ('stp',  [('o', 1, DIR_FANOUT)]),
        ('dir',  [('i', 1, DIR_FANIN)]),
        ('rst',  [('o', 1, DIR_FANOUT)]),
    ]
    def __init__(self):
        super().__init__(self.LAYOUT)

# USB VID/PID
USB_VID = 0x1209   # pid.codes testing VID (replace with your own for production)
USB_PID = 0x5380

MAX_PACKET_SIZE  = 512   # HS bulk max packet size (IQ endpoints)
CTRL_PACKET_SIZE = 64    # command/response endpoints — 12B cmd / 8B resp frames

# ---------------------------------------------------------------------------
# USB descriptor builder
# ---------------------------------------------------------------------------

def _make_descriptors():
    d = DeviceDescriptorCollection()

    with d.DeviceDescriptor() as dev:
        dev.idVendor           = USB_VID
        dev.idProduct          = USB_PID
        dev.iManufacturer      = "Hallycon Ventures"
        dev.iProduct           = "Spectra SDR"
        dev.iSerialNumber      = "00000001"
        dev.bNumConfigurations = 1

    with d.ConfigurationDescriptor() as cfg:
        cfg.bmAttributes = 0x80   # bus-powered
        cfg.bMaxPower    = 250    # 500 mA

        with cfg.InterfaceDescriptor() as iface:
            iface.bInterfaceNumber   = 0
            iface.bInterfaceClass    = 0xFF  # vendor-specific
            iface.bInterfaceSubclass = 0x00
            iface.bInterfaceProtocol = 0x00

            # EP1 IN — IQ RX (FPGA → PC)
            with iface.EndpointDescriptor() as ep:
                ep.bEndpointAddress = 0x81   # EP1 IN
                ep.wMaxPacketSize   = MAX_PACKET_SIZE

            # EP2 OUT — IQ TX (PC → FPGA)
            with iface.EndpointDescriptor() as ep:
                ep.bEndpointAddress = 0x02   # EP2 OUT
                ep.wMaxPacketSize   = MAX_PACKET_SIZE

            # EP3 IN — register-access response (FPGA → PC)
            with iface.EndpointDescriptor() as ep:
                ep.bEndpointAddress = 0x83   # EP3 IN
                ep.wMaxPacketSize   = CTRL_PACKET_SIZE

            # EP3 OUT — register-access command (PC → FPGA)
            with iface.EndpointDescriptor() as ep:
                ep.bEndpointAddress = 0x03   # EP3 OUT
                ep.wMaxPacketSize   = CTRL_PACKET_SIZE

    return d


# ---------------------------------------------------------------------------
# Top-level module with flat ports for clean Verilog generation
# ---------------------------------------------------------------------------

class USBIQTop(Elaboratable):
    """
    USB HS bulk IQ streaming device.

    All logic runs in the 60 MHz usb clock domain (ulpi_clk from USB3320).

    Ports:
      ulpi_*         : ULPI bus to USB3320 PHY
      rx_data[63:0]  : IQ RX data from AD9364 sys→usb CDC FIFO output
      rx_valid       : rx_data valid
      rx_ready       : back-pressure to CDC FIFO (output)
      tx_data[63:0]  : IQ TX data to AD9364 usb→sys CDC FIFO input
      tx_valid       : tx_data valid (output)
      tx_ready       : back-pressure from CDC FIFO (input)
      usb_connected  : high when USB link is active (output)
    """

    def __init__(self):
        # ULPI bus
        self.ulpi_clk     = Signal()        # 60 MHz from USB3320
        self.ulpi_data_i  = Signal(8)       # data from PHY (when dir=1)
        self.ulpi_data_o  = Signal(8)       # data to PHY   (when dir=0)
        self.ulpi_data_oe = Signal()        # output-enable for data bus
        self.ulpi_dir     = Signal()        # PHY drives bus when high
        self.ulpi_nxt     = Signal()        # PHY throttle
        self.ulpi_stp     = Signal()        # stop (output)
        self.ulpi_rst     = Signal()        # PHY reset, active high (output)

        # IQ RX stream: AD9364 → EP1 IN → PC (64-bit = 4× 16-bit I/Q samples)
        self.rx_data  = Signal(64)
        self.rx_valid = Signal()
        self.rx_ready = Signal()            # output

        # IQ TX stream: PC → EP2 OUT → AD9364 (64-bit)
        self.tx_data  = Signal(64)
        self.tx_valid = Signal()            # output
        self.tx_ready = Signal()

        # Register-access command: PC → EP3 OUT → USBWishboneBridge (12-byte frame)
        #   byte 0      : opcode (0x01=READ32, 0x02=WRITE32)
        #   bytes 1-3   : reserved
        #   bytes 4-7   : address (32-bit LE)
        #   bytes 8-11  : write data (32-bit LE, ignored on READ32)
        self.cmd_opcode = Signal(8)         # output
        self.cmd_addr   = Signal(32)        # output
        self.cmd_wdata  = Signal(32)        # output
        self.cmd_valid  = Signal()          # output
        self.cmd_ready  = Signal()          # input — backpressure from USBWishboneBridge

        # Register-access response: USBWishboneBridge → EP3 IN → PC (8-byte frame)
        #   bytes 0-3 : status (0x00=OK, 0x01=BUS_ERROR, 0x02=BAD_OPCODE)
        #   bytes 4-7 : read data (32-bit LE)
        self.resp_status = Signal(32)       # input
        self.resp_rdata  = Signal(32)       # input
        self.resp_valid  = Signal()         # input
        self.resp_ready  = Signal()         # output

        # Status output
        self.usb_connected = Signal()

    def elaborate(self, platform):
        m = Module()

        # ------------------------------------------------------------------
        # Wire flat ports → ULPIInterface record
        # ------------------------------------------------------------------
        ulpi = ULPIInterface()
        m.d.comb += [
            ulpi.data.i  .eq(self.ulpi_data_i),
            self.ulpi_data_o .eq(ulpi.data.o),
            self.ulpi_data_oe.eq(ulpi.data.oe),
            ulpi.dir.i   .eq(self.ulpi_dir),
            ulpi.nxt.i   .eq(self.ulpi_nxt),
            self.ulpi_stp.eq(ulpi.stp.o),
            self.ulpi_rst.eq(ulpi.rst.o),
        ]

        # ------------------------------------------------------------------
        # ULPI → UTMI translator (all low-level ULPI signalling handled here)
        # ------------------------------------------------------------------
        m.submodules.translator = translator = UTMITranslator(
            ulpi=ulpi, handle_clocking=False)

        # ------------------------------------------------------------------
        # USB 2.0 HS device core
        # ------------------------------------------------------------------
        m.submodules.usb = usb = USBDevice(bus=translator, handle_clocking=False)
        usb.add_standard_control_endpoint(_make_descriptors())

        # ------------------------------------------------------------------
        # EP1 IN — IQ RX (FPGA → PC), 8-byte wide = 64 bits per USB transfer
        # ------------------------------------------------------------------
        ep_rx_in = USBMultibyteStreamInEndpoint(
            byte_width      = 8,
            endpoint_number = 1,
            max_packet_size = MAX_PACKET_SIZE,
        )
        usb.add_endpoint(ep_rx_in)

        m.d.comb += [
            ep_rx_in.stream.payload.eq(self.rx_data),
            ep_rx_in.stream.valid  .eq(self.rx_valid),
            self.rx_ready          .eq(ep_rx_in.stream.ready),
        ]

        # ------------------------------------------------------------------
        # EP2 OUT — IQ TX (PC → FPGA)
        # USBStreamOutEndpoint is 1-byte wide; we assemble 8 bytes into 64 bits
        # ------------------------------------------------------------------
        ep_tx_out = USBStreamOutEndpoint(
            endpoint_number = 2,
            max_packet_size = MAX_PACKET_SIZE,
            buffer_size     = MAX_PACKET_SIZE * 2,
        )
        usb.add_endpoint(ep_tx_out)

        # Assemble 8 consecutive bytes into one 64-bit word
        tx_shift  = Signal(64)
        tx_count  = Signal(3)   # counts 0..7
        tx_data_v = Signal()

        with m.If(ep_tx_out.stream.valid):
            m.d.sync += [
                tx_shift.word_select(tx_count, 8).eq(ep_tx_out.stream.payload),
                tx_count.eq(tx_count + 1),
            ]
            with m.If(tx_count == 7):
                m.d.sync += [
                    self.tx_data .eq(tx_shift),
                    tx_data_v    .eq(1),
                ]
            with m.Else():
                m.d.sync += tx_data_v.eq(0)

        m.d.comb += [
            ep_tx_out.stream.ready.eq(self.tx_ready | ~tx_data_v),
            self.tx_valid         .eq(tx_data_v),
        ]

        # ------------------------------------------------------------------
        # EP3 OUT — register-access command (PC → FPGA), 12-byte frame
        # USBStreamOutEndpoint is 1-byte wide; assemble 12 bytes into cmd_shift.
        # ------------------------------------------------------------------
        ep_cmd_out = USBStreamOutEndpoint(
            endpoint_number = 3,
            max_packet_size = CTRL_PACKET_SIZE,
            buffer_size     = CTRL_PACKET_SIZE,
        )
        usb.add_endpoint(ep_cmd_out)

        cmd_shift   = Signal(96)   # byte0=opcode, bytes1-3=reserved, bytes4-7=addr, bytes8-11=wdata
        cmd_count   = Signal(4)    # counts 0..11
        cmd_valid_r = Signal()

        with m.If(ep_cmd_out.stream.valid):
            m.d.sync += cmd_shift.word_select(cmd_count, 8).eq(ep_cmd_out.stream.payload)
            with m.If(cmd_count == 11):
                m.d.sync += [
                    cmd_count  .eq(0),
                    cmd_valid_r.eq(1),
                ]
            with m.Else():
                m.d.sync += [
                    cmd_count  .eq(cmd_count + 1),
                    cmd_valid_r.eq(0),
                ]
        with m.Elif(self.cmd_ready):
            m.d.sync += cmd_valid_r.eq(0)

        m.d.comb += [
            ep_cmd_out.stream.ready.eq(self.cmd_ready | ~cmd_valid_r),
            self.cmd_valid          .eq(cmd_valid_r),
            self.cmd_opcode         .eq(cmd_shift[0:8]),
            self.cmd_addr           .eq(cmd_shift[32:64]),
            self.cmd_wdata          .eq(cmd_shift[64:96]),
        ]

        # ------------------------------------------------------------------
        # EP3 IN — register-access response (FPGA → PC), 8-byte frame
        # ------------------------------------------------------------------
        ep_resp_in = USBMultibyteStreamInEndpoint(
            byte_width      = 8,
            endpoint_number = 3,
            max_packet_size = CTRL_PACKET_SIZE,
        )
        usb.add_endpoint(ep_resp_in)

        m.d.comb += [
            ep_resp_in.stream.payload.eq(Cat(self.resp_status, self.resp_rdata)),
            ep_resp_in.stream.valid  .eq(self.resp_valid),
            self.resp_ready          .eq(ep_resp_in.stream.ready),
        ]

        # ------------------------------------------------------------------
        # Connect device + status
        # ------------------------------------------------------------------
        m.d.comb += [
            usb.connect         .eq(1),
            usb.full_speed_only .eq(0),   # HS preferred
            self.usb_connected  .eq(~translator.suspend),
        ]

        return m


# ---------------------------------------------------------------------------
# Verilog generation
# ---------------------------------------------------------------------------

def generate_verilog(output_path="usb_iq_device.v"):
    top = USBIQTop()

    ports = [
        top.ulpi_clk, top.ulpi_data_i, top.ulpi_data_o, top.ulpi_data_oe,
        top.ulpi_dir, top.ulpi_nxt, top.ulpi_stp, top.ulpi_rst,
        top.rx_data, top.rx_valid, top.rx_ready,
        top.tx_data, top.tx_valid, top.tx_ready,
        top.cmd_opcode, top.cmd_addr, top.cmd_wdata, top.cmd_valid, top.cmd_ready,
        top.resp_status, top.resp_rdata, top.resp_valid, top.resp_ready,
        top.usb_connected,
    ]

    output = verilog.convert(top, ports=ports, name="usb_iq_device",
                             emit_src=False)
    with open(output_path, "w") as f:
        f.write(output)
    print(f"Generated: {output_path}  ({len(output)} bytes)")


if __name__ == "__main__":
    generate_verilog()
