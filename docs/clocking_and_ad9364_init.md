# Spectra SDR — Clocking Architecture & AD9364 Initialization

## 1. Clock Sources on the Board

| Oscillator | Frequency | Accuracy | Destination |
|---|---|---|---|
| S2HO40000F3CHC-T TCXO | 40 MHz | 2.5 ppm | FPGA system clock + AD9364 reference |
| Onboard 24 MHz oscillator | 24 MHz | — | USB3320C ULPI PHY (generates 60 MHz internally) |
| PCIe refclk (from host) | 100 MHz | host-dependent | GTP transceivers (PCIe only) |

The 40 MHz TCXO is the master frequency reference for both the FPGA fabric and the RF chain.
The USB and PCIe clocks are completely independent.

---

## 2. Full Clock Tree

```
40 MHz TCXO (S2HO40000F3CHC-T, 2.5 ppm)
   │
   ├─► FPGA pin P4  (clk40, IO_L12P_T1_MRCC_34, Bank 34, 1.8V)
   │       └─► S7PLL (MMCME2_ADV)
   │               ├─► cd_sys    = 125 MHz   (main fabric clock)
   │               └─► cd_idelay = 200 MHz   (IDELAYCTRL reference)
   │
   └─► AD9364 XTALP / XTALN  (analog reference input, not in FPGA pinmap)
           └─► AD9364 BBPLL  (software-configured, target = 491.52 MHz)
                   └─► DATA_CLK = 245.76 MHz  (LVDS output, pin E16/D16)
                           └─► FPGA pin E16  (IO_L14P_T2_SRCC_15, Bank 15, 2.5V)
                                   └─► IBUFDS → BUFG → cd_rfic = 245.76 MHz

24 MHz oscillator
   └─► USB3320C internal PLL → 60 MHz ULPI_CLK output (pin R3)
           └─► FPGA pin R3  (IO_L14P_T2_SRCC_34, Bank 34, 1.8V)
                   └─► BUFG → cd_usb = 60 MHz

PCIe 100 MHz refclk  (M.2 connector, from host PCIe controller)
   └─► FPGA pin D6/D5  (MGTREFCLK0P/N_216)
           └─► IBUFDS_GTE2 → GTP transceivers → PCIe PHY user clock = 125 MHz (internal)
```

---

## 3. Clock Domain Summary

| Clock Domain | Source | Frequency | FPGA Pin | Bank | Voltage |
|---|---|---|---|---|---|
| `cd_sys` | 40 MHz TCXO → PLL | 125 MHz | P4 (via PLL) | 34 | 1.8V |
| `cd_idelay` | 40 MHz TCXO → PLL | 200 MHz | — (PLL output) | — | — |
| `cd_rfic` | AD9364 DATA_CLK | 245.76 MHz | E16 (SRCC) | 15 | 2.5V |
| `cd_usb` | USB3320 ULPI_CLK | 60 MHz | R3 (SRCC) | 34 | 1.8V |
| PCIe user clk | PCIe refclk → GTP | 125 MHz | D6/D5 (MGT) | — | — |

> **Note:** `cd_rfic` frequency is software-configurable. When the AD9364 BBPLL is
> programmed for a different sample rate, DATA_CLK frequency changes accordingly.
> The timing constraint `1e9/245.76e6` in `spectra_platform.py` must match
> the programmed rate.

---

## 4. Clock Domain Relationships and CDC Strategy

| Domain Pair | Relationship | Why | CDC Mechanism | Timing Constraint |
|---|---|---|---|---|
| sys ↔ rfic | Frequency-locked | Both trace to same 40 MHz TCXO, but different PLLs/phases | AsyncFIFO (gray-coded pointers) | `set_max_delay -datapath_only 4.0 ns` (sys→rfic) / `8.0 ns` (rfic→sys) |
| sys ↔ usb | Fully asynchronous | 40 MHz TCXO vs separate 24 MHz oscillator | AsyncFIFO (gray-coded pointers) | `set_max_delay -datapath_only 8.0 ns` (both directions) |
| sys ↔ pcie | Fully asynchronous | PCIe refclk from host | PCIe PHY handles internally | Managed by Xilinx PCIe IP |
| sys ↔ clk40 | Source/derived | clk40 is PLL input, sys is PLL output | False path | `set_false_path` |

All CDC constraints are applied in `spectra_platform.py`
`do_finalize()` via `pre_placement_commands` (post-synthesis TCL, after clocks are defined).

---

## 5. Important Bring-Up Dependency: cd_rfic Requires AD9364 Init

**The `cd_rfic` clock domain does not exist until the AD9364 is initialized by software.**

- The FPGA receives DATA_CLK from the AD9364 on pin E16.
- The AD9364 only outputs DATA_CLK after its BBPLL is configured and enabled via SPI.
- Until then, pin E16 is silent — `cd_rfic` has no clock.
- The AsyncFIFO between `cd_sys` and `cd_rfic` will sit idle. It will not crash or corrupt data, but no IQ samples will flow.

**Bring-up order for v1:**

```
1. Load bitstream (JTAG or PCIe)
2. Load litepcie kernel module  →  /dev/litepcie0 appears
3. Run AD9364 SPI init sequence  →  DATA_CLK appears  →  cd_rfic starts ticking
4. IQ data flows: AD9364 → cd_rfic → AsyncFIFO → cd_sys → PCIe DMA → host
```

---

## 6. AD9364 Initialization — What Must Happen

Before any IQ data flows, userspace must send the full AD9364 init sequence over SPI.
The sequence configures approximately 300 internal registers in the correct order.

### 6.1 Key Configuration Steps

```
Step 1:  Assert / deassert reset
         → FPGA CSR: ad9364_phy_control[0] (rst_n) = 0, then 1

Step 2:  Configure BBPLL
         → Reference clock = 40 MHz (from TCXO)
         → Target BBPLL frequency = 491.52 MHz
         → Divider → DATA_CLK = 245.76 MHz  (for 2R2T at 61.44 MSPS)

Step 3:  Set baseband sample rate
         → e.g. 61.44 MSPS → DATA_CLK = 245.76 MHz  (2R2T, 4 cycles/sample)
         →      30.72 MSPS → DATA_CLK = 122.88 MHz
         → After this step DATA_CLK appears on the wire

Step 4:  Set analog RF bandwidth  (e.g. 56 MHz for full bandwidth)

Step 5:  Set Rx LO frequency      (e.g. 2.4 GHz for Wi-Fi band)
Step 6:  Set Tx LO frequency

Step 7:  Set Rx gain (manual or AGC mode)
Step 8:  Enable Rx path, enable Tx path
         → IQ data now flows
```

### 6.2 SPI Register Access via FPGA CSRs

The FPGA exposes the AD9364 SPI master as LiteX CSR registers:

| CSR Register | Address | Width | Description |
|---|---|---|---|
| `ad9364_spi_control` | `0x0804` | 32-bit RW | `[7:0]` = start (pulse), `[15:8]` = transfer length in bits |
| `ad9364_spi_status` | `0x0808` | 32-bit RO | `[0]` = done flag |
| `ad9364_spi_mosi` | `0x080C` | 32-bit RW | 24-bit SPI word to transmit |
| `ad9364_spi_miso` | `0x0810` | 32-bit RO | 24-bit SPI word received |
| `ad9364_phy_control` | `0x0800` | 32-bit RW | `[0]` = rst_n, `[1]` = loopback |

**AD9364 SPI frame format (24-bit):**

```
Bit 23:    R/~W  — 1 = read, 0 = write
Bits 22-21: W[1:0] — transfer width: 00 = 1 byte, 01 = 2 bytes, 10 = 3 bytes
Bits 20-8:  Address (13-bit)
Bits 7-0:   Data byte
```

**Example — read Product ID register (addr 0x037, expect 0x0A for AD9364):**

```python
mosi = (1 << 23) | (0x037 << 8)   # R=1, addr=0x037, data=0
bus.regs.ad9364_spi_mosi.write(mosi)
bus.regs.ad9364_spi_control.write((24 << 8) | 1)   # length=24, start=1
while not (bus.regs.ad9364_spi_status.read() & 1):  # wait for done
    pass
chip_id = bus.regs.ad9364_spi_miso.read() & 0xFF    # should be 0x0A
```

---

## 7. AD9364 Initialization — Recommended Software Path

Writing the full ~300-register init sequence by hand is error-prone.
Analog Devices provides a battle-tested open-source no-OS driver that handles everything.

### 7.1 Analog Devices no-OS AD9361 Library

```
Repository: https://github.com/analogdevicesinc/no-OS
Driver path: drivers/rf-transceiver/ad9361/
```

The library is written in C and designed to be portable to any platform.
It supports AD9361, AD9363, and AD9364 (pin-compatible, same register map).

### 7.2 Platform Integration (Thin Shim)

The no-OS library calls four platform primitives that you implement as wrappers
around the FPGA CSR registers:

```c
// spi_write_and_read() — implement using ad9364_spi_* CSRs
int32_t spi_write_and_read(struct spi_device *dev,
                            uint8_t *data, uint16_t bytes_number)
{
    uint32_t mosi = 0;
    for (int i = 0; i < bytes_number; i++)
        mosi = (mosi << 8) | data[i];

    litepcie_csr_write(AD9364_SPI_MOSI, mosi);
    litepcie_csr_write(AD9364_SPI_CONTROL, (bytes_number * 8) << 8 | 1);
    while (!(litepcie_csr_read(AD9364_SPI_STATUS) & 1));  // wait done
    uint32_t miso = litepcie_csr_read(AD9364_SPI_MISO);
    for (int i = bytes_number - 1; i >= 0; i--) {
        data[i] = miso & 0xFF;
        miso >>= 8;
    }
    return 0;
}

// udelay() — use nanosleep or usleep
void udelay(uint32_t usecs) { usleep(usecs); }

// gpio_set_value() — drive rst_n, enable, txnrx via phy_control CSR
void gpio_set_value(struct gpio_device *dev, uint8_t gpio, uint8_t val) {
    // map gpio number to phy_control bits
}
```

Total shim code: approximately 50–80 lines of C.
Everything else — all 300+ register writes, PLL calibration, gain tables — is handled
by `ad9361_init()` in the no-OS library.

### 7.3 Init Parameter Structure

```c
struct ad9361_init_param init_param = {
    /* Reference clock */
    .reference_clk_rate        = 40000000UL,   // 40 MHz TCXO

    /* Sample rate */
    .rx_path_clock_frequencies = {983040000, 245760000, 122880000,
                                   61440000, 30720000, 30720000},
    .tx_path_clock_frequencies = {983040000, 245760000, 122880000,
                                   61440000, 30720000, 30720000},

    /* RF bandwidth */
    .rf_rx_bandwidth_hz        = 56000000,     // 56 MHz
    .rf_tx_bandwidth_hz        = 56000000,

    /* LO frequencies */
    .rx_synthesizer_frequency_hz = 2400000000ULL,  // 2.4 GHz
    .tx_synthesizer_frequency_hz = 2400000000ULL,

    /* 2R2T mode */
    .rx2tx2                    = 1,
    .tdd_use_dual_synth        = 0,

    /* FDD mode (TX and RX simultaneous) */
    .frequency_division_duplex_mode_enable = 1,
};
```

### 7.4 Software Stack (End Goal)

```
Application (GNU Radio, custom)
       ↓
  SoapySDR API
       ↓
  SoapyAD9361 plugin   (wraps no-OS lib, exposes gain/freq/rate controls)
       ↓
  AD9361 no-OS library  (ad9361_init, ad9361_set_rx_lo_freq, ...)
       ↓
  Platform shim         (spi_write_and_read → FPGA CSR registers)
       ↓
  litepcie CSR bridge   (/dev/litepcie0, ioctl)
       ↓
  FPGA SPI master       (ad9364_spi_* CSRs)
       ↓
  AD9364 RFIC           (via 4-wire SPI)
```

---

## 8. DATA_CLK Frequency vs Sample Rate Reference

| Sample rate (per channel) | BBPLL | DATA_CLK | Mode | cd_rfic |
|---|---|---|---|---|
| 61.44 MSPS | 491.52 MHz | 245.76 MHz | 2R2T (4 cycles/sample) | 245.76 MHz |
| 30.72 MSPS | 491.52 MHz | 122.88 MHz | 2R2T | 122.88 MHz |
| 61.44 MSPS | 491.52 MHz | 122.88 MHz | 1R1T (2 cycles/sample) | 122.88 MHz |
| 20.00 MSPS | 320.00 MHz | 160.00 MHz | 2R2T | 160.00 MHz |

When changing sample rate, update the timing constraint in `spectra_platform.py`:

```python
self.rfic_clk_freq = 245.76e6   # change this to match programmed DATA_CLK
```

and rebuild the bitstream, or use `set_max_delay` conservatively enough to cover all
intended rates (e.g. `set_max_delay 8.0 ns` covers DATA_CLK down to 125 MHz).

---

## 9. Validation Checklist

Use `validate_sdr.py` for automated checks. Manual checklist:

- [ ] Bitstream loaded (JTAG or flash)
- [ ] `/dev/litepcie0` present (litepcie module loaded)
- [ ] XADC: temperature 0–85 °C, VCCINT ~1.0V, VCCAUX ~1.8V
- [ ] DNA ID: non-zero (confirms FPGA is alive and CSR bridge works)
- [ ] AD9364 SPI: reg 0x037 reads 0x0A (product ID, confirms RFIC powered and SPI wired correctly)
- [ ] AD9364 init: BBPLL locked (reg 0x5E bit 0), Rx/Tx synthesizer locked
- [ ] DATA_CLK present: `cd_rfic` ticking (can verify via clock measurement CSR if added)
- [ ] IQ data: non-zero samples arriving via PCIe DMA (tune to FM broadcast band for easy signal)
