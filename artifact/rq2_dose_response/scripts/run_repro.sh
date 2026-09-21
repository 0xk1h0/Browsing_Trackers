#!/usr/bin/env bash
# Reproduce RQ2 tables and figures from the shipped per-session data.
# Usage:   bash scripts/run_repro.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c "import numpy, scipy, matplotlib" 2>/dev/null; then
  echo "[run_repro] $PYTHON is missing numpy / scipy / matplotlib."
  echo "[run_repro] Install with: pip install -r ../requirements-repro.txt"
  exit 2
fi

echo "=== Table 2 — per-agent 643-task exposure ==="
"$PYTHON" analyze_table2.py
echo
echo "=== Table 3 — action-space affordance bins ==="
"$PYTHON" analyze_table3.py
echo
echo "=== Table 3 — affordances and deep-navigation tail share ==="
"$PYTHON" analyze_table3_tail.py
echo
echo "=== Figure 3 — dose-response (3 panels) ==="
"$PYTHON" plot_dose_response.py
echo
echo "=== §6.2 — RTB broadcast on 50-session upper-quartile sample ==="
"$PYTHON" analyze_rtb.py
echo
echo "=== §6 — CMP consent decisions ==="
"$PYTHON" analyze_cmp.py

echo
echo "[run_repro] All RQ2 outputs written to expected_outputs/ and figures/"
