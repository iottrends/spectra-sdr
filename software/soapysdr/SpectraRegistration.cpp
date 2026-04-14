/*
 * SoapySDR driver for the Spectra SDR.
 * Adapted from enjoy-digital/litex_m2sdr.
 *
 * Copyright (c) 2021-2026 Enjoy Digital.
 * Copyright (c) 2026 Hallycon Ventures.
 * SPDX-License-Identifier: Apache-2.0
 */

#include <fcntl.h>
#include <unistd.h>

#include "LiteXM2SDRDevice.hpp"
#include "etherbone.h"

#include <SoapySDR/Registry.hpp>

/***********************************************************************
 * Device identification
 **********************************************************************/

#define MAX_DEVICES 8
#define LITEX_IDENTIFIER_SIZE 256

/* Accept both our ident string and the upstream one for compatibility */
#define SPECTRA_IDENTIFIER   "Spectra"
#define LITEX_IDENTIFIER     "LiteX-M2SDR"

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

static SoapySDR::Kwargs createDeviceKwargs(
    struct m2sdr_dev *m2sdr_dev,
    const std::string &path)
{
    SoapySDR::Kwargs dev = {
        {"device",         "Spectra SDR"},
        {"path",           path},
        {"serial",         getSpectraSerial(m2sdr_dev)},
        {"identification", getSpectraIdentification(m2sdr_dev)},
        {"version",        "1.0.0"},
        {"label",          ""},
        {"oversampling",   "0"},
    };
    dev["label"] = generateDeviceLabel(dev, path);
    return dev;
}

/***********************************************************************
 * Find available devices
 **********************************************************************/

std::vector<SoapySDR::Kwargs> findSpectra(
    const SoapySDR::Kwargs &args)
{
    std::vector<SoapySDR::Kwargs> discovered;

    auto attemptToAddDevice = [&](const std::string &path) {
        struct m2sdr_dev *dev = nullptr;
        std::string dev_id = "pcie:" + path;
        if (m2sdr_open(&dev, dev_id.c_str()) != 0)
            return false;
        auto dev_args = createDeviceKwargs(dev, path);
        m2sdr_close(dev);

        /* Match our Spectra ident OR upstream LiteX-M2SDR ident */
        const auto &ident = dev_args["identification"];
        if (ident.find(SPECTRA_IDENTIFIER) != std::string::npos ||
            ident.find(LITEX_IDENTIFIER) != std::string::npos) {
            discovered.push_back(std::move(dev_args));
            return true;
        }
        return false;
    };

    if (args.count("path") != 0) {
        attemptToAddDevice(args.at("path"));
    } else {
        /* Probe /dev/spectra0..7 then fall back to /dev/m2sdr0..7 */
        bool found = false;
        for (int i = 0; i < MAX_DEVICES; i++) {
            if (attemptToAddDevice("/dev/spectra" + std::to_string(i)))
                found = true;
            else
                break;
        }
        if (!found) {
            for (int i = 0; i < MAX_DEVICES; i++) {
                if (!attemptToAddDevice("/dev/m2sdr" + std::to_string(i)))
                    break;
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
