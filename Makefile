# Spectra SDR — Build & test targets

PYTHON   ?= python3
TARGET   ?= spectra_target_v2.py
BUILD_DIR = build/spectra_platform
BIT_FILE  = $(BUILD_DIR)/gateware/spectra_platform.bit
BIN_FILE  = $(BUILD_DIR)/gateware/spectra_platform.bin
CSR_CSV   = $(BUILD_DIR)/csr.csv

.PHONY: build load validate usb-verilog clean help

help:
	@echo "Spectra SDR build targets:"
	@echo "  make build         — synthesize bitstream (requires Vivado)"
	@echo "  make load          — load bitstream via JTAG"
	@echo "  make validate      — run hardware validation (PCIe)"
	@echo "  make validate-jtag — run hardware validation (JTAG)"
	@echo "  make usb-verilog   — regenerate usb_iq_device.v (requires amaranth+luna)"
	@echo "  make deps          — clone external dependencies"
	@echo "  make clean         — remove build artifacts"

build:
	$(PYTHON) $(TARGET) --build

load:
	$(PYTHON) $(TARGET) --load

validate:
	sudo $(PYTHON) validate_sdr.py --csr-csv $(CSR_CSV)

validate-jtag:
	$(PYTHON) validate_sdr.py --transport jtag --csr-csv $(CSR_CSV)

usb-verilog:
	$(PYTHON) usb_iq_device.py

deps:
	./setup_deps.sh

clean:
	rm -rf build/
