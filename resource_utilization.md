# FPGA Resource Utilization Comparison

## Builds Compared

| Build | Device | Speed | PCIe | Date |
|---|---|---|---|---|
| `hallycon_m2sdr_platform` (our design) | XC7A50T-2CSG325I | -2 | Yes (x1) + HyperRAM + AD9364 + JTAGBone + ICAP + XADC + DNA | 2026-04-04 |
| `litex_m2sdr_m2` (reference, no PCIe) | XC7A200T-3SBG484 | -3 | No | 2026-04-04 |
| `litex_m2sdr_m2_pcie_x1` (reference, with PCIe) | XC7A200T-3SBG484 | -3 | Yes (x1) | 2026-04-04 |

## Resource Utilization (Post-Place)

| Resource | Our design (v2, full) | litex_m2sdr (no PCIe) | litex_m2sdr (+ PCIe) |
|---|---|---|---|
| Slice LUTs | 4,289 / 32,600 = **13.2%** | 2,540 / 133,800 = 1.9% | 7,407 / 133,800 = 5.5% |
| Registers | 4,725 / 65,200 = **7.3%** | 4,435 / 267,600 = 1.7% | 8,489 / 267,600 = 3.2% |
| Block RAM Tiles | 26 / 75 = **34.7%** | 0 / 365 = 0% | 36 / 365 = 9.9% |
| DSP | 0 / 120 = 0% | 0 / 740 = 0% | 0 / 740 = 0% |
| GTPE2_CHANNEL | 2 / 4 = 50% | 0 / 4 = 0% | 1 / 4 = 25% |
| PCIE_2_1 | 1 / 1 = 100% | 0 / 1 = 0% | 1 / 1 = 100% |
| Bonded IOB | 57 / 150 = 38% | 64 / 285 = 22% | 65 / 285 = 23% |
| BUFGCTRL | 12 / 32 = 37.5% | 7 / 32 = 22% | 11 / 32 = 34% |

## Features in Each Build

### Our design (`hallycon_m2sdr_platform`)
- PCIe Gen2 x1 (hard PCIE_2_1 block + LitePCIe DMA)
- HyperRAM controller (IS66WVH8M8ALL, 8MB)
- AD9364 LVDS PHY (RX + TX, 6-bit DDR)
- USB ULPI PHY (USB3320, pinned out but minimal logic)
- QSPI Flash controller
- CRG (40 MHz TCXO → 125 MHz sys via MMCM)
- JTAGBone (BSCANE2 — JTAG → Wishbone CSR bridge, ~3,400 LUT overhead)
- ICAP (remote bitstream reload)
- XADC (on-chip temperature + voltage monitoring)
- DNA (unique device serial)

### litex_m2sdr reference (extras vs our design)
- SI5351 programmable clock generator (I2C)
- XADC (on-chip ADC / temperature monitor)
- DNA (device unique serial)
- ICAP (remote bitstream reload)
- JTAGBone debug bridge
- TimeGenerator + PPSGenerator (timestamping)
- SharedQPLL (shared between PCIe / Ethernet / SATA SerDes)
- TX/RX Header (embeds timestamp in stream)
- TX/RX Loopback + stream Crossbar (mux/demux for PCIe / Eth / SATA)
- PRBS + AGC support on AD9361 core
- MultiClk measurement CSR
- StatusLed (multiplexed LED state machine)
- Optional: Ethernet SFP (1000BaseX / 2500BaseX)
- Optional: SATA storage
- Optional: White Rabbit precision timing
- Optional: PCIe PTM (Precision Time Measurement)
- Optional: GPIO via AD9361 control bits
- Optional: LiteScope on-chip debug probes

## Key Observations

### PCIe DMA fabric cost
Delta between litex_m2sdr no-PCIe and with-PCIe:
- **~4,867 LUTs** and **~4,054 FFs** — this is the LitePCIe DMA wrapper logic in fabric.
- The `PCIE_2_1` hard block itself costs **zero fabric LUTs**.

### Our design on XC7A50T
If we added all litex_m2sdr features to our xc7a50t design, estimated total would be
~7,400 LUTs = **~22.7% of our 32,600 LUTs**. Leaves ~75% for baseband / signal processing.

### BRAM usage difference
- Their PCIe DMA uses large descriptor FIFOs → 36 BRAM tiles.
- Our design uses 10 BRAM tiles (AsyncFIFOs + HyperRAM controller).

### Tightest resource: BUFGCTRL
Clock buffers at **34%** (11/32) in the reference design with PCIe — the most constrained
resource, not LUTs. Adding Ethernet or SATA would push this further.

### XC7A50T device limits (for planning)
| Resource | Total | Notes |
|---|---|---|
| Slice LUTs | 32,600 | ~22% used by full feature set |
| Registers | 65,200 | |
| Block RAM tiles | 75 | 10 used now; 65 free |
| DSP48E1 | 120 | 0 used — all free |
| GTPE2_CHANNEL | 4 | 2 used (PCIe x1); 2 free |
| BUFGCTRL | 32 | 10 used now |
| PCIE_2_1 | 1 | Used |
