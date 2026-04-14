#!/usr/bin/env python3
"""
Spectra SDR — Minimal AD9364 Initialization

Brings up the AD9364 RFIC to a known-good state so IQ data flows.
This is a minimal register-level init — for production use, integrate
the Analog Devices no-OS library instead (see docs/clocking_and_ad9364_init.md).

Usage:
  # Via JTAG (litex_server must be running):
  python3 scripts/ad9364_init.py --transport jtag

  # Via PCIe:
  sudo python3 scripts/ad9364_init.py

Default config:
  - Sample rate: 30.72 MSPS (conservative, works on all variants)
  - RX LO: 100 MHz (FM broadcast band — easy to verify with antenna)
  - RX gain: 40 dB (manual)
  - Mode: 1R1T FDD

After running this script, DATA_CLK will appear and IQ samples will flow
through the PCIe DMA or USB path.
"""

import os
import sys
import time
import argparse

# ──────────────────────────────────────────────────────────────────────────────
# SPI primitives (same as validate_sdr.py)
# ──────────────────────────────────────────────────────────────────────────────

def spi_read(bus, addr):
    """Read one byte from AD9364 register at addr."""
    mosi_val = (1 << 23) | ((addr & 0x1FFF) << 8)
    bus.regs.ad9364_spi_mosi.write(mosi_val)
    bus.regs.ad9364_spi_control.write((24 << 8) | 1)
    timeout = time.time() + 0.5
    while not (bus.regs.ad9364_spi_status.read() & 1):
        if time.time() > timeout:
            raise TimeoutError(f"SPI read timeout at addr 0x{addr:03X}")
        time.sleep(0.001)
    return bus.regs.ad9364_spi_miso.read() & 0xFF


def spi_write(bus, addr, data):
    """Write one byte to AD9364 register at addr."""
    mosi_val = (0 << 23) | ((addr & 0x1FFF) << 8) | (data & 0xFF)
    bus.regs.ad9364_spi_mosi.write(mosi_val)
    bus.regs.ad9364_spi_control.write((24 << 8) | 1)
    timeout = time.time() + 0.5
    while not (bus.regs.ad9364_spi_status.read() & 1):
        if time.time() > timeout:
            raise TimeoutError(f"SPI write timeout at addr 0x{addr:03X}")
        time.sleep(0.001)


def spi_rmw(bus, addr, mask, val):
    """Read-modify-write: read register, clear bits in mask, OR in val."""
    cur = spi_read(bus, addr)
    spi_write(bus, addr, (cur & ~mask) | (val & mask))


# ──────────────────────────────────────────────────────────────────────────────
# AD9364 register addresses (subset needed for minimal init)
# Reference: AD9361 Register Map, UG-570
# ──────────────────────────────────────────────────────────────────────────────

# General
REG_SPI_CONF        = 0x000  # SPI configuration
REG_PRODUCT_ID      = 0x037  # Product ID (0x0A = AD9364)
REG_DEVICE_REV      = 0x005  # Silicon revision

# BBPLL
REG_BBPLL_REF_CLK   = 0x045  # Reference clock scaler
REG_BBPLL_FRACT_BB  = 0x048  # BBPLL fractional word [7:0]
REG_BBPLL_FRACT_BB1 = 0x049  # BBPLL fractional word [15:8]
REG_BBPLL_FRACT_BB2 = 0x04A  # BBPLL fractional word [23:16]
REG_BBPLL_INTEGER_BB= 0x04B  # BBPLL integer divider [7:0]
REG_BBPLL_CONFIG    = 0x04E  # BBPLL enable/lock
REG_BBPLL_LOCK      = 0x05E  # BBPLL lock status [0]

# Clock dividers
REG_CLOCK_CTRL      = 0x009  # Clock enable/select
REG_BBPLL_DIVIDER   = 0x00A  # BBPLL output divider

# RX synthesizer
REG_RX_SYNTH_POWER  = 0x232  # RX synth power down control
REG_RX_SYNTH_VCO    = 0x233  # RX VCO output divider
REG_RX_SYNTH_FRACT0 = 0x234  # RX synth fractional [7:0]
REG_RX_SYNTH_FRACT1 = 0x235  # RX synth fractional [15:8]
REG_RX_SYNTH_FRACT2 = 0x236  # RX synth fractional [23:16]
REG_RX_SYNTH_INTEGER= 0x237  # RX synth integer divider
REG_RX_SYNTH_LOCK   = 0x247  # RX synth lock detect [1]

# RX gain
REG_RX_GAIN_CTL     = 0x0FA  # RX gain control mode
REG_RX1_GAIN_INDEX  = 0x109  # RX1 gain table index
REG_RX_FULL_TABLE   = 0x0FC  # Full gain table select

# TX synthesizer (mirrors RX layout)
REG_TX_SYNTH_POWER  = 0x272  # TX synth power down control
REG_TX_SYNTH_VCO    = 0x273
REG_TX_SYNTH_FRACT0 = 0x274
REG_TX_SYNTH_FRACT1 = 0x275
REG_TX_SYNTH_FRACT2 = 0x276
REG_TX_SYNTH_INTEGER= 0x277
REG_TX_SYNTH_LOCK   = 0x287

# Ensm (Enable State Machine)
REG_ENSM_CONFIG_1   = 0x014  # ENSM configuration
REG_ENSM_CONFIG_2   = 0x015  # FDD/TDD mode


# ──────────────────────────────────────────────────────────────────────────────
# Colour helpers
# ──────────────────────────────────────────────────────────────────────────────

def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text

OK   = lambda t: _c("32;1", t)
ERR  = lambda t: _c("31;1", t)
WARN = lambda t: _c("33;1", t)
INFO = lambda t: _c("36;1", t)
BOLD = lambda t: _c("1", t)


def step(msg):
    print(f"  {INFO('>>>')} {msg}")


def check(label, condition, detail=""):
    status = OK("OK") if condition else ERR("FAIL")
    d = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{d}")
    if not condition:
        print(f"       {ERR('Init cannot continue.')}")
        sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# Init sequence
# ──────────────────────────────────────────────────────────────────────────────

def reset_ad9364(bus):
    """Hard reset the AD9364 via FPGA control pin."""
    step("Asserting AD9364 reset...")
    bus.regs.ad9364_phy_control.write(0x00)   # rst_n = 0
    time.sleep(0.01)
    bus.regs.ad9364_phy_control.write(0x01)   # rst_n = 1
    time.sleep(0.05)                           # wait for boot


def verify_chip(bus):
    """Confirm AD9364 is responding."""
    chip_id = spi_read(bus, REG_PRODUCT_ID)
    rev     = spi_read(bus, REG_DEVICE_REV)
    names = {0x0A: "AD9364", 0x08: "AD9361", 0x06: "AD9363"}
    name = names.get(chip_id, f"Unknown(0x{chip_id:02X})")
    check("Chip detected", chip_id in names, f"{name} rev {rev}")
    return chip_id


def configure_spi_mode(bus):
    """Set SPI to single-byte mode, MSB first."""
    step("Configuring SPI mode...")
    spi_write(bus, REG_SPI_CONF, 0x18)  # MSB first, single byte, 4-wire


def configure_bbpll(bus, ref_clk_hz=40e6, target_hz=491.52e6):
    """Configure the BBPLL to produce the desired frequency from the reference clock.

    The BBPLL uses an integer + fractional divider:
        BBPLL_freq = ref_clk * (integer + fractional/2^24)

    We need:
        491.52 MHz / 40 MHz = 12.288
        integer = 12, fractional = 0.288 * 2^24 = 4831838.208 ≈ 4831838
    """
    step(f"Configuring BBPLL: {ref_clk_hz/1e6:.0f} MHz -> {target_hz/1e6:.2f} MHz...")

    ratio = target_hz / ref_clk_hz
    integer_part = int(ratio)
    frac_part = int((ratio - integer_part) * (1 << 24))

    # Reference clock scaler (divide-by-1 for 40 MHz)
    spi_write(bus, REG_BBPLL_REF_CLK, 0x08)

    # Fractional word (24-bit, little-endian across 3 registers)
    spi_write(bus, REG_BBPLL_FRACT_BB,  (frac_part >>  0) & 0xFF)
    spi_write(bus, REG_BBPLL_FRACT_BB1, (frac_part >>  8) & 0xFF)
    spi_write(bus, REG_BBPLL_FRACT_BB2, (frac_part >> 16) & 0xFF)

    # Integer divider
    spi_write(bus, REG_BBPLL_INTEGER_BB, integer_part & 0xFF)

    # Enable BBPLL
    spi_write(bus, REG_BBPLL_CONFIG, 0x06)  # enable + reset calibration
    time.sleep(0.01)
    spi_write(bus, REG_BBPLL_CONFIG, 0x04)  # release calibration reset

    # Wait for lock
    time.sleep(0.05)
    lock = spi_read(bus, REG_BBPLL_LOCK) & 0x01
    check("BBPLL locked", lock == 1,
          f"int={integer_part}, frac=0x{frac_part:06X}, target={target_hz/1e6:.2f} MHz")


def configure_clocks(bus):
    """Enable baseband clock path and set dividers for ~30.72 MSPS."""
    step("Configuring clock dividers...")
    # Enable all clock domains
    spi_write(bus, REG_CLOCK_CTRL, 0xFF)
    # BBPLL output divider: divide by 2 for ADC/DAC clock
    spi_write(bus, REG_BBPLL_DIVIDER, 0x01)


def configure_rx_synth(bus, lo_hz=100e6, ref_clk_hz=40e6):
    """Program the RX synthesizer (LO) frequency.

    The RX synth uses a VCO at 2x-64x the LO frequency (in bands).
    VCO range: ~6 GHz to ~12 GHz.
    Synth frequency = ref_clk * (integer + fractional/2^24) * 2

    For 100 MHz LO:
        VCO divider = 64 → VCO target = 6400 MHz
        Synth ratio = 6400 / (2 * 40) = 80.0
        integer = 80, fractional = 0
    """
    step(f"Programming RX LO to {lo_hz/1e6:.1f} MHz...")

    # Select VCO divider band
    vco_dividers = [2, 4, 8, 16, 32, 64]
    vco_div = None
    for d in vco_dividers:
        vco_freq = lo_hz * d
        if 6e9 <= vco_freq <= 12.4e9:
            vco_div = d
            break
    if vco_div is None:
        # Fallback: use largest divider
        vco_div = 64
        print(f"       {WARN('WARNING')}: LO {lo_hz/1e6:.1f} MHz may be out of VCO range")

    vco_freq = lo_hz * vco_div
    synth_ratio = vco_freq / (2 * ref_clk_hz)
    integer_part = int(synth_ratio)
    frac_part = int((synth_ratio - integer_part) * (1 << 24))

    # VCO divider register encoding
    vco_div_map = {2: 0, 4: 1, 8: 2, 16: 3, 32: 4, 64: 5}
    spi_write(bus, REG_RX_SYNTH_VCO, (vco_div_map[vco_div] << 4) | 0x08)

    # Fractional + integer
    spi_write(bus, REG_RX_SYNTH_FRACT0, (frac_part >>  0) & 0xFF)
    spi_write(bus, REG_RX_SYNTH_FRACT1, (frac_part >>  8) & 0xFF)
    spi_write(bus, REG_RX_SYNTH_FRACT2, (frac_part >> 16) & 0xFF)
    spi_write(bus, REG_RX_SYNTH_INTEGER, integer_part & 0xFF)

    # Power up RX synth
    spi_write(bus, REG_RX_SYNTH_POWER, 0x00)
    time.sleep(0.05)

    lock = (spi_read(bus, REG_RX_SYNTH_LOCK) >> 1) & 0x01
    check("RX synth locked", lock == 1,
          f"LO={lo_hz/1e6:.1f} MHz, VCO=÷{vco_div}, int={integer_part}, frac=0x{frac_part:06X}")


def configure_tx_synth(bus, lo_hz=100e6, ref_clk_hz=40e6):
    """Program the TX synthesizer — same algorithm as RX."""
    step(f"Programming TX LO to {lo_hz/1e6:.1f} MHz...")

    vco_dividers = [2, 4, 8, 16, 32, 64]
    vco_div = None
    for d in vco_dividers:
        vco_freq = lo_hz * d
        if 6e9 <= vco_freq <= 12.4e9:
            vco_div = d
            break
    if vco_div is None:
        vco_div = 64

    vco_freq = lo_hz * vco_div
    synth_ratio = vco_freq / (2 * ref_clk_hz)
    integer_part = int(synth_ratio)
    frac_part = int((synth_ratio - integer_part) * (1 << 24))

    vco_div_map = {2: 0, 4: 1, 8: 2, 16: 3, 32: 4, 64: 5}
    spi_write(bus, REG_TX_SYNTH_VCO, (vco_div_map[vco_div] << 4) | 0x08)
    spi_write(bus, REG_TX_SYNTH_FRACT0, (frac_part >>  0) & 0xFF)
    spi_write(bus, REG_TX_SYNTH_FRACT1, (frac_part >>  8) & 0xFF)
    spi_write(bus, REG_TX_SYNTH_FRACT2, (frac_part >> 16) & 0xFF)
    spi_write(bus, REG_TX_SYNTH_INTEGER, integer_part & 0xFF)

    spi_write(bus, REG_TX_SYNTH_POWER, 0x00)
    time.sleep(0.05)

    lock = (spi_read(bus, REG_TX_SYNTH_LOCK) >> 1) & 0x01
    check("TX synth locked", lock == 1,
          f"LO={lo_hz/1e6:.1f} MHz, VCO=÷{vco_div}")


def configure_rx_gain(bus, gain_index=40):
    """Set RX gain to manual mode with a fixed gain index."""
    step(f"Setting RX gain index to {gain_index}...")
    spi_write(bus, REG_RX_FULL_TABLE, 0x00)     # full gain table
    spi_write(bus, REG_RX_GAIN_CTL, 0x00)        # manual gain control
    spi_write(bus, REG_RX1_GAIN_INDEX, gain_index & 0x7F)


def enable_datapath(bus):
    """Enable the ENSM (Enable State Machine) for FDD RX+TX."""
    step("Enabling FDD datapath...")
    spi_write(bus, REG_ENSM_CONFIG_2, 0x05)  # FDD mode, dual synth
    spi_write(bus, REG_ENSM_CONFIG_1, 0x0F)  # enable RX + TX + FDD


def read_status(bus):
    """Read and display final status."""
    print()
    print(BOLD("  Final status:"))
    bbpll_lock = spi_read(bus, REG_BBPLL_LOCK) & 0x01
    rx_lock    = (spi_read(bus, REG_RX_SYNTH_LOCK) >> 1) & 0x01
    tx_lock    = (spi_read(bus, REG_TX_SYNTH_LOCK) >> 1) & 0x01
    print(f"    BBPLL lock : {'YES' if bbpll_lock else 'NO'}")
    print(f"    RX PLL lock: {'YES' if rx_lock else 'NO'}")
    print(f"    TX PLL lock: {'YES' if tx_lock else 'NO'}")
    return bbpll_lock and rx_lock and tx_lock


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    print()
    print(INFO("═══════════════════════════════════════════════════"))
    print(INFO("        Spectra SDR — AD9364 Initialization        "))
    print(INFO("═══════════════════════════════════════════════════"))
    print()

    parser = argparse.ArgumentParser(description="Minimal AD9364 init for Spectra SDR")
    parser.add_argument("--transport", choices=["pcie", "jtag"], default="pcie")
    parser.add_argument("--csr-csv", default=None)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", default=1234, type=int)
    parser.add_argument("--rx-lo", type=float, default=100.0,
                        help="RX LO frequency in MHz (default: 100)")
    parser.add_argument("--tx-lo", type=float, default=100.0,
                        help="TX LO frequency in MHz (default: 100)")
    parser.add_argument("--gain", type=int, default=40,
                        help="RX gain index 0-76 (default: 40)")
    parser.add_argument("--skip-reset", action="store_true",
                        help="Skip AD9364 hardware reset")
    args = parser.parse_args()

    rx_lo_hz = args.rx_lo * 1e6
    tx_lo_hz = args.tx_lo * 1e6

    # Locate csr.csv
    if args.csr_csv:
        csr_csv = args.csr_csv
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.dirname(script_dir)
        candidates = [
            os.path.join(root_dir, "csr.csv"),
            os.path.join(root_dir, "build", "spectra_platform", "csr.csv"),
        ]
        csr_csv = next((p for p in candidates if os.path.exists(p)), None)
        if csr_csv is None:
            print(ERR("Cannot find csr.csv. Pass --csr-csv <path>."))
            sys.exit(1)

    print(f"  Transport : {args.transport.upper()}")
    print(f"  CSR CSV   : {csr_csv}")
    print(f"  RX LO     : {args.rx_lo:.1f} MHz")
    print(f"  TX LO     : {args.tx_lo:.1f} MHz")
    print(f"  RX gain   : {args.gain}")
    print()

    # Connect
    step("Connecting to LiteX CSR bridge...")
    try:
        from litex import RemoteClient
        bus = RemoteClient(host=args.host, port=args.port, csr_csv=csr_csv)
        bus.open()
        check("Connected", True, f"{args.host}:{args.port}")
    except Exception as e:
        print(ERR(f"  Cannot connect: {e}"))
        sys.exit(1)
    print()

    # Init sequence
    if not args.skip_reset:
        reset_ad9364(bus)
    verify_chip(bus)
    configure_spi_mode(bus)
    print()

    configure_bbpll(bus)
    configure_clocks(bus)
    print()

    configure_rx_synth(bus, lo_hz=rx_lo_hz)
    configure_tx_synth(bus, lo_hz=tx_lo_hz)
    print()

    configure_rx_gain(bus, gain_index=args.gain)
    enable_datapath(bus)

    all_locked = read_status(bus)
    bus.close()

    print()
    if all_locked:
        print(OK("  AD9364 initialized — IQ data should now be flowing."))
        print(f"  RX tuned to {args.rx_lo:.1f} MHz, gain index {args.gain}.")
    else:
        print(ERR("  WARNING: one or more PLLs did not lock."))
        print("  Check antenna/RF connections and try different LO frequency.")
    print()


if __name__ == "__main__":
    main()
