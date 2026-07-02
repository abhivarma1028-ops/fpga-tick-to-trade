#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_on_f2.sh — execute ON the f2.6xlarge instance to bring the AFI live and
# drive the tick-to-trade pipeline over BAR0, diffing against the offline golden.
#
# Prereqs on the instance (FPGA Developer AMI or Amazon Linux + FPGA mgmt tools):
#   - AWS FPGA mgmt tools installed (fpga-load-local-image / fpga-describe-local-image)
#   - this repo's f1/, sim/, host/ copied over (see launch_f2_run.sh)
#   - python3
#
# Usage:  sudo ./run_on_f2.sh [AGFI] [N] [SEED]
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

AGFI="${1:-agfi-006b5fd42e5f1cb6e}"     # our baked tick-to-trade image
N="${2:-120}"
SEED="${3:-7}"
SLOT=0

echo "== 1. clear any previously loaded image on slot $SLOT =="
sudo fpga-clear-local-image -S "$SLOT" || true

echo "== 2. load AFI $AGFI =="
sudo fpga-load-local-image -S "$SLOT" -I "$AGFI" -F

echo "== 3. confirm 'loaded' state =="
sudo fpga-describe-local-image -S "$SLOT" -R -H

echo "== 4. locate AppPF BAR0 resource file =="
# The AppPF appears under PCI vendor 0x1d0f once the image is loaded.
BAR0=""
for d in /sys/bus/pci/devices/*; do
  if [ "$(cat "$d/vendor" 2>/dev/null)" = "0x1d0f" ] && [ -s "$d/resource0" ]; then
    BAR0="$d/resource0"; echo "   candidate: $BAR0 ($(stat -c%s "$d/resource0") bytes)"
  fi
done
[ -n "$BAR0" ] || { echo "ERROR: no AppPF BAR0 found after load"; exit 1; }

echo "== 5. drive the pipeline over BAR0 and diff vs golden =="
sudo python3 f1/host_replay.py --device --n "$N" --seed "$SEED" --bar0 "$BAR0"

echo "== done =="
