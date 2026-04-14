#!/bin/bash
# Spectra SDR — dependency setup
# Clones the upstream LiteX M2SDR reference design used during development.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LITEX_M2SDR_REPO="https://github.com/enjoy-digital/litex_m2sdr"
LITEX_M2SDR_COMMIT="9289957e3dd3e3b03d10c1254054e29970d278cc"

if [ -d "$SCRIPT_DIR/litex_m2sdr" ]; then
    echo "litex_m2sdr/ already exists, updating..."
    cd "$SCRIPT_DIR/litex_m2sdr"
    git fetch origin
else
    echo "Cloning litex_m2sdr..."
    git clone "$LITEX_M2SDR_REPO" "$SCRIPT_DIR/litex_m2sdr"
    cd "$SCRIPT_DIR/litex_m2sdr"
fi

git checkout "$LITEX_M2SDR_COMMIT"
echo "litex_m2sdr pinned at $LITEX_M2SDR_COMMIT"
