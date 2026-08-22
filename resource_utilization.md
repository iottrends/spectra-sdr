# FPGA Resource Utilization Comparison

## Builds Compared

| Build | Device | Speed | PCIe | Date |
|---|---|---|---|---|
| `spectra_platform` (our design, rev 4.1) | XC7A50T-2CSG325I | -2 | Yes (x2) + HyperRAM + AD9364 + USB IQ/CTRL + PCIe/USB Wishbone Bridges + IQ Stream Mux + JTAGBone + ICAP + XADC + DNA | 2026-08-22 |
| `litex_m2sdr_m2` (reference, no PCIe) | XC7A200T-3SBG484 | -3 | No | 2026-04-04 |
| `litex_m2sdr_m2_pcie_x1` (reference, with PCIe) | XC7A200T-3SBG484 | -3 | Yes (x1) | 2026-04-04 |

The `litex_m2sdr` reference columns are unchanged from the original comparison
(no rebuild of that project was run in this pass) — only the "our design"
column below reflects the current bitstream. Rev 4.1 adds the `ad9364_reset`
CSR (dedicated self-releasing register for AD9364 RESETB/pin C14, replacing
the earlier bit-2-of-`ad9364_phy_control` approach) on top of rev 4 — no
other functional change.

## Resource Utilization (Post-Place)

| Resource | Our design (rev 4.1, full) | litex_m2sdr (no PCIe) | litex_m2sdr (+ PCIe) |
|---|---|---|---|
| Slice LUTs | 5,770 / 32,600 = **17.70%** | 2,540 / 133,800 = 1.9% | 7,407 / 133,800 = 5.5% |
| Registers | 6,332 / 65,200 = **9.71%** | 4,435 / 267,600 = 1.7% | 8,489 / 267,600 = 3.2% |
| Block RAM Tiles | 28 / 75 = **37.33%** | 0 / 365 = 0% | 36 / 365 = 9.9% |
| DSP | 0 / 120 = 0% | 0 / 740 = 0% | 0 / 740 = 0% |
| GTPE2_CHANNEL | 2 / 4 = 50% (PCIe x2) | 0 / 4 = 0% | 1 / 4 = 25% |
| PCIE_2_1 | 1 / 1 = 100% | 0 / 1 = 0% | 1 / 1 = 100% |
| Bonded IOB | 69 / 150 = 46.00% | 64 / 285 = 22% | 65 / 285 = 23% |
| BUFGCTRL | 12 / 32 = 37.50% | 7 / 32 = 22% | 11 / 32 = 34% |

Post-route timing on the current build: WNS +0.275ns, WHS +0.050ns (0 setup/hold
violations), one pre-existing pulse-width slack of -0.016ns on a single endpoint
entirely inside the PCIe hard IP's own internal clocking (present in every build
in this series, unrelated to any of the additions below). Per-clock, the
`ad9364_rfic_rx_clk_p` domain (the one `ad9364_reset` and the rest of the AD9364
PHY live in) closes at WNS +0.286ns / WHS +0.113ns. DRC: 0 errors, 3 pre-existing
warnings. Full detail in the Vivado reports under `build/spectra_platform/gateware/`.

## Features in Each Build

### Our design (`spectra_platform`, rev 4.1)
- PCIe Gen2 x2 (hard PCIE_2_1 block + LitePCIe DMA)
- **PCIe Wishbone Bridge** (`LitePCIeWishboneMaster`) — BAR0 MMIO reaches the
  CSR bus, not just `pcie_dma0`'s own descriptor registers
- HyperRAM controller (IS66WVH8M8ALL, 8MB)
- AD9364 LVDS PHY (RX + TX, 6-bit DDR) + SPI master
- USB ULPI PHY (USB3320) — no longer "pinned out but minimal logic":
  - EP1 IN / EP2 OUT: IQ streaming (LUNA/Amaranth `usb_iq_device.v`)
  - EP3 IN / EP3 OUT: register command/response framing
  - **USB Wishbone Bridge** (`USBWishboneBridge`) — EP3 frames reach the CSR
    bus, same access AD9364 SPI/HyperRAM/XADC/DNA/etc. already have over PCIe
    and JTAG
- **IQ Stream Mux** — routes `ad9364.sink`/`.source` to PCIe DMA when the PCIe
  link is trained and up, USB otherwise; driven by live PCIe link status, no
  CSR write involved
- QSPI Flash controller (config-only, Master SPI x4 boot — not on the CSR bus)
- CRG (40 MHz TCXO → 125 MHz sys via PLL)
- JTAGBone (BSCANE2 — JTAG → Wishbone CSR bridge, ~3,400 LUT overhead)
- ICAP (remote bitstream reload)
- XADC (on-chip temperature + voltage monitoring)
- DNA (unique device serial)

Three independent Wishbone bus masters as of rev 4: `pcie_wishbone`,
`usb_wishbone`, `jtagbone` — PCIe, USB, and JTAG can each reach every CSR in
the design standalone, with no dependency on either of the other two.

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
Tracked incrementally across this build series (each step independently
synthesized and verified, not estimated):

| Change | Slice LUTs | Delta |
|---|---|---|
| PCIe DMA + AD9364, USB IQ endpoints wired but not yet muxed in | 5,012 | — |
| + IQ Stream Mux (PCIe/USB share the AD9364 stream) | 5,276 | +264 |
| + PCIe Wishbone Bridge (BAR0 &#8594; CSR bus) | 5,419 | +143 |
| + USB Wishbone Bridge (EP3 &#8594; CSR bus) | 5,773 | +354 |
| + `ad9364_reset` CSR (self-releasing register, replaces bit-2-of-`ad9364_phy_control`) | 5,770 | -3 |

Both Wishbone bridges together (PCIe + USB, giving every host interface full
CSR/register access) cost **~497 LUTs** on top of the IQ-mux baseline — modest,
well inside budget. The `ad9364_reset` change is a wash within synthesis
run-to-run noise (a few FFs' worth of logic swapped for a few FFs' worth —
not a real reduction, just how Vivado happened to pack this pass). Reusing the
original comparison's ~3,111-LUT estimate for the litex_m2sdr-exclusive
feature list (SI5351, TimeGenerator/PPSGenerator, SharedQPLL, TX/RX Header,
Loopback+Crossbar, PRBS/AGC, MultiClk, StatusLed) on top of the current 5,770,
estimated total would be roughly **~8,900 LUTs ≈ 27% of our 32,600 LUTs** —
still leaves well over half the fabric free for baseband / signal processing.

### BRAM usage difference
- Their PCIe DMA uses large descriptor FIFOs → 36 BRAM tiles.
- Our design uses 28 BRAM tiles — AsyncFIFOs (AD9364 rx/tx, HyperRAM, USB IQ,
  USB command/response, JTAGbone, PCIe MSI/TX/RX datapaths) plus the
  HyperRAM controller's own buffering.

### Tightest resource: BUFGCTRL
Clock buffers at **37.5%** (12/32) in our design — the most constrained
resource, not LUTs. `sys`, `rfic`, `usb`, and `idelay` each need one, and the
PCIe hard IP's internal MMCM contributes several more of its own — adding
another independent clock domain would need care here before LUTs become the
limiting factor.

### XC7A50T device limits (for planning)
| Resource | Total | Notes |
|---|---|---|
| Slice LUTs | 32,600 | 17.70% used by the full rev 4.1 feature set |
| Registers | 65,200 | 9.71% used |
| Block RAM tiles | 75 | 28 used now; 47 free |
| DSP48E1 | 120 | 0 used — all free |
| GTPE2_CHANNEL | 4 | 2 used (PCIe x2); 2 free |
| BUFGCTRL | 32 | 12 used now — tightest resource in the design |
| PCIE_2_1 | 1 | Used |
