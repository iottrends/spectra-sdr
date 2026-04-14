# Spectra SDR — Developer Experience (DX) Strategy

## Goal: Build "ESP32-like" usability for our SDR platform

---

## 1. Objective

We are not just building an SDR device.
We are building a **developer platform for wireless experimentation**.

Success criteria:

- A new user can run a working example in **< 10 seconds**
- No prior SDR or FPGA knowledge required
- Clear progression: beginner → intermediate → advanced → research

---

## 2. Core Principle

**Examples = Product Interface**

Users should not start from documentation.
They should start from working examples and modify them.

The SDK hides everything: PCIe DMA, AD9364 SPI init, BBPLL config, clock domains.
The user sees one object: `sdr`.

---

## 3. Hardware Capabilities (What the SDK Exposes)

Based on the Spectra SDR hardware:

| Capability | Value | Notes |
|---|---|---|
| Frequency range | 70 MHz – 6 GHz | AD9364 tuning range |
| Max sample rate | 61.44 MSPS | 2R2T mode, PCIe path |
| RF bandwidth | Up to 56 MHz | AD9364 analog filter |
| Channels | 2 RX + 2 TX | 2R2T (or 1R1T for single channel) |
| ADC/DAC resolution | 12-bit | AD9364, sign-extended to 16-bit in FPGA |
| Transport options | PCIe Gen2 x2 or USB 2.0 HS | auto-detected |
| PCIe throughput | ~490 MB/s per direction | full 2R2T @ 61.44 MSPS |
| USB throughput | ~45 MB/s | 1R1T @ ~5.6 MSPS |
| Duplex | Full duplex (FDD) | simultaneous TX + RX |

---

## 4. Python SDK — `hallycon` Package

### 4.1 Public API (What users see)

```python
from hallycon import SDR

sdr = SDR()                      # auto-detect transport (PCIe → USB fallback)

# Tuning
sdr.set_freq(2.4e9)              # centre frequency, Hz
sdr.set_rx_freq(2.4e9)           # RX only
sdr.set_tx_freq(2.4e9)           # TX only
sdr.set_bw(5e6)                  # RF bandwidth, Hz
sdr.set_rate(10e6)               # sample rate, sps

# Gain
sdr.set_rx_gain(40)              # dB, 0–76 dB (AD9364 range)
sdr.set_tx_gain(-10)             # dBFS, 0 to -89.75 dB
sdr.set_agc(True)                # enable AD9364 hardware AGC

# Streaming
samples = sdr.rx(1024)           # receive N samples → numpy complex64 array
sdr.tx(samples)                  # transmit numpy complex64 array
sdr.tx_repeat(samples)           # loop TX until stopped

# Channels (2R2T)
sdr.set_channels(rx=2, tx=2)     # 1 or 2 channels each direction
samples_a, samples_b = sdr.rx2(1024)   # receive both RX channels

# Info
print(sdr.info())                # transport, serial, temperature, freq, rate
sdr.close()
```

### 4.2 Internal Architecture (What the SDK hides)

```
SDR()
 │
 ├── Transport layer
 │     ├── PCIeTransport  → /dev/litepcie0 → LitePCIe DMA
 │     └── USBTransport   → USB bulk EP1/EP2 → usb_iq_device.v
 │
 ├── RFIC layer
 │     └── AD9364Driver   → SPI CSR regs → AD9364 no-OS init sequence
 │           ├── set_bbpll(ref=40e6, rate)   → DATA_CLK appears
 │           ├── set_rx_lo(freq)
 │           ├── set_tx_lo(freq)
 │           └── set_gain(channel, db)
 │
 └── Utilities
       ├── XADC           → temperature, voltage readback
       └── DNA            → unique serial number
```

### 4.3 Smart Defaults (zero config required)

| Parameter | Default | Reason |
|---|---|---|
| Centre frequency | 100 MHz (FM band) | Immediately receives real signals |
| Sample rate | 2.4 MSPS | Wide enough for FM, safe on USB |
| RF bandwidth | 5 MHz | Matches FM station spacing |
| RX gain | AGC on | No clipping, no noise floor surprises |
| Channels | 1R1T | Simpler for beginners |
| Transport | PCIe, then USB | Auto-detected at `SDR()` init |

---

## 5. Example Repository Structure

```
examples/
│
├── 01_basics/                   # < 50 lines, zero thinking required
│   ├── rx_samples.py
│   ├── plot_spectrum.py
│   ├── tx_sine.py
│   └── loopback_test.py
│
├── 02_signal_processing/        # visual outputs, adjustable params
│   ├── fm_receiver.py
│   ├── am_demod.py
│   ├── bpsk_tx_rx.py
│   ├── qpsk_constellation.py
│   └── waterfall.py
│
├── 03_applications/             # real-world, end-to-end decoding
│   ├── adsb_receiver.py
│   ├── weather_satellite.py     # NOAA APT
│   ├── spectrum_analyzer.py
│   ├── pager_decoder.py         # POCSAG
│   └── walkie_talkie.py         # simplex FM voice
│
├── 04_advanced/                 # FPGA acceleration, high throughput
│   ├── ofdm_tx_rx.py            # uses OpenOFDM gateware
│   ├── wideband_capture.py      # 56 MHz bandwidth, 61.44 MSPS
│   ├── 2r2t_mimo.py             # both channels simultaneously
│   ├── cdma_multiuser.py
│   └── adaptive_modulation.py
│
└── utils/
    ├── plotting.py              # FFT, constellation, waterfall helpers
    ├── modulation.py            # BPSK, QPSK, QAM modulators/demodulators
    ├── filters.py               # low-pass, band-pass, root-raised cosine
    └── io.py                    # save/load IQ files, sigmf format
```

---

## 6. Level 1 — Basics (No Thinking Required)

Goal: **"It works"** — user gets a signal on screen in under 10 seconds.

### `rx_samples.py`

```python
"""
Receive IQ samples from the SDR.
Tunes to FM band. Prints first 10 samples.
"""
from hallycon import SDR

sdr = SDR()
sdr.set_freq(100e6)      # FM broadcast
sdr.set_rate(2.4e6)

samples = sdr.rx(1024)
print(f"Received {len(samples)} samples")
print(f"First 5: {samples[:5]}")
print(f"Max amplitude: {abs(samples).max():.4f}")
sdr.close()
```

### `plot_spectrum.py`

```python
"""
Plot the RF spectrum around a centre frequency.
Change FREQ to tune to any signal.
"""
import numpy as np
import matplotlib.pyplot as plt
from hallycon import SDR

FREQ = 100e6      # Hz — try 433e6 (ISM), 1090e6 (ADS-B)
RATE = 2.4e6      # sample rate
N    = 4096

sdr = SDR()
sdr.set_freq(FREQ)
sdr.set_rate(RATE)

samples = sdr.rx(N)
freqs = np.fft.fftshift(np.fft.fftfreq(N, 1/RATE)) + FREQ
psd   = 20 * np.log10(np.abs(np.fft.fftshift(np.fft.fft(samples))) + 1e-12)

plt.figure(figsize=(10, 4))
plt.plot(freqs / 1e6, psd)
plt.xlabel("Frequency (MHz)")
plt.ylabel("Power (dBFS)")
plt.title(f"Spectrum @ {FREQ/1e6:.1f} MHz")
plt.grid(True)
plt.tight_layout()
plt.show()
sdr.close()
```

### `tx_sine.py`

```python
"""
Transmit a CW (sine wave) tone.
WARNING: check local regulations before transmitting.
"""
import numpy as np
from hallycon import SDR

FREQ   = 915e6     # carrier Hz
RATE   = 1e6       # sample rate
OFFSET = 100e3     # tone offset from carrier Hz
NSAMPLES = 4096

sdr = SDR()
sdr.set_freq(FREQ)
sdr.set_rate(RATE)
sdr.set_tx_gain(-20)    # start low

t = np.arange(NSAMPLES) / RATE
tone = np.exp(1j * 2 * np.pi * OFFSET * t).astype(np.complex64)
sdr.tx_repeat(tone)     # loops until Ctrl+C
sdr.close()
```

### `loopback_test.py`

```python
"""
Internal loopback test — TX → RX through AD9364 internal path.
No antenna or cable required.
Used for: verifying the board works before RF testing.
"""
import numpy as np
from hallycon import SDR

sdr = SDR()
sdr.set_loopback(True)    # enables AD9364 internal loopback
sdr.set_freq(100e6)
sdr.set_rate(1e6)

# transmit a known pattern
tx = np.exp(1j * 2 * np.pi * 0.1 * np.arange(1024)).astype(np.complex64)
sdr.tx(tx)
rx = sdr.rx(1024)

correlation = np.abs(np.correlate(rx, tx[:64]))[0]
print(f"Loopback correlation: {correlation:.2f}")
print("PASS" if correlation > 10 else "FAIL — check hardware")
sdr.set_loopback(False)
sdr.close()
```

---

## 7. Level 2 — Signal Processing

Goal: **"Understand signals"** — visual outputs, real signals.

### `fm_receiver.py`

```python
"""
FM Broadcast Receiver
Demodulates wideband FM and plays audio.
Change STATION to tune different channels.
"""
import numpy as np
import scipy.signal as sig
from hallycon import SDR
from utils.plotting import live_spectrum

STATION = 100.3e6    # MHz — change to your local FM station
RATE    = 240e3      # 240 kHz capture (10x audio)
AUDIO   = 24000      # audio output sample rate

sdr = SDR()
sdr.set_freq(STATION)
sdr.set_rate(RATE)
sdr.set_bw(200e3)

try:
    while True:
        samples = sdr.rx(4096 * 10)
        # FM demodulation: differentiate the phase
        phase = np.angle(samples)
        audio = np.diff(np.unwrap(phase))
        # Decimate to audio rate
        audio = sig.decimate(audio, int(RATE / AUDIO))
        # (pipe to sounddevice or save to wav)
except KeyboardInterrupt:
    pass

sdr.close()
```

### `qpsk_constellation.py`

```python
"""
QPSK TX/RX — Transmit and receive QPSK symbols.
Plots the received constellation.
"""
import numpy as np
import matplotlib.pyplot as plt
from hallycon import SDR
from utils.modulation import qpsk_mod, qpsk_demod

FREQ    = 915e6
RATE    = 1e6
SPS     = 8        # samples per symbol
NSYMS   = 512

sdr = SDR()
sdr.set_freq(FREQ)
sdr.set_rate(RATE)
sdr.set_loopback(True)

bits = np.random.randint(0, 2, NSYMS * 2)
tx   = qpsk_mod(bits, sps=SPS)
sdr.tx(tx.astype(np.complex64))
rx = sdr.rx(len(tx))

plt.scatter(rx.real, rx.imag, s=1, alpha=0.3)
plt.axhline(0, c='gray', lw=0.5)
plt.axvline(0, c='gray', lw=0.5)
plt.title("QPSK Constellation (loopback)")
plt.xlabel("I"); plt.ylabel("Q")
plt.grid(True); plt.axis('equal'); plt.show()

sdr.set_loopback(False)
sdr.close()
```

---

## 8. Level 3 — Applications

Goal: **"Real-world usefulness"** — decode actual signals off the air.

### `adsb_receiver.py` outline

```python
"""
ADS-B Aircraft Receiver (1090 MHz)
Decodes Mode S transponder signals from aircraft.
Prints ICAO address, callsign, altitude, position.
"""
# FREQ = 1090e6, RATE = 2e6
# detect preamble (8µs), decode 56/112 bit frames
# output: aircraft table updated live
```

### `weather_satellite.py` outline

```python
"""
NOAA Weather Satellite (APT) Receiver
Receives NOAA-15/18/19 on 137 MHz, decodes image.
Requires: satellite pass overhead (check heavens-above.com)
"""
# FREQ = 137.5e6 (NOAA-18), RATE = 11025*4
# demodulate FM, decode 2400 Hz APT subcarrier
# output: grayscale image saved as .png
```

---

## 9. Level 4 — Advanced (Platform Differentiators)

Goal: **"Show platform power"** — features only this hardware can do.

### `wideband_capture.py` outline

```python
"""
56 MHz Wideband Capture
Captures 56 MHz of spectrum at 61.44 MSPS.
Streams directly via PCIe DMA — not possible on USB-only SDRs.
"""
# sdr.set_transport("pcie")     # force PCIe
# sdr.set_rate(61.44e6)
# sdr.set_bw(56e6)
# sdr.set_channels(rx=2, tx=0) # 2R for higher throughput
# stream to file at 492 MB/s
```

### `ofdm_tx_rx.py` outline

```python
"""
OFDM TX/RX using OpenOFDM FPGA core
Baseband processing done in FPGA fabric, not Python.
PHY parameters match 802.11a: 64-FFT, 52 data subcarriers.
"""
# Uses OpenOFDM gateware (Phase 4 complete)
# Python configures TX params, receives decoded bits
# Demonstrates FPGA-accelerated PHY
```

### `2r2t_mimo.py` outline

```python
"""
2×2 MIMO Experiment
Uses both TX and RX antenna ports simultaneously.
Measures cross-channel isolation and correlation.
"""
# sdr.set_channels(rx=2, tx=2)
# sdr.set_rx_freq(2.4e9)
# tx_a, tx_b = known_pilot, known_pilot_shifted
# rx_a, rx_b = sdr.rx2(4096)
# plot channel matrix H
```

---

## 10. Utilities Module

### `utils/plotting.py`

```python
def plot_spectrum(samples, rate, freq=0, title="Spectrum"):
    """FFT plot. freq offsets the x-axis to show real frequency."""

def plot_constellation(samples, title="Constellation"):
    """I/Q scatter plot."""

def plot_waterfall(samples, rate, nrows=64, title="Waterfall"):
    """Time-frequency waterfall. rows = time, columns = frequency."""

def live_spectrum(sdr, rate, freq, update_hz=10):
    """Matplotlib animation updated from live SDR stream."""
```

### `utils/modulation.py`

```python
def bpsk_mod(bits, sps=8):      # bits → complex baseband
def bpsk_demod(samples, sps=8): # complex baseband → bits
def qpsk_mod(bits, sps=8):
def qpsk_demod(samples, sps=8):
def qam16_mod(bits, sps=8):
def qam16_demod(samples, sps=8):
def rrc_filter(sps, rolloff=0.35, ntaps=101): # root-raised cosine
```

### `utils/filters.py`

```python
def lowpass(cutoff_hz, rate, order=64):    # FIR lowpass
def bandpass(lo, hi, rate, order=64):      # FIR bandpass
def decimate(samples, factor):             # downsample with anti-alias
def resample(samples, up, down):           # rational resampling
```

### `utils/io.py`

```python
def save_iq(filename, samples, rate, freq): # save .sigmf or raw binary
def load_iq(filename):                       # load back
def record(sdr, duration_sec, filename):    # stream to file
def playback(sdr, filename):                # file → TX
```

---

## 11. Example Design Rules

| Rule | Requirement |
|---|---|
| **Single concept** | Each script demonstrates ONE idea only |
| **Instant run** | `python example.py` — no setup steps |
| **Visual output** | Every example produces at least one plot, print, or audio |
| **Copy-paste friendly** | Change `FREQ =` at the top, re-run |
| **Length limit** | Max ~100 lines (use `utils/` for shared code) |
| **Comments** | Explain *why*, not *what* |
| **Docstring** | First block: what it does, expected output, key parameters |

---

## 12. Interactive Demo Mode

```python
sdr.demo("spectrum")     # live spectrum display, auto-tunes to FM band
sdr.demo("qpsk")         # loopback QPSK, live constellation
sdr.demo("waterfall")    # scrolling waterfall, drag to tune
sdr.demo("adsb")         # ADS-B receiver, aircraft table
```

Each demo:
- Auto-configures all hardware parameters
- Runs a full pipeline
- Displays a live UI (matplotlib or textual TUI)
- Exits cleanly on Ctrl+C

---

## 13. Transport Abstraction (PCIe vs USB)

The SDK must work identically regardless of which transport is connected.
Auto-detection logic:

```
SDR.__init__()
  ├── try PCIeTransport(/dev/litepcie0)   → succeeds? use PCIe
  └── try USBTransport(vid=0x1209, pid=0x5380)  → succeeds? use USB
        └── neither found? → raise HallyconNotFoundError with helpful message
```

Behaviour differences that the SDK handles transparently:

| Aspect | PCIe | USB |
|---|---|---|
| Max sample rate | 61.44 MSPS (2R2T) | ~5.6 MSPS (1R1T) |
| Max bandwidth | 56 MHz | ~4 MHz usable |
| Latency | < 1 ms | ~8 ms (USB framing) |
| Channels | 2R2T | 1R1T auto-limited |
| Driver required | litepcie.ko | none (bulk USB) |

When USB is active and user requests > 5.6 MSPS, SDK raises a clear error:
```
HallyconBandwidthError: USB transport supports up to 5.6 MSPS.
Load the PCIe driver for full 61.44 MSPS — see docs/driver_setup.md
```

---

## 14. Packaging

```
hallycon/                      # Python package
├── __init__.py                # exposes SDR class
├── sdr.py                     # main SDR class
├── transport/
│   ├── pcie.py                # LitePCIe DMA wrapper
│   └── usb.py                 # pyusb bulk transfer wrapper
├── rfic/
│   ├── ad9364.py              # AD9364 driver (wraps no-OS or direct SPI CSR)
│   └── registers.py           # AD9364 register map constants
└── utils/
    └── csr.py                 # CSR read/write via /dev/litepcie0 or JTAGBone
```

Install:
```bash
pip install hallycon       # from PyPI (future)
# or
pip install -e .           # dev install from repo
```

---

## 15. Success Metrics

| Metric | Target |
|---|---|
| Time to first signal | < 10 seconds from `pip install` |
| Lines of code for FM receiver | < 20 |
| Level 1 examples working for new user | 100% |
| API calls to receive samples | 3 (`SDR()`, `set_freq()`, `rx()`) |
| Supported platforms | Linux (PCIe), Linux/Mac/Win (USB) |

---

## 16. Non-Goals (v1)

- No dependency on GNU Radio or SoapySDR
- No complex config files
- No exposure of FPGA internals (CSR, DMA descriptors, clock domains)
- No Windows PCIe support (Linux only for PCIe path in v1)
- No GUIs (CLI + matplotlib only in v1)

---

## 17. Roadmap

```
v0.1  SDK skeleton + PCIe transport + AD9364 init
       └── rx_samples.py works

v0.2  USB transport + auto-detection
       └── all Level 1 examples work

v0.3  Level 2 examples (FM receiver, QPSK)
       └── utils/ module complete

v0.4  Level 3 examples (ADS-B, NOAA)
       └── real signal decoding confirmed

v1.0  Full example library (50+ examples)
       └── pip install hallycon
       └── demo mode working

v1.1  OpenOFDM integration (Level 4)
       └── FPGA-accelerated OFDM demo
```

---

## 18. Summary

We are building: **"ESP32 for wireless"**

| What we ship | Why it matters |
|---|---|
| `from hallycon import SDR` | One import, zero config |
| 50+ examples across 4 levels | Users learn by running, not reading |
| PCIe + USB transport | Works in a laptop or a server |
| Smart defaults | First run succeeds without tuning |
| `sdr.demo("spectrum")` | Instant wow moment for new users |

If we execute this well:
- Hardware becomes secondary
- Platform adoption becomes primary
- Community builds more examples than we do
