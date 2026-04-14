/*
 * SoapySDR driver for the Spectra SDR.
 *
 * Device discovery: probes PCIe first, then USB.
 * PCIe takes precedence when both are available (higher throughput).
 *
 * PCIe Gen2 x2: ~8 Gbps → 61.44 MSPS full rate
 * USB 2.0 HS:   ~40 MB/s → ~5 MSPS (sufficient for narrowband)
 *
 * Copyright (c) 2026 Hallycon Ventures.
 * SPDX-License-Identifier: Apache-2.0
 */

#include <fcntl.h>
#include <unistd.h>

#include "LiteXM2SDRDevice.hpp"
#include "SpectraUSB.hpp"
#include "SpectraUSBDevice.hpp"
#include "etherbone.h"

#include <SoapySDR/Registry.hpp>

/***********************************************************************
 * Constants
 **********************************************************************/

#define MAX_DEVICES 8
#define LITEX_IDENTIFIER_SIZE 256

/* Accepted ident strings */
#define SPECTRA_IDENTIFIER   "Spectra"
#define LITEX_IDENTIFIER     "LiteX-M2SDR"

/***********************************************************************
 * PCIe device helpers
 **********************************************************************/

static std::string readFPGAData(
    struct m2sdr_dev *dev,
    unsigned int baseAddr,
    size_t size)
{
    std::string data(size, 0);
    for (size_t i = 0; i < size; i++)
        data[i] = static_cast<char>(litex_m2sdr_readl(dev, baseAddr + 4 * i));
    return data;
}

static std::string getSpectraIdentification(struct m2sdr_dev *dev)
{
    std::string data = readFPGAData(dev, CSR_IDENTIFIER_MEM_BASE, LITEX_IDENTIFIER_SIZE);
    size_t nullPos = data.find('\0');
    if (nullPos != std::string::npos)
        data.resize(nullPos);
    return data;
}

static std::string getSpectraSerial(struct m2sdr_dev *dev)
{
    unsigned int high = litex_m2sdr_readl(dev, CSR_DNA_ID_ADDR + 0);
    unsigned int low  = litex_m2sdr_readl(dev, CSR_DNA_ID_ADDR + 4);
    char serial[32];
    snprintf(serial, sizeof(serial), "%x%08x", high, low);
    return std::string(serial);
}

static std::string generateDeviceLabel(
    const SoapySDR::Kwargs &dev,
    const std::string &path)
{
    std::string serialTrimmed = dev.at("serial").substr(
        dev.at("serial").find_first_not_of('0'));
    return dev.at("device") + " " + path + " " + serialTrimmed;
}

/***********************************************************************
 * Find available devices — PCIe first, USB fallback
 **********************************************************************/

std::vector<SoapySDR::Kwargs> findSpectra(
    const SoapySDR::Kwargs &args)
{
    std::vector<SoapySDR::Kwargs> discovered;

    /* ── 1. Probe PCIe devices (preferred — Gen2 x2, ~8 Gbps) ────── */

    auto attemptPCIe = [&](const std::string &path) -> bool {
        struct m2sdr_dev *dev = nullptr;
        std::string dev_id = "pcie:" + path;
        if (m2sdr_open(&dev, dev_id.c_str()) != 0)
            return false;

        SoapySDR::Kwargs dev_args = {
            {"device",         "Spectra SDR"},
            {"driver",         "spectra"},
            {"transport",      "pcie"},
            {"path",           path},
            {"serial",         getSpectraSerial(dev)},
            {"identification", getSpectraIdentification(dev)},
            {"version",        "1.0.0"},
            {"label",          ""},
            {"oversampling",   "0"},
        };
        m2sdr_close(dev);

        const auto &ident = dev_args["identification"];
        if (ident.find(SPECTRA_IDENTIFIER) != std::string::npos ||
            ident.find(LITEX_IDENTIFIER) != std::string::npos) {
            dev_args["label"] = generateDeviceLabel(dev_args, "PCIe:" + path);
            discovered.push_back(std::move(dev_args));
            return true;
        }
        return false;
    };

    if (args.count("path") != 0) {
        attemptPCIe(args.at("path"));
    } else {
        for (int i = 0; i < MAX_DEVICES; i++) {
            if (!attemptPCIe("/dev/spectra" + std::to_string(i)))
                break;
        }
        if (discovered.empty()) {
            for (int i = 0; i < MAX_DEVICES; i++) {
                if (!attemptPCIe("/dev/m2sdr" + std::to_string(i)))
                    break;
            }
        }
    }

    /* ── 2. Probe USB devices (fallback — HS, ~5 MSPS) ───────────── */

    /* Skip USB probe if user explicitly requested a PCIe path */
    if (args.count("path") == 0 || args.at("path").find("usb") != std::string::npos) {
        std::vector<std::string> usb_serials;
        int usb_count = SpectraUSB::enumerate(usb_serials);

        for (int i = 0; i < usb_count; i++) {
            /* Check if this serial is already discovered via PCIe */
            bool already_found = false;
            for (const auto &d : discovered) {
                if (d.count("serial") && d.at("serial").find(usb_serials[i]) != std::string::npos) {
                    already_found = true;
                    break;
                }
            }

            if (!already_found) {
                std::string usb_path = "usb:" + std::to_string(i);
                SoapySDR::Kwargs dev_args = {
                    {"device",         "Spectra SDR"},
                    {"driver",         "spectra"},
                    {"transport",      "usb"},
                    {"path",           usb_path},
                    {"serial",         usb_serials[i]},
                    {"identification", "Spectra SDR (USB)"},
                    {"version",        "1.0.0"},
                    {"label",          "Spectra SDR USB " + usb_serials[i]},
                    {"oversampling",   "0"},
                };
                discovered.push_back(std::move(dev_args));
            }
        }
    }

    return discovered;
}

/***********************************************************************
 * Make device instance
 **********************************************************************/

SoapySDR::Device *makeSpectra(const SoapySDR::Kwargs &args)
{
    /* USB transport — lightweight device using libusb bulk transfers */
    if (args.count("transport") && args.at("transport") == "usb")
        return new SoapySpectraUSB(args);

    /* PCIe transport — full-featured device via litepcie DMA */
    return new SoapyLiteXM2SDR(args);
}

/***********************************************************************
 * Registration
 **********************************************************************/

static SoapySDR::Registry registerSpectra(
    "spectra",
    &findSpectra,
    &makeSpectra,
    SOAPY_SDR_ABI_VERSION);
