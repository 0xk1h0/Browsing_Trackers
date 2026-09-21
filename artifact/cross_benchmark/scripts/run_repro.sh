#!/usr/bin/env bash
# Reproduce Mind2Web cross-benchmark Table 7 from the shipped per-session data.
# Usage:   bash scripts/run_repro.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c "import sys" 2>/dev/null; then
  echo "[run_repro] no python3 found"
  exit 2
fi

echo "=== Table 7 — Mind2Web cross-benchmark (300 tasks × 7 agents = 4,200 sessions) ==="
"$PYTHON" analyze_table7.py

echo
echo "[run_repro] Outputs in expected_outputs/"
