#!/usr/bin/env bash
# Reproduce RQ1 tables and figure from the shipped per-session data.
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

echo "=== RQ1 primary paired analysis (7 agents × 4 metrics) ==="
"$PYTHON" analyze_rq1.py
echo
echo "=== Table 1 — 10-task tracker-host matrix ==="
"$PYTHON" analyze_table1.py
echo
echo "=== Table 4 — tracker-family composition ==="
"$PYTHON" analyze_table4.py
echo
echo "=== Paired-difference CDF figure ==="
"$PYTHON" plot_paired_diff.py

echo
echo "[run_repro] All RQ1 outputs in expected_outputs/ and figures/"
