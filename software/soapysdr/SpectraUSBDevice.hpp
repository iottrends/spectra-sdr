/*
 * Spectra SDR — SoapySDR device implementation over USB 2.0
 *
 * This provides a complete SoapySDR Device that streams IQ data
 * over USB bulk endpoints using libusb. It is used when the board
 * is connected via USB instead of PCIe.
 *
 * Limitations vs PCIe:
 *   - USB 2.0 HS: ~40 MB/s → ~5 MSPS max (vs 61.44 MSPS on PCIe Gen2 x2)
 *   - RX and TX IQ are both supported (EP1 IN / EP2 OUT), full duplex —
 *     actual combined throughput still shares the one USB 2.0 HS link
 *   - Register/CSR access is available separately via the gateware's USB
 *     EP3 control bridge (see validate_sdr.py Step 11), but this SoapySDR
 *     device class only talks to EP1/EP2 for IQ; it doesn't call EP3
 *
 * Copyright (c) 2026 Hallycon Ventures.
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <SoapySDR/Device.hpp>
#include <SoapySDR/Logger.hpp>
#include <SoapySDR/Formats.hpp>
#include <SoapySDR/Types.hpp>

#include "SpectraUSB.hpp"

#include <thread>
#include <atomic>
#include <vector>
#include <cstring>
#include <cmath>

/* IQ data format on the USB bus (same as PCIe DMA path):
 *
 *   64 bits = [I_A:16][Q_A:16][I_B:16][Q_B:16]
 *   Each USB packet (512 bytes) = 64 sample groups
 *
 *   In 1R1T mode: I_B/Q_B are duplicates, use I_A/Q_A only
 *   In 2R2T mode: A = channel 0, B = channel 1
 *
 *   Samples are 16-bit signed, range [-2048, +2047] (12-bit ADC sign-extended)
 */

#define USB_RX_BUF_COUNT    32
#define USB_RX_BUF_SIZE     (512 * 64)  /* 32 KB per buffer (64 USB packets) */
#define USB_SAMPLES_PER_BUF (USB_RX_BUF_SIZE / 4)  /* 4 bytes per complex sample */

class SoapySpectraUSB : public SoapySDR::Device {
public:
    SoapySpectraUSB(const SoapySDR::Kwargs &args);
    ~SoapySpectraUSB();

    /* Identification */
    std::string getDriverKey() const override { return "spectra-usb"; }
    std::string getHardwareKey() const override { return "Spectra SDR (USB)"; }

    /* Channels */
    size_t getNumChannels(const int direction) const override {
        return 1;   /* RX and TX both available over EP1 IN / EP2 OUT */
    }
    bool getFullDuplex(const int, const size_t) const override { return true; }

    /* Stream format */
    std::string getNativeStreamFormat(const int, const size_t, double &fullScale) const override {
        fullScale = 2048.0;
        return SOAPY_SDR_CS16;
    }
    std::vector<std::string> getStreamFormats(const int, const size_t) const override {
        return {SOAPY_SDR_CS16, SOAPY_SDR_CF32};
    }

    /* Stream API */
    SoapySDR::Stream *setupStream(
        const int direction,
        const std::string &format,
        const std::vector<size_t> &channels,
        const SoapySDR::Kwargs &args) override;

    void closeStream(SoapySDR::Stream *stream) override;

    int activateStream(SoapySDR::Stream *stream, const int flags,
                       const long long timeNs, const size_t numElems) override;

    int deactivateStream(SoapySDR::Stream *stream,
                         const int flags, const long long timeNs) override;

    size_t getStreamMTU(SoapySDR::Stream *stream) const override {
        return USB_SAMPLES_PER_BUF;
    }

    int readStream(SoapySDR::Stream *stream, void * const *buffs,
                   const size_t numElems, int &flags, long long &timeNs,
                   const long timeoutUs) override;

    int writeStream(SoapySDR::Stream *stream, const void * const *buffs,
                    const size_t numElems, int &flags, const long long timeNs,
                    const long timeoutUs) override;

    /* Frequency API (informational — actual tuning via ad9364_init.py or PCIe/JTAG) */
    void setFrequency(int direction, size_t channel, double frequency,
                      const SoapySDR::Kwargs &args) override {
        if (direction == SOAPY_SDR_RX) _rxFreq = frequency;
        else                           _txFreq = frequency;
    }
    double getFrequency(const int direction, const size_t channel,
                        const std::string &name) const override {
        return (direction == SOAPY_SDR_RX) ? _rxFreq : _txFreq;
    }
    std::vector<std::string> listFrequencies(const int, const size_t) const override {
        return {"RF"};
    }
    SoapySDR::RangeList getFrequencyRange(const int, const size_t,
                                           const std::string &) const override {
        return {SoapySDR::Range(70e6, 6e9)};
    }

    /* Sample rate API */
    void setSampleRate(const int direction, const size_t, const double rate) override {
        if (direction == SOAPY_SDR_RX) _rxRate = rate;
        else                           _txRate = rate;
    }
    double getSampleRate(const int direction, const size_t) const override {
        return (direction == SOAPY_SDR_RX) ? _rxRate : _txRate;
    }
    std::vector<double> listSampleRates(const int, const size_t) const override {
        return {1e6, 2e6, 2.5e6, 3e6, 4e6, 5e6};
    }
    SoapySDR::RangeList getSampleRateRange(const int, const size_t) const override {
        return {SoapySDR::Range(500e3, 5.6e6)};  /* USB HS throughput limit, both directions share it */
    }

    /* Gain API */
    std::vector<std::string> listGains(const int, const size_t) const override {
        return {"RF"};
    }
    void setGain(int direction, size_t channel, const double value) override {
        if (direction == SOAPY_SDR_RX) _rxGain = value;
        else                           _txGain = value;
    }
    double getGain(const int direction, const size_t channel) const override {
        return (direction == SOAPY_SDR_RX) ? _rxGain : _txGain;
    }
    SoapySDR::Range getGainRange(const int, const size_t) const override {
        return SoapySDR::Range(0.0, 76.0);
    }

    /* Bandwidth */
    SoapySDR::RangeList getBandwidthRange(const int, const size_t) const override {
        return {SoapySDR::Range(200e3, 56e6)};
    }

    /* Antenna */
    std::vector<std::string> listAntennas(const int direction, const size_t) const override {
        return {(direction == SOAPY_SDR_RX) ? "RX" : "TX"};
    }
    std::string getAntenna(const int direction, const size_t) const override {
        return (direction == SOAPY_SDR_RX) ? "RX" : "TX";
    }

private:
    SpectraUSB _usb;
    SoapySDR::Kwargs _args;

    double _rxFreq = 100e6, _txFreq = 100e6;
    double _rxRate = 2.5e6, _txRate = 2.5e6;
    double _rxGain = 40.0,  _txGain = 0.0;

    /* Stream state — RX and TX are independent streams, distinguished by
     * the opaque handle returned from setupStream() (0x1 = RX, 0x2 = TX). */
    bool _rxStreamActive = false;
    bool _txStreamActive = false;
    std::string _rxStreamFormat;
    std::string _txStreamFormat;

    /* RX read buffer — USB delivers 32KB bursts, app asks for arbitrary N,
     * so RX needs local buffering between the two. */
    std::vector<uint8_t> _rxBuf;
    size_t _rxBufOffset = 0;   /* current read position in bytes */
    size_t _rxBufValid = 0;    /* valid bytes in buffer */

    /* TX scratch buffer — no local buffering needed, just format conversion
     * before handing the whole call straight to a single bulk_transfer(). */
    std::vector<uint8_t> _txBuf;
};


/* ─────────────── Implementation ─────────────── */

inline SoapySpectraUSB::SoapySpectraUSB(const SoapySDR::Kwargs &args)
    : _args(args)
{
    int rc = _usb.open();
    if (rc != 0)
        throw std::runtime_error("Failed to open Spectra SDR USB device");
    SoapySDR::logf(SOAPY_SDR_INFO, "Spectra SDR USB connected (serial: %s)",
                   _usb.getSerial().c_str());
}

inline SoapySpectraUSB::~SoapySpectraUSB() {
    _usb.close();
}

inline SoapySDR::Stream *SoapySpectraUSB::setupStream(
    const int direction,
    const std::string &format,
    const std::vector<size_t> &channels,
    const SoapySDR::Kwargs &args)
{
    if (direction != SOAPY_SDR_RX && direction != SOAPY_SDR_TX)
        throw std::runtime_error("Spectra USB: unsupported direction");
    if (format != SOAPY_SDR_CS16 && format != SOAPY_SDR_CF32)
        throw std::runtime_error("Spectra USB: unsupported format " + format);

    if (direction == SOAPY_SDR_RX) {
        _rxStreamFormat = format;
        _rxBuf.resize(USB_RX_BUF_SIZE);
        _rxBufOffset = 0;
        _rxBufValid = 0;
        return (SoapySDR::Stream *)0x1;
    }

    _txStreamFormat = format;
    return (SoapySDR::Stream *)0x2;
}

inline void SoapySpectraUSB::closeStream(SoapySDR::Stream *stream) {
    if (stream == (SoapySDR::Stream *)0x1) {
        _rxStreamActive = false;
        _rxBuf.clear();
    } else if (stream == (SoapySDR::Stream *)0x2) {
        _txStreamActive = false;
        _txBuf.clear();
    }
}

inline int SoapySpectraUSB::activateStream(SoapySDR::Stream *stream,
    const int flags, const long long timeNs, const size_t numElems)
{
    if (stream == (SoapySDR::Stream *)0x1) {
        _rxStreamActive = true;
        _rxBufOffset = 0;
        _rxBufValid = 0;
    } else if (stream == (SoapySDR::Stream *)0x2) {
        _txStreamActive = true;
    }
    return 0;
}

inline int SoapySpectraUSB::deactivateStream(SoapySDR::Stream *stream,
    const int flags, const long long timeNs)
{
    if (stream == (SoapySDR::Stream *)0x1)      _rxStreamActive = false;
    else if (stream == (SoapySDR::Stream *)0x2) _txStreamActive = false;
    return 0;
}

inline int SoapySpectraUSB::readStream(SoapySDR::Stream *stream,
    void * const *buffs, const size_t numElems, int &flags,
    long long &timeNs, const long timeoutUs)
{
    if (stream != (SoapySDR::Stream *)0x1 || !_rxStreamActive)
        return SOAPY_SDR_STREAM_ERROR;

    size_t produced = 0;

    while (produced < numElems) {
        /* Refill buffer from USB if empty */
        if (_rxBufOffset >= _rxBufValid) {
            int actual = 0;
            unsigned int tmo = (timeoutUs > 0) ? (timeoutUs / 1000 + 1) : 1000;
            int rc = _usb.readIQ(_rxBuf.data(), USB_RX_BUF_SIZE, &actual, tmo);
            if (rc == LIBUSB_ERROR_TIMEOUT)
                return (produced > 0) ? (int)produced : SOAPY_SDR_TIMEOUT;
            if (rc < 0)
                return SOAPY_SDR_STREAM_ERROR;
            _rxBufValid = actual;
            _rxBufOffset = 0;
            if (actual == 0) continue;
        }

        /* How many samples available in current buffer? */
        size_t availBytes = _rxBufValid - _rxBufOffset;
        size_t availSamples = availBytes / 4;
        size_t take = std::min(availSamples, numElems - produced);

        if (_rxStreamFormat == SOAPY_SDR_CS16) {
            /* Direct copy — USB data is already CS16 */
            memcpy((int16_t*)buffs[0] + produced * 2,
                   _rxBuf.data() + _rxBufOffset, take * 4);
        } else {
            /* Convert CS16 → CF32 */
            const int16_t *src = (const int16_t*)(_rxBuf.data() + _rxBufOffset);
            float *dst = (float*)buffs[0] + produced * 2;
            for (size_t i = 0; i < take * 2; i++)
                dst[i] = src[i] / 2048.0f;
        }

        _rxBufOffset += take * 4;
        produced += take;
    }

    return (int)produced;
}

inline int SoapySpectraUSB::writeStream(SoapySDR::Stream *stream,
    const void * const *buffs, const size_t numElems, int &flags,
    const long long timeNs, const long timeoutUs)
{
    if (stream != (SoapySDR::Stream *)0x2 || !_txStreamActive)
        return SOAPY_SDR_STREAM_ERROR;

    /* Pack numElems complex samples into the wire's 8-byte-per-group format:
     * [I_A:16][Q_A:16][I_B:16][Q_B:16]. This device exposes one logical
     * channel, so I_A/Q_A is duplicated into I_B/Q_B — correct as-is for
     * 1R1T mode (where B is defined to mirror A) and harmless in 2R2T mode
     * (channel B just repeats channel A rather than carrying anything). */
    _txBuf.resize(numElems * 8);
    int16_t *dst = (int16_t *)_txBuf.data();

    if (_txStreamFormat == SOAPY_SDR_CS16) {
        const int16_t *src = (const int16_t *)buffs[0];
        for (size_t i = 0; i < numElems; i++) {
            int16_t I = src[i * 2 + 0];
            int16_t Q = src[i * 2 + 1];
            dst[i * 4 + 0] = I; dst[i * 4 + 1] = Q;   /* channel A */
            dst[i * 4 + 2] = I; dst[i * 4 + 3] = Q;   /* channel B (mirror) */
        }
    } else {
        const float *src = (const float *)buffs[0];
        for (size_t i = 0; i < numElems; i++) {
            int16_t I = (int16_t)std::lround(std::max(-1.0f, std::min(1.0f, src[i * 2 + 0])) * 2048.0f);
            int16_t Q = (int16_t)std::lround(std::max(-1.0f, std::min(1.0f, src[i * 2 + 1])) * 2048.0f);
            dst[i * 4 + 0] = I; dst[i * 4 + 1] = Q;
            dst[i * 4 + 2] = I; dst[i * 4 + 3] = Q;
        }
    }

    int actual = 0;
    unsigned int tmo = (timeoutUs > 0) ? (timeoutUs / 1000 + 1) : 1000;
    int rc = _usb.writeIQ(_txBuf.data(), (int)_txBuf.size(), &actual, tmo);
    if (rc == LIBUSB_ERROR_TIMEOUT)
        return SOAPY_SDR_TIMEOUT;
    if (rc < 0)
        return SOAPY_SDR_STREAM_ERROR;

    return (int)(actual / 8);   /* samples actually accepted by the device */
}
