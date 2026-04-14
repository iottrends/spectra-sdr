/*
 * Spectra SDR — SoapySDR device implementation over USB 2.0
 *
 * This provides a complete SoapySDR Device that streams IQ data
 * over USB bulk endpoints using libusb. It is used when the board
 * is connected via USB instead of PCIe.
 *
 * Limitations vs PCIe:
 *   - USB 2.0 HS: ~40 MB/s → ~5 MSPS max (vs 61.44 MSPS on PCIe Gen2 x2)
 *   - No CSR access over USB (AD9364 must be pre-initialized via JTAG)
 *   - RX only for now (TX support can be added via EP2 OUT)
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
        return (direction == SOAPY_SDR_RX) ? 1 : 0;
    }
    bool getFullDuplex(const int, const size_t) const override { return false; }

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

    /* Frequency API (informational — actual tuning via ad9364_init.py or PCIe/JTAG) */
    void setFrequency(int direction, size_t channel, double frequency,
                      const SoapySDR::Kwargs &args) override {
        if (direction == SOAPY_SDR_RX) _rxFreq = frequency;
    }
    double getFrequency(const int direction, const size_t channel,
                        const std::string &name) const override {
        return _rxFreq;
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
    }
    double getSampleRate(const int direction, const size_t) const override {
        return _rxRate;
    }
    std::vector<double> listSampleRates(const int, const size_t) const override {
        return {1e6, 2e6, 2.5e6, 3e6, 4e6, 5e6};
    }
    SoapySDR::RangeList getSampleRateRange(const int, const size_t) const override {
        return {SoapySDR::Range(500e3, 5.6e6)};  /* USB HS throughput limit */
    }

    /* Gain API */
    std::vector<std::string> listGains(const int, const size_t) const override {
        return {"RF"};
    }
    void setGain(int direction, size_t channel, const double value) override {
        if (direction == SOAPY_SDR_RX) _rxGain = value;
    }
    double getGain(const int direction, const size_t channel) const override {
        return _rxGain;
    }
    SoapySDR::Range getGainRange(const int, const size_t) const override {
        return SoapySDR::Range(0.0, 76.0);
    }

    /* Bandwidth */
    SoapySDR::RangeList getBandwidthRange(const int, const size_t) const override {
        return {SoapySDR::Range(200e3, 56e6)};
    }

    /* Antenna */
    std::vector<std::string> listAntennas(const int, const size_t) const override {
        return {"RX"};
    }
    std::string getAntenna(const int, const size_t) const override { return "RX"; }

private:
    SpectraUSB _usb;
    SoapySDR::Kwargs _args;

    double _rxFreq = 100e6;
    double _rxRate = 2.5e6;
    double _rxGain = 40.0;

    /* Stream state */
    bool _streamActive = false;
    std::string _streamFormat;

    /* Read buffer */
    std::vector<uint8_t> _rxBuf;
    size_t _rxBufOffset = 0;   /* current read position in bytes */
    size_t _rxBufValid = 0;    /* valid bytes in buffer */
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
    if (direction != SOAPY_SDR_RX)
        throw std::runtime_error("Spectra USB: only RX supported");
    if (format != SOAPY_SDR_CS16 && format != SOAPY_SDR_CF32)
        throw std::runtime_error("Spectra USB: unsupported format " + format);

    _streamFormat = format;
    _rxBuf.resize(USB_RX_BUF_SIZE);
    _rxBufOffset = 0;
    _rxBufValid = 0;
    return (SoapySDR::Stream *)0x1;
}

inline void SoapySpectraUSB::closeStream(SoapySDR::Stream *stream) {
    _streamActive = false;
    _rxBuf.clear();
}

inline int SoapySpectraUSB::activateStream(SoapySDR::Stream *stream,
    const int flags, const long long timeNs, const size_t numElems)
{
    _streamActive = true;
    _rxBufOffset = 0;
    _rxBufValid = 0;
    return 0;
}

inline int SoapySpectraUSB::deactivateStream(SoapySDR::Stream *stream,
    const int flags, const long long timeNs)
{
    _streamActive = false;
    return 0;
}

inline int SoapySpectraUSB::readStream(SoapySDR::Stream *stream,
    void * const *buffs, const size_t numElems, int &flags,
    long long &timeNs, const long timeoutUs)
{
    if (!_streamActive) return SOAPY_SDR_STREAM_ERROR;

    /* Bytes per complex sample: CS16 = 4 bytes (I16 + Q16), CF32 = 8 bytes */
    const size_t bytesPerSample = (_streamFormat == SOAPY_SDR_CS16) ? 4 : 8;
    const size_t wantBytes = numElems * 4;  /* USB always delivers 4 bytes/sample (CS16) */
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

        if (_streamFormat == SOAPY_SDR_CS16) {
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
