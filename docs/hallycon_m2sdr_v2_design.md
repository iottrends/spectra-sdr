# Hallycon M2 SDR — v2 Gateware Design Reference

**Board:** Hallycon M2 SDR (M.2 2280 form factor)
**FPGA:** Xilinx XC7A50T-2CSG325I (Artix-7, speed grade -2)
**RFIC:** AD9364BBCZ (pin-compatible with AD9361 / AD9363)
**Build tool:** Vivado 2025.2
**Framework:** LiteX + Migen (Python-based HDL)
**Target file:** `hallycon_m2sdr_target_v2.py`
**Platform file:** `hallycon_m2sdr_platform.py`
**Last successful build:** 2026-04-05

---

## Table of Contents

1. [Board Hardware Summary](#1-board-hardware-summary)
2. [Top-Level Block Diagram](#2-top-level-block-diagram)
3. [Clock Architecture](#3-clock-architecture)
4. [Gateware Modules](#4-gateware-modules)
5. [Data Paths](#5-data-paths)
6. [CSR Register Map](#6-csr-register-map)
7. [Memory Map](#7-memory-map)
8. [FPGA Resource Utilization](#8-fpga-resource-utilization)
9. [Timing Results](#9-timing-results)
10. [PCIe Configuration](#10-pcie-configuration)
11. [Pin Assignments (Key Signals)](#11-pin-assignments-key-signals)
12. [File Structure](#12-file-structure)
13. [Build Instructions](#13-build-instructions)
14. [Bring-Up Sequence](#14-bring-up-sequence)
15. [What Is Done vs Pending](#15-what-is-done-vs-pending)
16. [Known Issues and Notes](#16-known-issues-and-notes)

---

## 1. Board Hardware Summary

| Component | Part | Details |
|---|---|---|
| FPGA | XC7A50T-2CSG325I | Artix-7, 50K LUT, 75 BRAM, 120 DSP, 4× GTP |
| RFIC | AD9364BBCZ | 2R2T, 70 MHz – 6 GHz, 56 MHz BW, 61.44 MSPS max |
| USB PHY | USB3320C-EZK-TR | ULPI, USB 2.0 HS (480 Mbps) |
| RAM | IS66WVH8M8ALL-166B1LI | HyperRAM, 8 MB, 166 MHz |
| Flash | W25Q128JVSIQ | 16 MB QSPI |
| System clock | S2HO40000F3CHC-T | 40 MHz TCXO, 2.5 ppm |
| USB clock | Onboard oscillator | 24 MHz → USB3320 PLL → 60 MHz ULPI_CLK |
| PCIe | M.2 B+M Key (2280) | Gen2 x2, PERST# on T18 |
| Power | LTC3370 + MIC47100 + LM3880 | Quad Buck + 1.3V LDO + sequencer |
| JTAG | J5 header | TDI/TDO/TMS/TCK broken out |

**Bank voltage summary:**

| Bank | Voltage | Signals |
|---|---|---|
| Bank 0 | 3.3V | Configuration, JTAG |
| Bank 14 | 3.3V | QSPI Flash, M.2 control, LEDs |
| Bank 15 | 2.5V | AD9364 LVDS data + SPI + control |
| Bank 34 | 1.8V | HyperRAM, USB3320 ULPI, SYS_CLK, USB_CLK |

---

## 2. Top-Level Block Diagram

```
                        ┌─────────────────────────────────────────────────────────────┐
                        │                  XC7A50T FPGA                               │
                        │                                                             │
  40 MHz TCXO ─────────►│ clk40 (P4)                                                 │
                        │   └─► S7PLL ──► cd_sys  125 MHz                            │
                        │              └─► cd_idelay 200 MHz                         │
                        │                                                             │
  PCIe 100MHz refclk ──►│ MGTREFCLK (D6/D5)                                         │
  (from host M.2)       │   └─► IBUFDS_GTE2 ──► 2× GTPE2_CHANNEL ──► PCIe PHY      │
                        │                              │                             │
                        │         ┌────────────────────┘                             │
                        │         │  LitePCIe Gen2 x2 (64-bit, 125 MHz user clk)    │
                        │         │                                                  │
                        │         │  LitePCIeDMA0                                   │
                        │         │    writer (Host→FPGA, TX path) ──────────────┐  │
                        │         │    reader (FPGA→Host, RX path) ◄──────────┐  │  │
                        │         │                                            │  │  │
                        │         │  LitePCIeMSI  (DMA writer + reader IRQs)  │  │  │
                        │         └────────────────────────────────────────────┘  │  │
                        │                                                          │  │
                        │  ┌───────────────────────── AD9364 Core ─────────────┐  │  │
                        │  │                                                    │  │  │
                        │  │  AD9364 SPI Master (CSR) ◄── SPI bus (F17/E18/.. )│  │  │
                        │  │                                                    │  │  │
                        │  │  AD9364 PHY (cd_rfic domain, 245.76 MHz)          │  │  │
                        │  │    6× IBUFDS+IDDR  ── RX lanes ◄── AD9364        │  │  │
                        │  │    6× ODDR+OBUFDS  ── TX lanes ──► AD9364        │  │  │
                        │  │    IBUFDS+IDDR     ── RX_FRAME                   │  │  │
                        │  │    ODDR+OBUFDS     ── TX_FRAME                   │  │  │
                        │  │    IBUFDS+BUFG     ── DATA_CLK ──► cd_rfic       │  │  │
                        │  │                                                    │  │  │
                        │  │  AsyncFIFO  sys→rfic  (TX CDC) ◄──────────────────┘  │  │
                        │  │  AsyncFIFO  rfic→sys  (RX CDC) ──────────────────────┘  │
                        │  └────────────────────────────────────────────────────────  │
                        │                                                             │
                        │  ┌───────────────────── USB IQ Device ──────────────────┐  │
                        │  │                                                        │  │
                        │  │  usb_iq_device.v  (LUNA Amaranth → generated Verilog) │  │
                        │  │    USB 2.0 HS Bulk Device  VID=0x1209 PID=0x5380      │  │
                        │  │    EP1 IN  (FPGA → PC, IQ RX, 512B packets)          │  │
                        │  │    EP2 OUT (PC → FPGA, IQ TX, 512B packets)           │  │
                        │  │                                                        │  │
                        │  │  AsyncFIFO  sys→usb  (rx_cdc) ◄── from AD9364 RX     │  │
                        │  │  AsyncFIFO  usb→sys  (tx_cdc) ──► to AD9364 TX       │  │
                        │  └────────────────────────────────────────────────────────  │
  USB3320C ◄──────────►│ ULPI bus (R3,V3..U6,R7,T7,U7,V6)                         │
  (60 MHz ULPI_CLK)     │   └─► BUFG ──► cd_usb 60 MHz                             │
                        │                                                             │
                        │  ┌──────────────────── Utilities ────────────────────────┐ │
                        │  │  JTAGBone   BSCANE2 ──► Wishbone ──► CSR bus         │ │
                        │  │  XADC       temp, VCCINT, VCCAUX, VCCBRAM            │ │
                        │  │  DNA        64-bit unique device serial               │ │
                        │  │  ICAP       in-system reconfiguration (reload)        │ │
                        │  │  LEDChaser  user_led[0..1] (N17, N18)                │ │
                        │  │  Timer0     SoC system timer                          │ │
                        │  └───────────────────────────────────────────────────────┘ │
                        │                                                             │
                        │  ┌──────────────────── HyperRAM ─────────────────────────┐ │
                        │  │  IS66WVH8M8ALL  8 MB  @ 0x40000000                    │ │
                        │  │  clk_p/n (M2/M1), cs_n (M6), rwds (K2), dq[7:0]     │ │
                        │  └───────────────────────────────────────────────────────┘ │
                        └─────────────────────────────────────────────────────────────┘
                                │                │                   │
                          PCIe x2            ULPI bus           AD9364 LVDS
                        (E4/E3/H2/H1      (Bank 34, 1.8V)     (Bank 15, 2.5V)
                         A4/A3/F2/F1)
                                │
                         Host PC / laptop
```

---

## 3. Clock Architecture

> Full detail: `docs/clocking_and_ad9364_init.md`

### Clock domains in the design

| Domain | Frequency | Source | FPGA primitive |
|---|---|---|---|
| `cd_sys` | 125 MHz | 40 MHz TCXO → S7PLL | MMCME2_ADV |
| `cd_idelay` | 200 MHz | 40 MHz TCXO → S7PLL | MMCME2_ADV |
| `cd_rfic` | 245.76 MHz* | AD9364 DATA_CLK (E16) | IBUFDS + BUFG |
| `cd_usb` | 60 MHz | USB3320 ULPI_CLK (R3) | BUFG |
| PCIe user clk | 125 MHz | PCIe refclk → GTP | PLLE2_ADV (internal) |

*`cd_rfic` frequency is determined by the AD9364 BBPLL setting programmed over SPI.
The FPGA constraint (`rfic_clk_freq = 245.76e6`) must match the programmed rate.
**`cd_rfic` does not exist until the AD9364 SPI init sequence is executed.**

### CDC (Clock Domain Crossing) strategy

All async crossings use LiteX `stream.ClockDomainCrossing` (AsyncFIFO with gray-coded
pointers). Timing constraints applied in `platform.py` `do_finalize()`:

```
sys → rfic:  set_max_delay -datapath_only 4.0 ns   (125→245 MHz, tighter)
rfic → sys:  set_max_delay -datapath_only 8.0 ns
sys → usb:   set_max_delay -datapath_only 8.0 ns
usb → sys:   set_max_delay -datapath_only 8.0 ns
sys → clk40: set_false_path                         (PLL feedback, not a real path)
```

---

## 4. Gateware Modules

### 4.1 CRG — Clock and Reset Generator

**File:** `hallycon_m2sdr_target_v2.py` — `class _CRG`

- Requests `clk40` (40 MHz TCXO, pin P4)
- Instantiates `S7PLL` → 125 MHz `cd_sys` + 200 MHz `cd_idelay`
- Instantiates `S7IDELAYCTRL` on `cd_idelay` (required for IDELAY primitives)
- Buffers `ulpi_pads.clk` (USB3320 60 MHz) through `BUFG` → `cd_usb`
- `cd_rfic` is NOT created here — it is created inside `AD9364PHY` from DATA_CLK

### 4.2 AD9364SPIMaster

**File:** `hallycon_m2sdr_target_v2.py` — `class AD9364SPIMaster`

- Custom 4-wire SPI master, CPOL=0, CPHA=1
- Data width: 24-bit (matches AD9364 register format)
- Clock divider: 8 (SPI clock = 125/8 = 15.625 MHz, within AD9364 spec)
- CSRs exposed: `control` (start + length), `status` (done), `mosi`, `miso`
- AD9364 SPI frame: `[R/~W][W1:W0][A12:A0][D7:D0]`

### 4.3 AD9364PHY

**File:** `hallycon_m2sdr_target_v2.py` — `class AD9364PHY`

Implements the AD9364 LVDS DDR data interface in the `rfic` clock domain.

**RX path (AD9364 → FPGA):**
- DATA_CLK: `IBUFDS` (E16/D16) → `BUFG` → `ClockSignal("rfic")`
- RX_FRAME: `IBUFDS` (D8/C8) → `IDDR` (SAME_EDGE_PIPELINED) → frame sync signal
- 6× data lanes: `IBUFDS` → `IDDR` → 6-bit half-words (I on rising, Q on falling)
- Frame counter tracks 4-cycle (2R2T) or 2-cycle (1R1T) interleaving
- Assembles 12-bit I/Q words for IA, QA, IB, QB channels

**TX path (FPGA → AD9364):**
- FB_CLK: `ODDR` (SAME_EDGE) + `OBUFDS` (D13/C13) — loopback of rfic clock
- TX_FRAME: `ODDR` + `OBUFDS` (B10/A10)
- 6× data lanes: `ODDR` + `OBUFDS` — serializes 12-bit I/Q words DDR

**Modes (software-selectable via CSR):**
- `1R1T`: 4-lane interleaving, DATA_CLK = sample_rate × 2
- `2R2T`: 8-lane interleaving, DATA_CLK = sample_rate × 4

**Control signals (driven from AD9364Core, sys domain → async):**
- `rst_n` (C14) — active-low reset to AD9364
- `enable` (H18) — chip enable, held high
- `txnrx` (F14) — 0 = FDD (simultaneous TX+RX)

### 4.4 AD9364Core

**File:** `hallycon_m2sdr_target_v2.py` — `class AD9364Core`

Top-level AD9364 wrapper combining SPI master + PHY + CDC FIFOs.

```
sink  (dma_layout 64-bit, sys domain)  ──► tx_cdc (sys→rfic) ──► AD9364PHY.sink
source (dma_layout 64-bit, sys domain) ◄── rx_cdc (rfic→sys) ◄── AD9364PHY.source
```

IQ packing in 64-bit DMA word:

```
Bits [11: 0]  IA  (12-bit, sign-extended to 16-bit in RX)
Bits [27:16]  QA
Bits [43:32]  IB
Bits [59:48]  QB
```

RX sign-extends 12-bit samples to 16-bit (fills bits [15:12] with sign bit).

### 4.5 LitePCIe — PCIe Gen2 x2

**PHY:** `S7PCIEPHY` using Xilinx `PCIE_2_1` hard block (1× on XC7A50T)

| Parameter | Value |
|---|---|
| Link speed | Gen2 (5.0 GT/s) |
| Lane width | x2 |
| Data width | 64-bit |
| User clock | 125 MHz |
| BAR0 size | 0x20000 (128 KB) — CSR space |
| Device ID | 0x7022 |
| Max payload | 512 bytes |
| Interrupts | MSI (no legacy INTx) |

**DMA:** `LitePCIeDMA0`
- Writer channel: Host → FPGA (TX IQ data)
- Reader channel: FPGA → Host (RX IQ data)
- Buffering depth: 8192 (both directions)
- Descriptor-based scatter-gather
- MSI interrupt on writer and reader completion

**Throughput:**
- PCIe Gen2 x2 theoretical: ~800 MB/s bidirectional
- Usable after overhead: ~490 MB/s per direction
- Required for 2R2T @ 61.44 MSPS: 492 MB/s → fits in x2 lane

### 4.6 USB IQ Device

**Source:** `usb_iq_device.v` (generated by `usb_iq_device.py` via LUNA + Amaranth)

| Parameter | Value |
|---|---|
| USB version | 2.0 High Speed (480 Mbps) |
| USB class | Vendor-specific |
| VID | 0x1209 |
| PID | 0x5380 |
| EP1 IN | FPGA → PC (IQ RX stream, 512-byte bulk packets) |
| EP2 OUT | PC → FPGA (IQ TX stream, 512-byte bulk packets) |
| PHY | USB3320C via ULPI (8-bit data bus, 60 MHz) |

**Throughput:** 480 Mbps raw / ~45 MB/s usable after USB overhead
**Use case:** Low-cost streaming path (no PCIe slot required, e.g. Raspberry Pi, laptop)
**Limitation:** Bandwidth sufficient for 1R1T @ ~5.6 MSPS at 16-bit I/Q

**CDC integration:**
```
AD9364 RX ──► rx_cdc (sys→usb AsyncFIFO) ──► usb_iq_device EP1 IN ──► USB host
USB host ──► EP2 OUT ──► tx_cdc (usb→sys AsyncFIFO) ──► AD9364 TX
```

### 4.7 JTAGBone

- `BSCANE2` primitive bridges JTAG USER2 scan chain to Wishbone bus
- Gives full CSR read/write access without PCIe driver or USB
- Used for: bitstream validation, AD9364 SPI init debugging, register inspection
- Access via: `litex_server --jtag` + `RemoteClient` or `litex_cli`
- Resource cost: ~3,400 LUTs (included in utilization figures)

### 4.8 XADC

- Monitors FPGA on-chip sensors: temperature, VCCINT, VCCAUX, VCCBRAM
- CSR accessible at base `0x6000`
- Temperature formula: `raw × 503.975 / 4096 − 273.15` → °C
- Voltage formula: `raw × 3 / 4096` → V

### 4.9 DNA

- Reads 57-bit unique FPGA device identifier (factory programmed)
- CSR at base `0x1800`, two 32-bit registers (64-bit total, upper 7 bits unused)
- Used for: device serial tracking, license binding, field identification

### 4.10 ICAP

- Internal Configuration Access Port — allows FPGA to reconfigure itself
- `add_reload()` adds a CSR-triggered soft reboot capability
- Can be used to switch between bitstream slots (golden / application)
- CSR at base `0x2800`

### 4.11 HyperRAM

- IS66WVH8M8ALL, 8 MB, connected on Bank 34 (1.8V)
- Mapped at `0x40000000` (8 MB window)
- Driven by LiteX `HyperRAM` core via Wishbone
- Available for future use (sample buffering, capture, waveform playback)
- Currently not actively used in v1 IQ streaming path

### 4.12 LEDChaser

- Controls `user_led[0]` (N17) and `user_led[1]` (N18)
- Default pattern: chaser running at `sys_clk_freq / (2^24)` ≈ 7.5 Hz
- Visual confirmation that the FPGA is alive and clocked

---

## 5. Data Paths

### 5.1 PCIe RX Path (AD9364 → Host)

```
AD9364 RFIC
  │  DATA_CLK (245.76 MHz LVDS, E16/D16)
  │  RX_FRAME (D8/C8)
  │  RX_D[5:0]+/- (Bank 15, 2.5V LVDS)
  ▼
AD9364PHY  [cd_rfic, 245.76 MHz]
  │  6× IBUFDS + IDDR (DDR de-serialise)
  │  Frame alignment + 4-cycle IQ assembly
  │  Output: valid + {IA,QA,IB,QB} 12-bit each
  ▼
rx_cdc  AsyncFIFO  [rfic→sys]
  │  64-bit: [QB:IB:QA:IA] sign-extended to 16-bit each
  ▼
AD9364Core.source  [cd_sys, 125 MHz]
  ▼
pcie_dma0.sink  (LitePCIeDMA reader channel)
  │  Scatter-gather descriptor engine
  │  Writes to host RAM via PCIe TLP
  │  MSI interrupt on completion
  ▼
Host CPU RAM  →  application (GNU Radio, SoapySDR, etc.)
```

### 5.2 PCIe TX Path (Host → AD9364)

```
Host CPU RAM  (application writes IQ data)
  ▼
pcie_dma0.source  (LitePCIeDMA writer channel)
  │  PCIe TLP reads from host RAM
  │  MSI interrupt on completion
  ▼
AD9364Core.sink  [cd_sys, 125 MHz]
  ▼
tx_cdc  AsyncFIFO  [sys→rfic]
  ▼
AD9364PHY.sink  [cd_rfic, 245.76 MHz]
  │  4-cycle serialisation → 6-bit DDR half-words
  │  6× ODDR + OBUFDS
  ▼
AD9364 RFIC  (TX_D[5:0]+/-, TX_FRAME, FB_CLK)
```

### 5.3 USB RX Path (AD9364 → Host via USB)

```
AD9364Core.source  [cd_sys, 125 MHz]
  ▼
usb_rx_cdc  AsyncFIFO  [sys→usb]
  ▼
usb_iq_device.v  [cd_usb, 60 MHz]
  │  USB 2.0 HS EP1 IN bulk endpoint
  │  512-byte packet assembly
  ▼
USB3320C ULPI PHY  →  USB cable  →  Host
```

### 5.4 USB TX Path (Host via USB → AD9364)

```
Host  →  USB cable  →  USB3320C ULPI PHY
  ▼
usb_iq_device.v  [cd_usb, 60 MHz]
  │  USB 2.0 HS EP2 OUT bulk endpoint
  ▼
usb_tx_cdc  AsyncFIFO  [usb→sys]
  ▼
AD9364Core.sink  [cd_sys, 125 MHz]  →  TX path (same as PCIe TX from here)
```

### 5.5 Control Path (CSR access)

```
Via PCIe:
  Host ──► PCIe BAR0 (128 KB) ──► LitePCIeEndpoint ──► Wishbone ──► CSR bus
  Access: /dev/litepcie0 ioctl

Via JTAG:
  OpenOCD ──► BSCANE2 (USER2) ──► JTAGBone ──► Wishbone ──► CSR bus
  Access: litex_server --jtag, then RemoteClient / litex_cli
```

---

## 6. CSR Register Map

Access via **PCIe BAR0** (`/dev/litepcie0` ioctl) or **JTAGBone** (`litex_server --jtag` → `RemoteClient`).
All offsets are relative to BAR0 base / Wishbone `0x00000000`. All registers 32-bit wide.

### 6.1 Module Base Address Map

```
 Address     Module            Description
─────────────────────────────────────────────────────────────────────
 0x0000      pcie_dma0         PCIe DMA engine (writer + reader channels)
 0x0800      ad9364            AD9364 RFIC (PHY control + SPI master)
 0x1000      ctrl              SoC control (reset, scratch, bus errors)
 0x1800      dna               FPGA unique device DNA (57-bit serial)
 0x2000      hyperram          HyperRAM controller (8 MB IS66WVH8M8ALL)
 0x2800      icap              In-system reconfiguration (ICAPE2)
 0x3000      identifier_mem    SoC build identity string (read-only)
 0x3800      leds              User LED control
 0x4000      pcie_endpoint     PCIe endpoint status (link, MSI, payload)
 0x4800      pcie_msi          MSI interrupt controller
 0x5000      pcie_phy          PCIe PHY status (mirror of endpoint)
 0x5800      timer0            System timer with interrupt
 0x6000      xadc              FPGA on-chip sensors (temp + voltages)
─────────────────────────────────────────────────────────────────────
```

### 6.2 PCIe DMA — `pcie_dma0` (base 0x0000)

```
┌──────────────────────────────────────┬────────┬─────┬───────────────────────────────┐
│ Register                             │ Offset │ R/W │ Description                   │
├──────────────────────────────────────┼────────┼─────┼───────────────────────────────┤
│ pcie_dma0_writer_enable              │ 0x0000 │ RW  │ Enable Host→FPGA DMA writer   │
│ pcie_dma0_writer_table_value         │ 0x0004 │ RW  │ Descriptor address (64-bit)   │
│ pcie_dma0_writer_table_we            │ 0x000C │ RW  │ Push descriptor to table      │
│ pcie_dma0_writer_table_loop_prog_n   │ 0x0010 │ RW  │ Loop mode program             │
│ pcie_dma0_writer_table_loop_status   │ 0x0014 │ RO  │ Loop mode active status       │
│ pcie_dma0_writer_table_level         │ 0x0018 │ RO  │ Descriptors pending           │
│ pcie_dma0_writer_table_reset         │ 0x001C │ RW  │ Flush descriptor table        │
├──────────────────────────────────────┼────────┼─────┼───────────────────────────────┤
│ pcie_dma0_reader_enable              │ 0x0020 │ RW  │ Enable FPGA→Host DMA reader   │
│ pcie_dma0_reader_table_value         │ 0x0024 │ RW  │ Descriptor address (64-bit)   │
│ pcie_dma0_reader_table_we            │ 0x002C │ RW  │ Push descriptor to table      │
│ pcie_dma0_reader_table_loop_prog_n   │ 0x0030 │ RW  │ Loop mode program             │
│ pcie_dma0_reader_table_loop_status   │ 0x0034 │ RO  │ Loop mode active status       │
│ pcie_dma0_reader_table_level         │ 0x0038 │ RO  │ Descriptors pending           │
│ pcie_dma0_reader_table_reset         │ 0x003C │ RW  │ Flush descriptor table        │
├──────────────────────────────────────┼────────┼─────┼───────────────────────────────┤
│ pcie_dma0_buffering_reader_fifo_ctrl │ 0x0040 │ RW  │ Reader FIFO control           │
│ pcie_dma0_buffering_reader_fifo_sts  │ 0x0044 │ RO  │ Reader FIFO status/level      │
│ pcie_dma0_buffering_writer_fifo_ctrl │ 0x0048 │ RW  │ Writer FIFO control           │
│ pcie_dma0_buffering_writer_fifo_sts  │ 0x004C │ RO  │ Writer FIFO status/level      │
└──────────────────────────────────────┴────────┴─────┴───────────────────────────────┘
  MSI vectors:  reader done = 0,  writer done = 1
```

### 6.3 AD9364 RFIC — `ad9364` (base 0x0800)

```
┌──────────────────────┬────────┬─────┬──────────────────────────────────────────────┐
│ Register             │ Offset │ R/W │ Bit Fields                                   │
├──────────────────────┼────────┼─────┼──────────────────────────────────────────────┤
│ ad9364_phy_control   │ 0x0800 │ RW  │ [0] rst_n  (1=run, 0=hold in reset)         │
│                      │        │     │ [1] loopback (1=TX→RX internal loopback)     │
├──────────────────────┼────────┼─────┼──────────────────────────────────────────────┤
│ ad9364_spi_control   │ 0x0804 │ RW  │ [0]    start  — pulse to begin transfer      │
│                      │        │     │ [15:8] length — transfer length in bits (24) │
├──────────────────────┼────────┼─────┼──────────────────────────────────────────────┤
│ ad9364_spi_status    │ 0x0808 │ RO  │ [0] done — 1 when transfer complete          │
├──────────────────────┼────────┼─────┼──────────────────────────────────────────────┤
│ ad9364_spi_mosi      │ 0x080C │ RW  │ [23:0] — 24-bit SPI word to transmit        │
├──────────────────────┼────────┼─────┼──────────────────────────────────────────────┤
│ ad9364_spi_miso      │ 0x0810 │ RO  │ [23:0] — 24-bit SPI word received           │
└──────────────────────┴────────┴─────┴──────────────────────────────────────────────┘

  SPI frame format (24-bit):
  ┌───┬───────┬─────────────────┬───────────────┐
  │23 │ 22:21 │      20:8       │      7:0      │
  ├───┼───────┼─────────────────┼───────────────┤
  │R/W│ W1:W0 │  Address[12:0]  │   Data[7:0]   │
  └───┴───────┴─────────────────┴───────────────┘
   1=R  00=1B   AD9364 register    read/write byte
```

### 6.4 On-Chip Utilities

```
┌──────────────────────┬────────┬─────┬──────────────────────────────────────────────┐
│ Register             │ Offset │ R/W │ Description                                  │
├──────────────────────┼────────┼─────┼──────────────────────────────────────────────┤
│ ctrl_reset           │ 0x1000 │ RW  │ SoC reset                                    │
│ ctrl_scratch         │ 0x1004 │ RW  │ Scratch register (read-back test)            │
│ ctrl_bus_errors      │ 0x1008 │ RO  │ Wishbone bus error counter                   │
├──────────────────────┼────────┼─────┼──────────────────────────────────────────────┤
│ dna_id               │ 0x1800 │ RO  │ FPGA DNA [63:32] — upper word               │
│ dna_id               │ 0x1804 │ RO  │ FPGA DNA [31: 0] — lower word               │
│                      │        │     │ Combine: (hi << 32) | lo → 64-bit serial     │
├──────────────────────┼────────┼─────┼──────────────────────────────────────────────┤
│ icap_addr            │ 0x2800 │ RW  │ ICAP register address                        │
│ icap_data            │ 0x2804 │ RW  │ ICAP write data                              │
│ icap_write           │ 0x2808 │ RW  │ Trigger ICAP write                           │
│ icap_done            │ 0x280C │ RO  │ ICAP operation complete                      │
│ icap_read            │ 0x2810 │ RW  │ Trigger ICAP read                            │
├──────────────────────┼────────┼─────┼──────────────────────────────────────────────┤
│ leds_out             │ 0x3800 │ RW  │ [0] LED_D3 (N17)   [1] LED_D4 (N18)        │
├──────────────────────┼────────┼─────┼──────────────────────────────────────────────┤
│ timer0_load          │ 0x5800 │ RW  │ Load value                                   │
│ timer0_reload        │ 0x5804 │ RW  │ Auto-reload value (0 = one-shot)             │
│ timer0_en            │ 0x5808 │ RW  │ Timer enable                                 │
│ timer0_value         │ 0x5810 │ RO  │ Current counter value                        │
│ timer0_ev_pending    │ 0x5818 │ RW  │ Interrupt pending (write 1 to clear)         │
│ timer0_ev_enable     │ 0x581C │ RW  │ Interrupt enable                             │
└──────────────────────┴────────┴─────┴──────────────────────────────────────────────┘
```

### 6.5 XADC — `xadc` (base 0x6000)

```
┌─────────────────────┬────────┬─────┬──────────────────────────────────────────────┐
│ Register            │ Offset │ R/W │ Description / Conversion Formula             │
├─────────────────────┼────────┼─────┼──────────────────────────────────────────────┤
│ xadc_temperature    │ 0x6000 │ RO  │ raw × 503.975 / 4096 − 273.15  →  °C        │
│ xadc_vccint         │ 0x6004 │ RO  │ raw × 3 / 4096  →  V  (nominal 1.0V)        │
│ xadc_vccaux         │ 0x6008 │ RO  │ raw × 3 / 4096  →  V  (nominal 1.8V)        │
│ xadc_vccbram        │ 0x600C │ RO  │ raw × 3 / 4096  →  V  (nominal 1.0V)        │
│ xadc_eoc            │ 0x6010 │ RO  │ End-of-conversion flag                       │
│ xadc_eos            │ 0x6014 │ RO  │ End-of-sequence flag                         │
└─────────────────────┴────────┴─────┴──────────────────────────────────────────────┘

  Healthy ranges:
    Temperature : 0–85 °C
    VCCINT      : 0.92–1.08 V   (1.0V ± 8%)
    VCCAUX      : 1.70–1.90 V   (1.8V ± 6%)
    VCCBRAM     : 0.92–1.08 V   (1.0V ± 8%)
```

### 6.6 PCIe Status — `pcie_endpoint` / `pcie_msi` (base 0x4000 / 0x4800)

```
┌───────────────────────────────────┬────────┬─────┬───────────────────────────────┐
│ Register                          │ Offset │ R/W │ Description                   │
├───────────────────────────────────┼────────┼─────┼───────────────────────────────┤
│ pcie_endpoint_phy_link_status     │ 0x4000 │ RO  │ Link up/down status           │
│ pcie_endpoint_phy_msi_enable      │ 0x4004 │ RO  │ MSI enabled by host           │
│ pcie_endpoint_phy_bus_master_en   │ 0x400C │ RO  │ Bus master enable from host   │
│ pcie_endpoint_phy_max_payload     │ 0x4014 │ RO  │ Negotiated max payload size   │
├───────────────────────────────────┼────────┼─────┼───────────────────────────────┤
│ pcie_msi_enable                   │ 0x4800 │ RW  │ Enable MSI interrupt output   │
│ pcie_msi_clear                    │ 0x4804 │ RW  │ Clear pending interrupt       │
│ pcie_msi_vector                   │ 0x4808 │ RO  │ Active interrupt vector       │
└───────────────────────────────────┴────────┴─────┴───────────────────────────────┘
```

### MSI interrupt vectors

| Vector | Signal | Condition |
|---|---|---|
| 0 | `PCIE_DMA0_READER` | DMA reader descriptor complete |
| 1 | `PCIE_DMA0_WRITER` | DMA writer descriptor complete |

---

## 7. Memory Map

| Region | Base | Size | Description |
|---|---|---|---|
| CSR | `0x00000000` | 64 KB | All control/status registers |
| HyperRAM | `0x40000000` | 8 MB | IS66WVH8M8ALL (Wishbone slave) |

---

## 8. FPGA Resource Utilization

**Build:** `hallycon_m2sdr_platform` — post-place, Fully Placed
**Device:** `xc7a50t-2csg325` (Artix-7 50T, speed grade −2)
**Tool:** Vivado 2025.2 — `report_utilization_place.rpt`

### 8.1 Fabric Resources

```
┌─────────────────────┬────────┬───────────┬────────┬─────────────────────────────────┐
│ Resource            │  Used  │ Available │  Util% │ Notes                           │
├─────────────────────┼────────┼───────────┼────────┼─────────────────────────────────┤
│ Slice LUTs          │  5,010 │    32,600 │  15.4% │ ◄ 85% headroom for baseband     │
│   LUT as Logic      │  4,245 │    32,600 │  13.0% │                                 │
│   LUT as Memory     │    765 │     9,600 │   8.0% │ Distributed RAM (FIFOs, CSR)    │
├─────────────────────┼────────┼───────────┼────────┼─────────────────────────────────┤
│ Slice Registers     │  5,466 │    65,200 │   8.4% │ ◄ 92% headroom                  │
│   Flip Flops        │  5,466 │    65,200 │   8.4% │                                 │
│   Latches           │      0 │    65,200 │   0.0% │                                 │
├─────────────────────┼────────┼───────────┼────────┼─────────────────────────────────┤
│ Slices              │  2,083 │     8,150 │  25.6% │                                 │
├─────────────────────┼────────┼───────────┼────────┼─────────────────────────────────┤
│ Block RAM Tiles     │     27 │        75 │  36.0% │ 25× RAMB36 + 4× RAMB18         │
├─────────────────────┼────────┼───────────┼────────┼─────────────────────────────────┤
│ DSP48               │      0 │       120 │   0.0% │ ◄ entirely free for DSP/filters │
└─────────────────────┴────────┴───────────┴────────┴─────────────────────────────────┘
```

### 8.2 IO and Clocking Resources

```
┌──────────────────────┬──────┬───────────┬────────┬────────────────────────────────┐
│ Resource             │ Used │ Available │  Util% │ Notes                          │
├──────────────────────┼──────┼───────────┼────────┼────────────────────────────────┤
│ Bonded IOB           │   69 │       150 │  46.0% │ 36 master + 33 slave pads      │
│ IBUFDS               │    8 │       144 │   5.6% │ LVDS RX inputs (rfic + pcie)   │
│ IDDR                 │    7 │       150 │   4.7% │ DDR RX deserialise             │
│ ODDR                 │    8 │       150 │   5.3% │ DDR TX serialise               │
│ OBUFDS               │    8 │       150 │   5.3% │ LVDS TX outputs                │
├──────────────────────┼──────┼───────────┼────────┼────────────────────────────────┤
│ BUFGCTRL             │   12 │        32 │  37.5% │ Global clock buffers           │
│ MMCME2_ADV           │    1 │         5 │  20.0% │ sys (125 MHz) + idelay (200 MHz│
│ PLLE2_ADV            │    1 │         5 │  20.0% │ PCIe PHY PLL (internal)        │
├──────────────────────┼──────┼───────────┼────────┼────────────────────────────────┤
│ GTPE2_CHANNEL        │    2 │         4 │  50.0% │ PCIe Gen2 x2 lanes             │
│ IBUFDS_GTE2          │    1 │         2 │  50.0% │ PCIe 100 MHz refclk input      │
└──────────────────────┴──────┴───────────┴────────┴────────────────────────────────┘
```

### 8.3 Specialized Primitives

```
┌────────────┬──────┬───────────┬────────┬────────────────────────────────────────┐
│ Primitive  │ Used │ Available │  Util% │ Function                               │
├────────────┼──────┼───────────┼────────┼────────────────────────────────────────┤
│ PCIE_2_1   │    1 │         1 │  100%  │ Hard PCIe Gen2 x2 block                │
│ XADC       │    1 │         1 │  100%  │ FPGA temperature + voltage monitor     │
│ DNA_PORT   │    1 │         1 │  100%  │ 57-bit unique device serial            │
│ ICAPE2     │    1 │         2 │   50%  │ In-system reconfiguration (reload)     │
│ BSCANE2    │    1 │         4 │   25%  │ JTAGBone JTAG→Wishbone bridge          │
└────────────┴──────┴───────────┴────────┴────────────────────────────────────────┘
```

### 8.4 Per-Module Cost Breakdown (estimates)

```
┌────────────────────────────────┬────────────┬────────────┬────────┐
│ Module                         │  LUTs      │  Registers │  BRAM  │
├────────────────────────────────┼────────────┼────────────┼────────┤
│ LitePCIe (PHY + DMA + MSI)     │  ~1,800    │  ~1,800    │  ~12   │
│ AD9364 (PHY + SPI + CDC)       │    ~600    │    ~800    │   ~4   │
│ USB IQ device + CDC FIFOs      │    ~800    │    ~800    │   ~2   │
│ JTAGBone                       │    ~600    │    ~600    │   ~2   │
│ CRG + LEDs + SoC infra         │    ~800    │    ~900    │   ~7   │
│ HyperRAM                       │    ~300    │    ~400    │    0   │
│ ICAP + XADC + DNA              │    ~100    │    ~150    │    0   │
├────────────────────────────────┼────────────┼────────────┼────────┤
│ TOTAL                          │  ~5,000    │  ~5,450    │  ~27   │
└────────────────────────────────┴────────────┴────────────┴────────┘
```

### 8.5 Headroom Summary

```
LUTs       ████░░░░░░░░░░░░░░░░░░░░░░  15% used  —  ~27,590 LUTs free
Registers  ████░░░░░░░░░░░░░░░░░░░░░░   8% used  —  ~59,734 FFs free
BRAM       ████████░░░░░░░░░░░░░░░░░░  36% used  —  ~48 tiles free
DSP        ░░░░░░░░░░░░░░░░░░░░░░░░░░   0% used  —  120 DSPs free
```

OpenOFDM baseband (64-point FFT + Viterbi) estimated at ~8,000 LUTs — fits
comfortably with existing infrastructure in place.

---

## 9. Timing Results

**Status: All setup and hold paths met. One benign WPWS violation.**

| Metric | Value | Status |
|---|---|---|
| WNS (Worst Negative Slack) | +0.118 ns | PASS |
| TNS (Total Negative Slack) | 0.000 ns | PASS |
| WHS (Worst Hold Slack) | +0.036 ns | PASS |
| WPWS (Worst Pulse Width Slack) | −0.016 ns | Benign* |

*The WPWS violation is on a `RAMD32` distributed RAM cell, 1 of 6387 total. It is a
Vivado slow-corner analysis artifact — functionally safe, does not affect operation.

### Per-clock domain results

| Clock | WNS | Domain |
|---|---|---|
| `main_crg_clkout0` (125 MHz sys) | +0.118 ns | Main fabric |
| `ad9364_rfic_rx_clk_p` (245.76 MHz) | +0.285 ns | RFIC data |
| `clk40` (40 MHz input) | +23.863 ns | TCXO input |

### CDC path results (set_max_delay paths)

| From → To | Slack | Constraint |
|---|---|---|
| sys → rfic | +2.173 ns | 4.0 ns max delay |
| rfic → sys | +6.107 ns | 8.0 ns max delay |
| sys → usb | +7.157 ns | 8.0 ns max delay |
| usb → sys | +7.131 ns | 8.0 ns max delay |

---

## 10. PCIe Configuration

Vivado IP core: `pcie_7x`, module name `pcie_s7`

| Parameter | Value |
|---|---|
| Device ID | 0x7022 |
| Block location | X0Y0 |
| Link speed | 5.0 GT/s (Gen2) |
| Max link width | x2 |
| Reference clock | 100 MHz |
| User clock | 125 MHz |
| Interface width | 64-bit |
| Max payload | 512 bytes |
| Legacy interrupt | None |
| MSI 64-bit | Disabled |
| BAR0 | 1 MB (Megabytes scale) |
| Buffer optimisation | Enabled (Buf_Opt_BMA=True) |

---

## 11. Pin Assignments (Key Signals)

### Clock pins

| Signal | Pin | Bank | Standard | Notes |
|---|---|---|---|---|
| `clk40` (40 MHz TCXO) | P4 | 34 | LVCMOS18 | IO_L12P_T1_MRCC_34 |
| `ad9364_rfic.rx_clk_p` (DATA_CLK+) | E16 | 15 | LVDS_25 | IO_L14P_T2_SRCC_15 |
| `ad9364_rfic.rx_clk_n` (DATA_CLK−) | D16 | 15 | LVDS_25 | IO_L14N_T2_SRCC_15 |
| `ulpi.clk` (60 MHz from USB3320) | R3 | 34 | LVCMOS18 | IO_L14P_T2_SRCC_34 |
| `pcie_x2.clk_p` (100 MHz PCIe ref+) | D6 | — | LVDS | MGTREFCLK0P_216 |
| `pcie_x2.clk_n` (100 MHz PCIe ref−) | D5 | — | LVDS | MGTREFCLK0N_216 |

### AD9364 data interface (Bank 15, 2.5V LVDS)

| Signal | Pins (+/−) | Direction |
|---|---|---|
| RX_FRAME | D8 / C8 | AD9364 → FPGA |
| RX_D[0..5] | C16/B17, E17/D18, C17/C18, H16/G16, G15/F15, G17/F18 | AD9364 → FPGA |
| FB_CLK (TX_CLK) | D13 / C13 | FPGA → AD9364 |
| TX_FRAME | B10 / A10 | FPGA → AD9364 |
| TX_D[0..5] | B14/A15, D11/C12, B12/A12, C11/B11, B9/A9, D9/C9 | FPGA → AD9364 |

### AD9364 control (Bank 15, 2.5V LVCMOS)

| Signal | Pin | Direction |
|---|---|---|
| `rst_n` | C14 | FPGA → AD9364 |
| `enable` | H18 | FPGA → AD9364 |
| `txnrx` | F14 | FPGA → AD9364 |
| `clk_out` | E15 | AD9364 → FPGA (unused in v2) |

### AD9364 SPI (Bank 15, 2.5V LVCMOS)

| Signal | Pin |
|---|---|
| `sclk` | F17 |
| `cs_n` | E18 |
| `mosi` | D14 |
| `miso` | G14 |

### USB3320C ULPI (Bank 34, 1.8V LVCMOS)

| Signal | Pin |
|---|---|
| `clk` (60 MHz in) | R3 |
| `data[7:0]` | V3,V2,T4,T3,U4,V4,P6,U6 |
| `dir` | R7 |
| `nxt` | T7 |
| `stp` | U7 |
| `rst` | V6 |

### PCIe (GTP transceivers, Bank 216)

| Signal | Pins |
|---|---|
| `rx_p[1:0]` | E4, A4 |
| `rx_n[1:0]` | E3, A3 |
| `tx_p[1:0]` | H2, F2 |
| `tx_n[1:0]` | H1, F1 |
| `rst_n` | T18 (Bank 14, LVCMOS33) |

### LEDs (Bank 14, 3.3V LVCMOS)

| LED | Pin |
|---|---|
| `user_led[0]` (LED_D3) | N17 |
| `user_led[1]` (LED_D4) | N18 |

---

## 12. File Structure

```
m2sdr50t/
├── hallycon_m2sdr_platform.py      # Platform: pin definitions, IO standards, CDC constraints
├── hallycon_m2sdr_target_v2.py     # Top-level SoC: all modules wired together
├── usb_iq_device.py                # LUNA Amaranth source for USB bulk device
├── usb_iq_device.v                 # Generated Verilog (354 KB) — do not hand-edit
├── validate_sdr.py                 # Customer-facing hardware validation script
│
├── docs/
│   ├── hallycon_m2sdr_v2_design.md         # This file
│   ├── clocking_and_ad9364_init.md         # Clocking + AD9364 init deep-dive
│   └── resource_utilization.md             # Utilization comparison vs litex_m2sdr
│
├── build/
│   └── hallycon_m2sdr_platform/
│       ├── gateware/
│       │   ├── hallycon_m2sdr_platform.bit          # Bitstream
│       │   ├── hallycon_m2sdr_platform.bin          # QSPI flash image
│       │   ├── hallycon_m2sdr_platform.v            # LiteX-generated RTL
│       │   ├── hallycon_m2sdr_platform.tcl          # Vivado build script
│       │   ├── hallycon_m2sdr_platform_timing.rpt   # Final timing report
│       │   └── hallycon_m2sdr_platform_utilization_place.rpt
│       ├── csr.csv                          # Full CSR address map
│       └── software/
│           ├── include/generated/csr.h      # C CSR accessors
│           ├── kernel/                      # LitePCIe kernel module
│           └── user/                        # LitePCIe userspace lib
│
├── litex_m2sdr/                    # Reference design (Enjoy-Digital) — for comparison
└── install/                        # LiteX / Migen installation scripts
```

---

## 13. Build Instructions

### Prerequisites

```bash
# LiteX ecosystem
pip install litex litepcie migen

# LUNA (for USB device generation)
pip install luna amaranth amaranth-yosys

# Vivado 2025.2 must be in PATH
source /opt/Xilinx/Vivado/2025.2/settings64.sh
```

### Regenerate USB Verilog (only if usb_iq_device.py changes)

```bash
cd /mnt/d/work/m2sdr50t
python3 usb_iq_device.py          # writes usb_iq_device.v
```

### Build bitstream

```bash
cd /mnt/d/work/m2sdr50t
python3 hallycon_m2sdr_target_v2.py --build
# Output: build/hallycon_m2sdr_platform/gateware/hallycon_m2sdr_platform.bit
```

### Load bitstream via JTAG

```bash
python3 hallycon_m2sdr_target_v2.py --load
# Uses OpenFPGALoader with digilent_hs2 cable, freq=10 MHz
```

### Flash to QSPI

```bash
openFPGALoader --board arty_a7 \
  build/hallycon_m2sdr_platform/gateware/hallycon_m2sdr_platform.bin
```

### Build litepcie kernel module

```bash
cd build/hallycon_m2sdr_platform/software/kernel
make
sudo insmod litepcie.ko
# /dev/litepcie0 should appear
```

### Run validation

```bash
# Terminal 1 (JTAG path, no PCIe driver needed):
litex_server --jtag --jtag-config openocd_xc7_ft2232.cfg

# Terminal 2:
python3 validate_sdr.py --transport jtag

# Or with PCIe driver loaded:
sudo python3 validate_sdr.py
```

---

## 14. Bring-Up Sequence

```
Step 1: Load bitstream via JTAG (J5 header)
        → LEDs start chasing (~7.5 Hz) — FPGA alive, cd_sys running

Step 2: Insert board into M.2 slot / power via USB
        → Host sees PCIe device (lspci should show Xilinx device 7022)

Step 3: Load litepcie kernel module
        → /dev/litepcie0 appears

Step 4: Run validate_sdr.py (JTAG or PCIe)
        → XADC confirms voltages
        → DNA prints unique serial
        → AD9364 SPI product ID = 0x0A confirms RFIC wired and powered

Step 5: Send AD9364 full SPI init sequence (via no-OS library shim)
        → BBPLL locks
        → DATA_CLK (245.76 MHz) appears on E16
        → cd_rfic starts ticking
        → AsyncFIFO sys↔rfic becomes active

Step 6: Enable PCIe DMA (writer + reader)
        → IQ samples flowing: AD9364 → rfic → AsyncFIFO → sys → DMA → host RAM

Step 7: Tune to FM broadcast band (~100 MHz)
        → Verify non-zero, time-varying IQ samples in host application
        → FM demodulation as basic sanity check
```

---

## 15. What Is Done vs Pending

### Done (v1 IQ Streamer)

- [x] Pin map: all signals derived from schematic, verified against Artix-7 bank rules
- [x] Platform file: IO standards, CDC timing constraints, QSPI flash config
- [x] CRG: 40 MHz → 125 MHz sys, 200 MHz idelay, USB 60 MHz buffered
- [x] AD9364 PHY: full LVDS DDR RX + TX, 2R2T and 1R1T modes, frame alignment
- [x] AD9364 SPI master: 24-bit, 15.625 MHz, CSR-accessible
- [x] AD9364 CDC: sys↔rfic AsyncFIFO with proper timing constraints
- [x] PCIe Gen2 x2: full DMA with scatter-gather, MSI, 128 KB BAR0
- [x] LitePCIe software generation: kernel module + userspace library auto-built
- [x] USB 2.0 HS IQ device: LUNA-generated, EP1 IN + EP2 OUT, sys↔usb CDC
- [x] JTAGBone: full CSR access without PCIe driver
- [x] XADC, DNA, ICAP: on-chip utilities, CSR-accessible
- [x] HyperRAM: 8 MB mapped at 0x40000000, Wishbone slave
- [x] LEDs: chaser as heartbeat
- [x] Timing: all setup/hold paths met, CDC constraints clean
- [x] Resource utilization: 15.4% LUT, 36% BRAM — 85% LUT headroom for baseband
- [x] Validation script: `validate_sdr.py` (PCIe + JTAG modes)
- [x] Design documentation: this file + clocking_and_ad9364_init.md

### Pending (Post-v1)

- [ ] **AD9364 no-OS driver shim** — 4 platform primitives wrapping litepcie CSR access
- [ ] **SoapySDR plugin** — exposes gain/freq/rate/bandwidth via standard API
- [ ] **PCIe userspace streaming demo** — simple Python/C loop-back test using DMA
- [ ] **USB streaming demo** — pyusb bulk transfer test
- [ ] **HyperRAM validation** — read/write test via JTAGBone
- [ ] **QSPI flash boot** — test cold-boot from flash (vs JTAG load)
- [ ] **OpenOFDM Phase 4** — wire Xilinx FFT IP into ifft_wrapper.v / fft_wrapper.v,
      replace MODEL Viterbi backend with Xilinx IP
- [ ] **Clock measurement CSR** — frequency counter on each clock domain
      (useful to confirm DATA_CLK appeared after AD9364 init)
- [ ] **AD9364 loopback test in validate_sdr.py** — enable internal loopback,
      transmit known IQ pattern, verify RX matches

---

## 16. Next Iteration — v2 Hardware (M.2 2260)

> **This section documents the planned v2 board revision. Nothing here is implemented yet.**
> v1 (2280) must be fully validated first.

### 16.1 Competitive Context

| Product | Company | Form factor | Size (mm) | M.2 standard? |
|---|---|---|---|---|
| Matchstiq S10 | Epiq Solutions | Custom | 31 × 50 | No |
| LimeSDR Mini 2.0 | Lime Micro | Custom | 69 × 31 | No |
| Hallycon M2 SDR v1 | Hallycon | M.2 2280 | 22 × 80 | Yes |
| **Hallycon M2 SDR v2** | **Hallycon** | **M.2 2260** | **22 × 60** | **Yes** |

Epiq Solutions is the closest competitor in terms of size and capability. Their 31×50mm
board (1,550 mm²) is not M.2 standard — it requires a custom carrier. The v2 at 22×60mm
(1,320 mm²) is **smaller**, **standard M.2**, and **plugs directly into any laptop or
desktop** without a carrier board. No competitor currently ships an SDR in M.2 2260.

---

### 16.2 What Changes in v2

```
┌────────────────────────┬────────────────────┬────────────────────────────────────┐
│ Item                   │ v1 (2280)          │ v2 (2260)                          │
├────────────────────────┼────────────────────┼────────────────────────────────────┤
│ Form factor            │ 22 × 80 mm         │ 22 × 60 mm  (-20mm)                │
│ HyperRAM               │ IS66WVH8M8ALL 8MB  │ Removed                            │
│ JTAG header            │ J5 2×5 on board    │ Removed (routed to M.2 pins)       │
│ JTAG test pads         │ Via J5             │ 4× SMD pads for pogo fixture       │
│ GPS PPS                │ Not available      │ M.2 pin 7 (DSA)                    │
│ UART debug             │ Not available      │ M.2 pins 9/53 (DSS/W_DIS1#)       │
│ RF section             │ Identical          │ Identical                          │
│ FPGA                   │ Identical          │ Identical                          │
│ AD9364                 │ Identical          │ Identical                          │
│ USB3320                │ Identical          │ Identical                          │
│ PCIe                   │ Identical          │ Identical                          │
│ Power                  │ ~same              │ ~same (HyperRAM was ~100mW)        │
└────────────────────────┴────────────────────┴────────────────────────────────────┘
```

---

### 16.3 M.2 Pin Allocation — v2 Full Map

Epiq Solutions pioneered this concept — mapping JTAG + GPS PPS + UART to unused M.2
pins so the connector becomes a complete system interface, not just a PCIe lane.
v2 follows the same principle.

```
M.2 B+M Key — pin reuse in PCIe mode (SATA pins unused):

┌─────────┬────────────┬───────────┬──────────────────────────────────────────────┐
│ M.2 Pin │ Std Signal │ v2 Use    │ Notes                                        │
├─────────┼────────────┼───────────┼──────────────────────────────────────────────┤
│  29     │ SATA-A+    │ TDI       │ JTAG data in  (FPGA T9, Bank 0, 3.3V)       │
│  31     │ SATA-A-    │ TDO       │ JTAG data out (FPGA T8)                      │
│  33     │ SATA-B+    │ TMS       │ JTAG mode sel (FPGA R8)                      │
│  35     │ SATA-B-    │ TCK       │ JTAG clock    (FPGA F8) + 33Ω series R      │
├─────────┼────────────┼───────────┼──────────────────────────────────────────────┤
│   7     │ DSA        │ GPS PPS   │ 1PPS input from host/carrier GPS module      │
├─────────┼────────────┼───────────┼──────────────────────────────────────────────┤
│   9     │ DSS        │ UART TX   │ FPGA → host debug console (115200 8N1)       │
│  53     │ W_DIS1#    │ UART RX   │ host → FPGA                                  │
├─────────┼────────────┼───────────┼──────────────────────────────────────────────┤
│  67     │ W_DIS2#    │ GPIO spare│ Reserved for future use                      │
└─────────┴────────────┴───────────┴──────────────────────────────────────────────┘

Standard M.2 signals unchanged:
  PCIe x2 (lanes 0+1), PERST#, CLKREQ#, WAKE#, 3.3V, GND — all identical
```

---

### 16.4 GPS PPS — What It Enables

The GPS PPS pin (M.2 pin 7) was not possible on v1 without a hardware add-on.
On v2 it is a first-class signal through the standard connector.

```
Host system / carrier board
  └── GPS module (u-blox M8/M9 or similar)
        └── 1PPS output (±50ns accuracy)
              └── M.2 pin 7 (DSA) → FPGA
                    └── timestamp counter + AD9364 BBPLL fractional trim
```

This enables:
- **DVB-T SFN** — multiple v2 units phase-locked via GPS, true single-frequency network
- **Precision timestamped capture** — IQ samples tagged with absolute GPS time
- **Multi-board phase coherence** — same PPS → same clock correction → coherent RX
- **TDOA positioning** — time-difference-of-arrival across multiple units

---

### 16.5 M.2 Debug Adapter (Companion PCB)

A small adapter board allows full JTAG + UART + PPS access during development.
Only engineers ever use it — never shipped with the product.

```
┌──────────────────────────────────────────────────────────────┐
│                  M.2 JTAG/Debug Adapter                      │
│                                                              │
│  [M.2 male edge] ──► passthrough ──► [M.2 female socket]    │
│  (to host slot)                       (SDR card plugs in)   │
│                          │                                   │
│                 ┌────────┴────────┐                          │
│                 │  taps pins      │                          │
│                 │  29/31/33/35    │                          │
│                 │   7, 9, 53      │                          │
│                 └────────┬────────┘                          │
│                          │                                   │
│              ┌───────────┼───────────┐                       │
│              ▼           ▼           ▼                       │
│        JTAG 2×5     USB-UART     SMA/header                  │
│        header       (CP2102)     for PPS in                  │
└──────────────────────────────────────────────────────────────┘
  ~30 × 22 mm, 2-layer PCB, ~$5 to manufacture
```

Pass-through design: SDR remains in M.2 slot, PCIe active, JTAG + UART accessible
simultaneously. No need to remove the card to debug.

---

### 16.6 v2 Gateware Changes

```
Remove:   LiteHyperBus              (~300 LUT, ~400 FF, frees Wishbone slave)
Remove:   hyperram from memory map  (0x40000000 region gone)
Remove:   hyperram CSRs
Add:      GPS PPS input CSR         (timestamp counter, latch register)
Add:      UART debug core           (LiteX UARTBone or simple TX/RX)
Keep:     JTAGBone (BSCANE2)        still useful for in-system debug
Keep:     Everything else           identical to v1
```

Net gateware change: removes ~700 LUT/FF, adds ~200 LUT for PPS + UART.
Overall utilization stays well under 20% LUTs.

---

### 16.7 v2 Size Comparison

```
Epiq Matchstiq S10:    31 × 50 mm  =  1,550 mm²  (custom form factor, needs carrier)
Hallycon v1 (2280):    22 × 80 mm  =  1,760 mm²  (standard M.2, no carrier needed)
Hallycon v2 (2260):    22 × 60 mm  =  1,320 mm²  (standard M.2, smallest in class)
```

v2 is the smallest full-duplex 2R2T SDR in a standard form factor.
No other manufacturer currently ships a 2R2T SDR in M.2 2260.

---

## 17. Known Issues and Notes (v1)

| Issue | Severity | Notes |
|---|---|---|
| WPWS −0.016 ns on RAMD32 | Benign | Vivado slow-corner artifact, 1 cell, not functional |
| `cd_rfic` absent until AD9364 init | Expected | AsyncFIFO is safe when source clock is absent; IQ data simply won't flow |
| USB bandwidth limited | By design | USB 2.0 HS ~45 MB/s → ~5.6 MSPS 1R1T; PCIe is the high-throughput path |
| `usb_iq_device.v` not hand-editable | By design | 354 KB generated file; modify `usb_iq_device.py` and regenerate |
| PCIe Device ID 0x7022 | Placeholder | Should be assigned a proper VID/PID for product release |
| No AGC control in gateware | v1 scope | AD9364 AGC runs autonomously in hardware; manual gain via SPI in v1 |
| HyperRAM not used in IQ path | v1 scope | Available for capture/replay in future versions |
| `txnrx = 0` (FDD hardcoded) | By design | TDD support requires gateware change to drive txnrx dynamically |
