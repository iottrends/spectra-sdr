# LiteX-M2SDR Platform Pinmap — Reference Extract
# Source: litex_m2sdr/litex_m2sdr_platform.py (Enjoy-Digital)
# Device: XC7A200T-SBG484-3 (Artix-7 200T, speed grade -3)

---

## 1. Board Differences vs Hallycon M2 SDR

```
┌─────────────────────┬────────────────────────┬────────────────────────────┐
│ Feature             │ LiteX-M2SDR            │ Hallycon M2 SDR v1         │
├─────────────────────┼────────────────────────┼────────────────────────────┤
│ FPGA                │ XC7A200T-SBG484-3      │ XC7A50T-CSG325-2           │
│ FPGA LUTs           │ 134,600                │ 32,600                     │
│ FPGA BRAMs          │ 365                    │ 75                         │
│ System clock        │ 100 MHz oscillator     │ 40 MHz TCXO (2.5ppm)       │
│ Clock IC            │ SI5351 (programmable)  │ None (direct TCXO)         │
│ RFIC                │ AD9361BBCZ             │ AD9364BBCZ                 │
│ RFIC interface      │ 2.5V LVDS + GPIOs      │ 2.5V LVDS only             │
│ PCIe                │ x1 / x2 / x4          │ x2 only                    │
│ USB PHY             │ None                   │ USB3320C (HS USB)          │
│ HyperRAM            │ None                   │ IS66WVH8M8ALL (8MB)        │
│ GPS PPS             │ YES (M.2 NC22/NC24)    │ No (v2 planned)            │
│ Sync GPIOs          │ 5× on M.2              │ No                         │
│ Form factor         │ Not specified          │ M.2 2280                   │
│ Bank voltages       │ 3.3V (13-16), 1.8V (34,35) │ 3.3V/2.5V/1.8V      │
└─────────────────────┴────────────────────────┴────────────────────────────┘
```

---

## 2. LiteX-M2SDR Full Pin Map

### 2.1 System Clock
```
clk100   C18   LVCMOS33   100 MHz system oscillator (SYSCLK)
```

### 2.2 LEDs / Debug
```
user_led  AB15   LVCMOS33   FPGA_LED2
debug     V13    LVCMOS33   SYNCDBG_CLK (also used as sync_clk_in)
```

### 2.3 SI5351 Clock Generator (I2C + outputs)
```
si5351_scl          AA20   LVCMOS33   SI5351_SCL  (PULLUP)
si5351_sda          AB21   LVCMOS33   SI5351_SDA  (PULLUP)
si5351_ssen_clkin   W22    LVCMOS33   SSEN(A,B) / CLKIN(C), SLEW=SLOW, DRIVE=4
si5351_pwm          W19    LVCMOS33   VCXO_TUNE_FPGA

si5351_clk0   J19   LVCMOS33   FPGA_AUXCLK_0  → Local VCTCXO @ 38.4 MHz
si5351_clk1   E19   LVCMOS33   FPGA_AUXCLK_1  → Time/PPS reference @ 100 MHz
si5351_clk2   H4    LVCMOS25   FPGA_AUXCLK_3
si5351_clk3   R4    LVCMOS25   FPGA_AUXCLK_4
```

### 2.4 QSPI Flash
```
flash_cs_n   T19   LVCMOS33
flash_mosi   P22   LVCMOS33
flash_miso   R22   LVCMOS33
flash_wp     P21   LVCMOS33
flash_hold   R21   LVCMOS33
```

### 2.5 AD9361 RFIC — LVDS Data Interface (2.5V)
```
── RX ──────────────────────────────────────────────────────────
rx_clk_p    V4                  LVDS_25  DIFF_TERM   RF_DATA_CLK_P
rx_clk_n    W4                  LVDS_25  DIFF_TERM   RF_DATA_CLK_N
rx_frame_p  AB7                 LVDS_25  DIFF_TERM   RX_FRAME_P
rx_frame_n  AB6                 LVDS_25  DIFF_TERM   RX_FRAME_N
rx_data_p   U6 W6 Y6 V7 W9 V9  LVDS_25  DIFF_TERM   RX_DATA_P0-5
rx_data_n   V5 W5 AA6 W7 Y9 V8 LVDS_25  DIFF_TERM   RX_DATA_N0-5

── TX ──────────────────────────────────────────────────────────
tx_clk_p    T5                     LVDS_25   RF_FB_CLK_P
tx_clk_n    U5                     LVDS_25   RF_FB_CLK_N
tx_frame_p  AA8                    LVDS_25   TX_FRAME_P
tx_frame_n  AB8                    LVDS_25   TX_FRAME_N
tx_data_p   U3 Y4 AB3 AA1 W1 AA5  LVDS_25   TX_DATA_P_0-5
tx_data_n   V3 AA4 AB2 AB1 Y1 AB5 LVDS_25   TX_DATA_N_0-5
```

### 2.6 AD9361 Control Signals (LVCMOS25)
```
rst_n    E1                        RF_RESET_N
enable   P4                        RF_ENABLE
txnrx    M5                        RF_RXTX
en_agc   N5                        RF_EN_AGC        ← not in Hallycon v1

ctrl     T1 U1 M3 M1               RF_CTRL_IN_0-3   ← not in Hallycon v1
stat     L1 M2 P1 R2 R3 N3 N2 N4  RF_CTRL_OUT_0-7  ← not in Hallycon v1
```

### 2.7 AD9361 SPI (LVCMOS25)
```
spi_clk    P5   RF_SPI_CLK
spi_cs_n   E2   RF_SPI_CS
spi_mosi   P6   RF_SPI_DI
spi_miso   M6   RF_SPI_DO
```

### 2.8 GPIOs
```
gpios[0]   E22   LVCMOS33   TP1 (test point, Bank16)
gpios[1]   D22   LVCMOS33   TP2 (test point, Bank16)
```

---

## 3. M.2 Connector Pin Map (Key Finding)

This is the most important section — LiteX-M2SDR defines the M.2 connector
as a named connector, mapping logical names to FPGA balls.

### 3.1 PCIe Lanes (GTP transceivers)
```
M.2 Name   FPGA Ball   M.2 Pin#   Signal
─────────────────────────────────────────────
PETn3        A4           5       PCIe_TX3_P
PETp3        B4           7       PCIe_TX3_N
PERn3        A8          11       PCIe_RX3_P
PERp3        B8          13       PCIe_RX3_N
PETn2        A6          17       PCIe_TX2_P
PETp2        B6          19       PCIe_TX2_N
PERn2        A10         23       PCIe_RX2_P
PERp2        B10         25       PCIe_RX2_N
PETn1        C5          29       PCIe_TX1_P   ← SATA-A+ in B+M spec
PETp1        D5          31       PCIe_TX1_N   ← SATA-A- in B+M spec
PERn1        C11         35       PCIe_RX1_P   ← SATA-B+ in B+M spec
PERp1        D11         37       PCIe_RX1_N   ← SATA-B- in B+M spec
PETn0        C7          41       PCIe_TX0_P
PETp0        D7          43       PCIe_TX0_N
PERn0        C9          47       PCIe_RX0_P
PERp0        D9          49       PCIe_RX0_N
REFClkn      E6          53       PCIe_REF_CLK_N
REFClkp      F6          55       PCIe_REF_CLK_P
PERSTn       A15         50       PCIe_PERST (Bank16, 3.3V)
```

### 3.2 SMBus (optional, 0Ω DNP resistors)
```
SMB_CLK   A13   M.2 pin 40   PCIe_SMCLK (Bank16, 3.3V)  — R82 not mounted
SMB_DAT   A14   M.2 pin 42   PCIe_SMDAT (Bank16, 3.3V)  — R83 not mounted
```

### 3.3 ★ Synchro / GPS / GPIO Pins (Most Interesting)

LiteX-M2SDR routes GPS PPS and sync signals to M.2 "NC" (no-connect) pins.
These are pins that standard hosts leave unconnected — making them free
for custom carrier board use.

```
M.2 Name   FPGA Ball   M.2 Pin#   Function              Bank / Voltage
──────────────────────────────────────────────────────────────────────
NC22         K18          22       PPS_IN                Bank15 / 3.3V
NC24         Y18          24       PPS_OUT               Bank14 / 3.3V
NC28         A19          28       Synchro_GPIO1         Bank16 / 3.3V
NC30         A18          30       Synchro_GPIO2         Bank16 / 3.3V
NC32         A21          32       Synchro_GPIO3         Bank16 / 3.3V
NC34         A20          34       Synchro_GPIO4         Bank16 / 3.3V
NC36         B20          36       Synchro_GPIO5         Bank16 / 3.3V
```

**PPS_IN** (pin 22): accepts 1PPS from GPS on carrier board → FPGA timestamps
**PPS_OUT** (pin 24): FPGA-generated PPS output → can drive another device
**Synchro_GPIO1-5** (pins 28-36): general purpose sync/trigger signals to carrier

This is exactly the GPS PPS approach planned for Hallycon v2.
LiteX-M2SDR confirmed it works on M.2 NC pins.

---

## 4. Baseboard Extension (_io_baseboard)

When plugged into the Acorn Baseboard Mini, extra capabilities become available
by repurposing PCIe lane 1/2 GTP transceivers:

```
sfp[0]   TX: M2:PETp2/PETn2   RX: M2:PERp2/PERn2   (PCIe lane 2 → SFP cage 0)
sfp[1]   TX: M2:PETp1/PETn1   RX: M2:PERp1/PERn1   (PCIe lane 1 → SFP cage 1)
sata[0]  TX: M2:PETp0/PETn0   RX: M2:PERp0/PERn0   (PCIe lane 0 → SATA)
```

Note: polarity swaps on some lanes — handled in platform by swapping p/n.

---

## 5. Key Takeaways for Hallycon v2

### 5.1 GPS PPS — Confirmed Viable on M.2 NC pins
LiteX-M2SDR uses M.2 pins 22/24 for PPS_IN/PPS_OUT. Our v2 plan to use
pin 7 (DSA) for GPS PPS is valid. We could also consider pins 22/24 (NC)
which they confirmed work.

### 5.2 AD9361/AD9364 GPIO Pins Missing in v1
LiteX-M2SDR connects `en_agc`, `ctrl[3:0]`, `stat[7:0]` to FPGA.
Hallycon v1 does not connect these. For v2, worth adding at minimum:
- `en_agc` — hardware AGC enable
- `stat[0]` (`LOCK_DETECT` / `ALERT`) — BBPLL lock indicator

### 5.3 SI5351 vs TCXO
They use SI5351 programmable clock gen + VCTCXO for frequency calibration.
We use a fixed 40MHz TCXO. For v2, adding a VCTCXO tune pin (DAC or PWM)
would enable software frequency correction without rebuilding the bitstream.

### 5.4 PCIe x4 Support
Their XC7A200T has 4 GTP channels → supports x4. Our XC7A50T has 4 GTP
but 2 are used for PCIe x2, 2 remain free. Not a limitation for our use case.

### 5.5 No USB on LiteX-M2SDR
They have no USB PHY at all. Our USB3320C path is a genuine differentiator —
enables standalone operation without a PCIe slot (laptop USB, Raspberry Pi, etc.).
