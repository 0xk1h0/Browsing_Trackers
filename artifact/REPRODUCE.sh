#!/usr/bin/env bash
# REPRODUCE.sh — one-shot reproduction of all paper tables and figures.
#
# Runs every per-RQ `scripts/run_repro.sh` in sequence. Tier-1 only — no GPU,
# no mitmproxy, no API calls. Expected wall-clock: ~5 minutes on a laptop.
#
# Usage:
#   bash REPRODUCE.sh                  # all RQs
#   bash REPRODUCE.sh rq3              # just RQ3
#   bash REPRODUCE.sh rq1 cross        # multi-select
#
# Set PYTHON=/path/to/python to override the interpreter (default: python3).
# Outputs land under each rq*/expected_outputs/ and figures/.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c "import numpy, scipy, matplotlib" 2>/dev/null; then
  echo "[REPRODUCE] $PYTHON is missing numpy / scipy / matplotlib." >&2
  echo "[REPRODUCE] Install with: pip install -r requirements-repro.txt" >&2
  exit 2
fi

MODULES_ALL=(rq1_human_vs_agent rq2_dose_response rq3_ablation cross_benchmark)

selected=()
if [[ $# -gt 0 ]]; then
  for tag in "$@"; do
    case "$tag" in
      rq1)    selected+=("rq1_human_vs_agent") ;;
      rq2)    selected+=("rq2_dose_response") ;;
      rq3)    selected+=("rq3_ablation") ;;
      cross|m2w|mind2web) selected+=("cross_benchmark") ;;
      *)
        if [[ -d "$tag" ]]; then selected+=("$tag")
        else echo "[REPRODUCE] unknown module: $tag" >&2; exit 3
        fi ;;
    esac
  done
else
  selected=("${MODULES_ALL[@]}")
fi

fail=0
for module in "${selected[@]}"; do
  script="$module/scripts/run_repro.sh"
  if [[ ! -x "$script" ]]; then
    echo "[REPRODUCE] missing or non-executable: $script (skip)" >&2
    fail=1
    continue
  fi
  echo
  echo "================================================================"
  echo "  $module"
  echo "================================================================"
  PYTHON="$PYTHON" bash "$script" || { fail=1; echo "[REPRODUCE] $module FAILED" >&2; }
done

echo
if [[ $fail -eq 0 ]]; then
  echo "[REPRODUCE] All modules reproduced successfully."
  echo "[REPRODUCE] Run: $PYTHON $HERE/verify.py   (to diff against shipped reference values)"
else
  echo "[REPRODUCE] One or more modules failed — see error output above." >&2
  exit 1
fi
