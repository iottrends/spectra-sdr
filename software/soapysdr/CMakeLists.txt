########################################################################
## SoapySDR plugin for Spectra SDR
########################################################################
cmake_minimum_required(VERSION 3.10)
project(SoapySpectra CXX C)

set(CMAKE_CXX_STANDARD 14)

find_package(SoapySDR CONFIG REQUIRED)

# LiteX M2SDR shared libraries (cloned by setup_deps.sh)
set(UPSTREAM_SW ${CMAKE_SOURCE_DIR}/../../litex_m2sdr/litex_m2sdr/software)
set(UPSTREAM_USER ${UPSTREAM_SW}/user)

# Path to LiteX build output (for generated CSR headers)
set(BUILD_SW ${CMAKE_SOURCE_DIR}/../../build/spectra_platform/software)

# Check that dependencies exist
if(NOT EXISTS "${UPSTREAM_USER}/liblitepcie/litepcie_dma.c")
    message(FATAL_ERROR
        "Cannot find LiteX M2SDR libraries. Run ./setup_deps.sh from repo root.")
endif()

if(NOT EXISTS "${BUILD_SW}/include/generated/csr.h")
    message(FATAL_ERROR
        "Cannot find generated CSR headers. Run 'make build' from repo root first.")
endif()

# Use LitePCIe transport (not Ethernet)
add_definitions(-DUSE_LITEPCIE=1)

include_directories(
    ${CMAKE_SOURCE_DIR}
    ${BUILD_SW}/include/generated       # csr.h, soc.h, mem.h
    ${BUILD_SW}/kernel                  # litepcie.h, config.h
    ${UPSTREAM_USER}/liblitepcie        # litepcie_dma.h, litepcie_helpers.h
    ${UPSTREAM_USER}/libm2sdr           # libm2sdr.h, m2sdr.h
    ${UPSTREAM_USER}/ad9361             # ad9361_api.h
    ${UPSTREAM_USER}/libliteeth         # etherbone.h
)

# Sources
set(SOURCES
    # Device registration
    SpectraRegistration.cpp

    # SoapySDR plugin core
    ${UPSTREAM_SW}/soapysdr/LiteXM2SDRDevice.cpp
    ${UPSTREAM_SW}/soapysdr/LiteXM2SDRStreaming.cpp

    # libm2sdr
    ${UPSTREAM_USER}/libm2sdr/m2sdr_ad9361_spi.c
    ${UPSTREAM_USER}/libm2sdr/m2sdr_si5351_i2c.c
    ${UPSTREAM_USER}/libm2sdr/m2sdr_flash.c
    ${UPSTREAM_USER}/libm2sdr/m2sdr.c

    # liblitepcie (userspace DMA)
    ${UPSTREAM_USER}/liblitepcie/litepcie_dma.c
    ${UPSTREAM_USER}/liblitepcie/litepcie_helpers.c

    # AD9361 no-OS driver (works for AD9364)
    ${UPSTREAM_USER}/ad9361/ad9361_api.c
    ${UPSTREAM_USER}/ad9361/ad9361.c
    ${UPSTREAM_USER}/ad9361/ad9361_conv.c
    ${UPSTREAM_USER}/ad9361/util.c

    # Etherbone stub (needed by libm2sdr even when unused)
    ${UPSTREAM_USER}/libliteeth/etherbone.c
)

# Build the SoapySDR module
SOAPY_SDR_MODULE_UTIL(
    TARGET SoapySpectra
    SOURCES ${SOURCES}
)
