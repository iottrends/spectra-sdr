# Spectra SDR — Quick Start Guide

Get your Spectra SDR board running in 15 minutes.

## What you need

- Spectra SDR M.2 card installed in a PCIe M.2 slot (or connected via JTAG)
- Linux host (Ubuntu 22.04+ recommended)
- Vivado 2025.2+ (for building bitstream) **OR** a pre-built `.bit` file
- JTAG adapter (Digilent HS2 or compatible) for initial bring-up

## Step 1: Build or obtain the bitstream

### Option A: Build from source (requires Vivado)

```bash
git clone https://github.com/iottrends/spectra-sdr.git
cd spectra-sdr

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

make build    # takes ~15-30 minutes
```

Output: `build/spectra_platform/gateware/spectra_platform.bit`

### Option B: Use pre-built bitstream

Check [Releases](https://github.com/iottrends/spectra-sdr/releases) for pre-built `.bit` files.

## Step 2: Load the bitstream

### Via JTAG (recommended for first boot)

```bash
make load
# or manually:
openFPGALoader -c digilent_hs2 build/spectra_platform/gateware/spectra_platform.bit
```

### Via QSPI flash (persistent across reboots)

```bash
openFPGALoader -c digilent_hs2 --write-flash build/spectra_platform/gateware/spectra_platform.bin
```

## Step 3: Validate the hardware

### Via JTAG (no driver needed)

```bash
# Terminal 1: start the JTAG-to-Wishbone bridge
litex_server --jtag --jtag-config openocd_xc7_ft2232.cfg

# Terminal 2: run validation
make validate-jtag
```

### Via PCIe (after driver is loaded)

```bash
# Build and load the litepcie kernel module
cd build/spectra_platform/software/kernel
make
sudo insmod litepcie.ko

# Verify device appeared
ls /dev/litepcie0

# Run validation
make validate
```

### Expected output

```
╔══════════════════════════════════════════════════╗
║         Spectra SDR — Hardware Validation          ║
╚══════════════════════════════════════════════════╝

  [PASS]  SoC ident string  Spectra SDR SoC
  [PASS]  Temperature in range (0–85 °C)  42.3 °C
  [PASS]  VCCINT  nominal 1.0 V  (±8%)  1.003 V
  [PASS]  VCCAUX  nominal 1.8 V  (±6%)  1.802 V
  [PASS]  VCCBRAM nominal 1.0 V  (±8%)  1.003 V
  [PASS]  DNA ID readable and non-zero  0x1234567890ABCDEF
  [PASS]  Walking ones pattern (64 words)  OK
  [PASS]  Walking zeros pattern (64 words)  OK
  [PASS]  Random pattern (64 words)  OK
  [PASS]  Product ID (0x037 → 0x0A), after reset  got 0x0A, expected 0x0A
  [PASS]  Chip variant  AD9364 (ID=0x0A, rev=0x02)
  [PASS]  SPI write/readback (6/6 patterns)  all matched
  [PASS]  LED blink  LEDs toggled (check board visually)

  ALL TESTS PASSED — your Spectra SDR is ready.
```

## Step 4: Initialize the AD9364 and stream IQ data

After validation passes, bring up the RF:

```bash
# Initialize AD9364: tune to 100 MHz FM band, 40 dB gain
python3 scripts/ad9364_init.py --transport jtag --rx-lo 100 --gain 40

# Or via PCIe:
sudo python3 scripts/ad9364_init.py --rx-lo 100 --gain 40
```

You should see all PLLs lock:

```
  [OK] Chip detected  (AD9364 rev 2)
  [OK] BBPLL locked  (int=12, frac=0x49BA5E, target=491.52 MHz)
  [OK] RX synth locked  (LO=100.0 MHz, VCO=÷64)
  [OK] TX synth locked  (LO=100.0 MHz, VCO=÷64)

  AD9364 initialized — IQ data should now be flowing.
```

IQ samples are now streaming through the PCIe DMA engine. To capture them,
use the litepcie test utilities:

```bash
cd build/spectra_platform/software/user
make
./litepcie_util dma_test    # basic DMA throughput test
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| No `/dev/litepcie0` | Driver not loaded | `sudo insmod litepcie.ko` |
| SPI timeout | AD9364 not powered or SPI bus issue | Check 2.5V supply on Bank 15 |
| BBPLL won't lock | Bad TCXO or wrong dividers | Verify 40 MHz clock on pin P4 |
| RX synth won't lock | LO out of range | AD9364 supports 70 MHz – 6 GHz |
| HyperRAM fails | RAM not soldered or 1.8V issue | Check Bank 34 power |
| DNA reads zero | Bitstream not loaded | Reload via JTAG |

## Next steps

- **Full AD9364 init**: For production, integrate the [Analog Devices no-OS library](https://github.com/analogdevicesinc/no-OS) — see [clocking doc](clocking_and_ad9364_init.md) for the shim code.
- **GNU Radio**: Build a SoapySDR plugin to use the board with GNU Radio.
- **USB streaming**: The v2 target includes USB 2.0 HS — connect via USB for lower-bandwidth applications without PCIe.
