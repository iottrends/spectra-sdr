# Spectra SDR — Host Software Stack

The host software provides everything needed to use the Spectra SDR with standard
SDR applications (GQRX, SDRangel, CubicSDR, GNU Radio).

## Architecture

```
SDR Applications (GQRX, SDRangel, GNU Radio)
       |
  SoapySDR API
       |
  SoapySpectra plugin          (software/soapysdr/)
  |-- AD9361 no-OS driver      (AD9364-compatible)
  |-- libspectra               (software/lib/)
  '-- liblitepcie              (generic LitePCIe userspace)
       |
  spectra.ko kernel module     (software/kernel/)
       |
  FPGA (PCIe BAR0 + DMA)
```

## Prerequisites

```bash
sudo apt install build-essential linux-headers-$(uname -r) \
    cmake libsoapysdr-dev soapysdr-tools
```

## Build order

### 1. Build the FPGA bitstream first

The LiteX build generates CSR headers needed by the kernel module and libraries:

```bash
# From repo root
make build
```

This produces `build/spectra_platform/software/` with generated headers (`csr.h`,
`config.h`, `soc.h`, `mem.h`) and kernel module source.

### 2. Kernel module

```bash
cd software/kernel
make
sudo insmod spectra.ko

# Verify
ls /dev/spectra0

# Permanent install (auto-load on boot)
sudo make install
```

### 3. SoapySDR plugin

```bash
cd software/soapysdr
mkdir build && cd build
cmake ..
make
sudo make install

# Verify SoapySDR sees the device
SoapySDRUtil --find
SoapySDRUtil --probe="driver=spectra"
```

## Quick test with GQRX

After installing the kernel module and SoapySDR plugin:

```bash
gqrx
# Select device: "Spectra SDR" from the device list
# Set frequency to 100 MHz (FM broadcast)
# Set sample rate to 30.72 MSPS
# Set gain to 40 dB
# Click play
```

## Dependencies

The SoapySDR plugin builds against the LiteX LitePCIe userspace library and
the Analog Devices AD9361 no-OS driver. These are pulled in automatically
via `setup_deps.sh` and referenced by the CMake build.
