# Spectra SDR — Build & test targets

PYTHON   ?= python3
TARGET   ?= spectra_target_v2.py
BUILD_DIR = build/spectra_platform
BIT_FILE  = $(BUILD_DIR)/gateware/spectra_platform.bit
BIN_FILE  = $(BUILD_DIR)/gateware/spectra_platform.bin
CSR_CSV   = $(BUILD_DIR)/csr.csv

.PHONY: build load validate usb-verilog driver soapysdr clean help

help:
	@echo "Spectra SDR build targets:"
	@echo ""
	@echo "  Gateware:"
	@echo "    make build         — synthesize bitstream (requires Vivado)"
	@echo "    make load          — load bitstream via JTAG"
	@echo "    make usb-verilog   — regenerate usb_iq_device.v (requires amaranth+luna)"
	@echo ""
	@echo "  Validation:"
	@echo "    make validate      — run hardware validation (PCIe)"
	@echo "    make validate-jtag — run hardware validation (JTAG)"
	@echo ""
	@echo "  Host software:"
	@echo "    make driver        — build + install kernel module"
	@echo "    make soapysdr      — build + install SoapySDR plugin"
	@echo ""
	@echo "  Setup:"
	@echo "    make deps          — clone external dependencies"
	@echo "    make clean         — remove build artifacts"

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

driver:
	$(MAKE) -C software/kernel

driver-install: driver
	sudo $(MAKE) -C software/kernel install

soapysdr:
	mkdir -p software/soapysdr/build
	cd software/soapysdr/build && cmake .. && $(MAKE)

soapysdr-install: soapysdr
	cd software/soapysdr/build && sudo $(MAKE) install

deps:
	./setup_deps.sh

clean:
	rm -rf build/
	$(MAKE) -C software/kernel clean 2>/dev/null || true
	rm -rf software/soapysdr/build
