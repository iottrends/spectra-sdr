# Spectra SDR — Linux kernel module
#
# The LiteX build generates kernel source + CSR headers in:
#   build/spectra_platform/software/kernel/
#
# This Makefile copies those generated files, patches the device name,
# and builds the module.

KERNEL_VERSION := $(shell uname -r)
KERNEL_PATH ?= /lib/modules/$(KERNEL_VERSION)/build

# LiteX build output directory
BUILD_SW_DIR ?= ../../build/spectra_platform/software

# Module name
obj-m = spectra.o
spectra-objs = main.o

# Default target
all: spectra.ko

# Copy generated sources from LiteX build, patch device name
prepare:
	@if [ ! -d "$(BUILD_SW_DIR)/kernel" ]; then \
		echo "ERROR: LiteX build output not found at $(BUILD_SW_DIR)/kernel"; \
		echo "Run 'make build' from the repo root first."; \
		exit 1; \
	fi
	cp $(BUILD_SW_DIR)/kernel/main.c .
	cp $(BUILD_SW_DIR)/kernel/*.h .
	cp $(BUILD_SW_DIR)/include/generated/*.h . 2>/dev/null || true
	@# Patch device name from litepcie default to spectra
	sed -i 's/#define LITEPCIE_NAME.*/#define LITEPCIE_NAME "spectra"/' main.c

spectra.ko: prepare
	make -C $(KERNEL_PATH) M=$(shell pwd) modules

install:
	make -C $(KERNEL_PATH) M=$(shell pwd) modules_install
	@echo spectra | sudo tee /etc/modules-load.d/spectra.conf
	sudo depmod -a
	@echo 'KERNEL=="spectra*", MODE="0666"' | sudo tee /etc/udev/rules.d/99-spectra.rules
	sudo udevadm control --reload-rules && sudo udevadm trigger

uninstall:
	sudo rmmod spectra || true
	sudo rm -f /lib/modules/$(KERNEL_VERSION)/updates/spectra.ko
	sudo rm -f /etc/modules-load.d/spectra.conf
	sudo rm -f /etc/udev/rules.d/99-spectra.rules
	sudo depmod

clean:
	make -C $(KERNEL_PATH) M=$(shell pwd) clean 2>/dev/null || true
	rm -f main.c *.h *~

.PHONY: all prepare clean install uninstall
