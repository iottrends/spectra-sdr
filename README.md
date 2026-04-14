# Spectra SDR

Compact M.2 2280 Software Defined Radio built on LiteX.

- **FPGA**: Xilinx Artix-7 XC7A50T-2CSG325I
- **RFIC**: Analog Devices AD9364 (70 MHz – 6 GHz, 12-bit ADC/DAC)
- **Host interfaces**: PCIe Gen2 x2, USB 2.0 High-Speed (USB3320 ULPI)
- **Memory**: 8 MB HyperRAM, 16 MB QSPI Flash
- **Clock**: 40 MHz TCXO (2.5 ppm)

## Quick start

```bash
# 1. Clone and set up dependencies
git clone <this-repo>
cd m2sdr50t
./setup_deps.sh          # clones upstream litex_m2sdr reference

# 2. Install LiteX ecosystem (into a venv)
python3 -m venv venv && source venv/bin/activate
pip install migen litex litepcie

# 3. Build bitstream (requires Vivado)
python3 spectra_target_v2.py --build

# 4. Load and validate
python3 spectra_target_v2.py --load
sudo python3 validate_sdr.py
```

## Project structure

| File | Description |
|------|-------------|
| `spectra_platform.py` | LiteX platform — FPGA pin map, I/O standards, timing constraints |
| `spectra_target.py` | v1 SoC target — PCIe DMA ↔ AD9364 (no USB) |
| `spectra_target_v2.py` | v2 SoC target — adds USB 2.0 IQ streaming |
| `usb_iq_device.py` | Amaranth/LUNA USB bulk device generator |
| `usb_iq_device.v` | Generated Verilog (regenerate with `python3 usb_iq_device.py`) |
| `validate_sdr.py` | Post-bitstream hardware validation (XADC, DNA, SPI, DMA) |
| `setup_deps.sh` | Clones upstream litex_m2sdr reference repo |

## Architecture

```
Host PC
 ├─ PCIe Gen2 x2 ──► LitePCIe DMA ──┐
 └─ USB 2.0 HS ───► LUNA USB Core ──┤
                                     ▼
                              Stream CDC FIFOs
                                     │
                              AD9364 LVDS PHY
                                     │
                              AD9364 RFIC (RF)
```

Three clock domains: `sys` (125 MHz), `rfic` (245.76 MHz from AD9364 DATA_CLK), `usb` (60 MHz from USB3320).

See [docs/](docs/) for detailed design documents.

## License

BSD-2-Clause. Based on [litex_m2sdr](https://github.com/enjoy-digital/litex_m2sdr) by Enjoy-Digital.
