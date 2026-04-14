/*
 * Spectra SDR — USB transport for SoapySDR
 *
 * Provides IQ streaming over USB 2.0 HS bulk endpoints.
 * Used as a fallback when PCIe is not available (e.g., laptop without M.2 PCIe slot).
 *
 * USB endpoint layout (defined in usb_iq_device.py):
 *   EP1 IN  (0x81) — IQ RX: FPGA → PC, 512-byte bulk packets
 *   EP2 OUT (0x02) — IQ TX: PC → FPGA, 512-byte bulk packets
 *
 * IQ data format (same as PCIe path):
 *   64 bits per sample group = 4x 16-bit signed values: [I_A, Q_A, I_B, Q_B]
 *   Each USB packet (512 bytes) carries 64 sample groups.
 *
 * Copyright (c) 2026 Hallycon Ventures.
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <libusb-1.0/libusb.h>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <mutex>

/* Spectra SDR USB identifiers (from usb_iq_device.py) */
#define SPECTRA_USB_VID        0x1209
#define SPECTRA_USB_PID        0x5380
#define SPECTRA_USB_EP_RX_IN   0x81   /* EP1 IN  — IQ RX (FPGA → PC) */
#define SPECTRA_USB_EP_TX_OUT  0x02   /* EP2 OUT — IQ TX (PC → FPGA) */
#define SPECTRA_USB_IFACE      0
#define SPECTRA_USB_TIMEOUT_MS 1000
#define SPECTRA_USB_PACKET_SIZE 512

/*
 * USB transport handle for Spectra SDR.
 *
 * This provides a streaming interface over USB bulk endpoints.
 * The IQ data format is identical to the PCIe DMA path:
 * 64-bit words, each containing 4x 16-bit signed I/Q samples.
 *
 * Throughput: USB 2.0 HS = 480 Mbps theoretical, ~40 MB/s practical
 *             → ~5 MSPS complex (16-bit I + 16-bit Q per sample)
 */
class SpectraUSB {
public:
    SpectraUSB();
    ~SpectraUSB();

    /* Open the first Spectra SDR USB device found. Returns 0 on success. */
    int open();
    void close();
    bool isOpen() const { return _devh != nullptr; }

    /* Get device serial string */
    std::string getSerial() const;

    /* Read IQ samples from EP1 IN (RX path).
     * buf: destination buffer
     * len: buffer size in bytes (should be multiple of 512)
     * actual: number of bytes actually read
     * timeout_ms: USB transfer timeout
     * Returns 0 on success, negative libusb error on failure. */
    int readIQ(uint8_t *buf, int len, int *actual, unsigned int timeout_ms = SPECTRA_USB_TIMEOUT_MS);

    /* Write IQ samples to EP2 OUT (TX path). */
    int writeIQ(const uint8_t *buf, int len, int *actual, unsigned int timeout_ms = SPECTRA_USB_TIMEOUT_MS);

    /* Find all connected Spectra SDR USB devices. Returns count. */
    static int enumerate(std::vector<std::string> &serials);

private:
    libusb_context       *_ctx  = nullptr;
    libusb_device_handle *_devh = nullptr;
    bool                  _claimed = false;
    std::mutex            _mutex;
};


/* ─────────────────────── Implementation ─────────────────────── */

inline SpectraUSB::SpectraUSB() {}

inline SpectraUSB::~SpectraUSB() {
    close();
}

inline int SpectraUSB::open() {
    if (_devh) return 0; /* already open */

    int rc = libusb_init(&_ctx);
    if (rc < 0) return rc;

    _devh = libusb_open_device_with_vid_pid(_ctx, SPECTRA_USB_VID, SPECTRA_USB_PID);
    if (!_devh) {
        libusb_exit(_ctx);
        _ctx = nullptr;
        return LIBUSB_ERROR_NO_DEVICE;
    }

    /* Detach kernel driver if attached */
    if (libusb_kernel_driver_active(_devh, SPECTRA_USB_IFACE) == 1)
        libusb_detach_kernel_driver(_devh, SPECTRA_USB_IFACE);

    rc = libusb_claim_interface(_devh, SPECTRA_USB_IFACE);
    if (rc < 0) {
        libusb_close(_devh);
        libusb_exit(_ctx);
        _devh = nullptr;
        _ctx = nullptr;
        return rc;
    }
    _claimed = true;
    return 0;
}

inline void SpectraUSB::close() {
    if (_devh) {
        if (_claimed) {
            libusb_release_interface(_devh, SPECTRA_USB_IFACE);
            _claimed = false;
        }
        libusb_close(_devh);
        _devh = nullptr;
    }
    if (_ctx) {
        libusb_exit(_ctx);
        _ctx = nullptr;
    }
}

inline std::string SpectraUSB::getSerial() const {
    if (!_devh) return "";
    libusb_device *dev = libusb_get_device(_devh);
    struct libusb_device_descriptor desc;
    if (libusb_get_device_descriptor(dev, &desc) < 0) return "";
    if (desc.iSerialNumber == 0) return "";
    unsigned char buf[64] = {};
    int len = libusb_get_string_descriptor_ascii(_devh, desc.iSerialNumber, buf, sizeof(buf));
    if (len < 0) return "";
    return std::string(reinterpret_cast<char*>(buf), len);
}

inline int SpectraUSB::readIQ(uint8_t *buf, int len, int *actual, unsigned int timeout_ms) {
    std::lock_guard<std::mutex> lock(_mutex);
    return libusb_bulk_transfer(_devh, SPECTRA_USB_EP_RX_IN, buf, len, actual, timeout_ms);
}

inline int SpectraUSB::writeIQ(const uint8_t *buf, int len, int *actual, unsigned int timeout_ms) {
    std::lock_guard<std::mutex> lock(_mutex);
    return libusb_bulk_transfer(_devh, SPECTRA_USB_EP_TX_OUT, const_cast<uint8_t*>(buf), len, actual, timeout_ms);
}

inline int SpectraUSB::enumerate(std::vector<std::string> &serials) {
    serials.clear();
    libusb_context *ctx = nullptr;
    if (libusb_init(&ctx) < 0) return 0;

    libusb_device **devs = nullptr;
    ssize_t cnt = libusb_get_device_list(ctx, &devs);
    for (ssize_t i = 0; i < cnt; i++) {
        struct libusb_device_descriptor desc;
        if (libusb_get_device_descriptor(devs[i], &desc) < 0) continue;
        if (desc.idVendor != SPECTRA_USB_VID || desc.idProduct != SPECTRA_USB_PID) continue;

        libusb_device_handle *h = nullptr;
        if (libusb_open(devs[i], &h) == 0) {
            unsigned char buf[64] = {};
            int len = 0;
            if (desc.iSerialNumber)
                len = libusb_get_string_descriptor_ascii(h, desc.iSerialNumber, buf, sizeof(buf));
            serials.push_back(len > 0 ? std::string(reinterpret_cast<char*>(buf), len) : "unknown");
            libusb_close(h);
        }
    }
    libusb_free_device_list(devs, 1);
    libusb_exit(ctx);
    return (int)serials.size();
}
