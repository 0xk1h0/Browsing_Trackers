#!/usr/bin/env bash
# Reproduce Table 5 / Table 6 / Table 12 numbers from the shipped per-session data.
# Usage:   bash scripts/run_repro.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c "import numpy, scipy" 2>/dev/null; then
  echo "[run_repro] $PYTHON is missing numpy / scipy."
  echo "[run_repro] Install with: pip install -r ../requirements-repro.txt"
  exit 2
fi

echo "=== Table 6 + extended metrics (643-task) ==="
"$PYTHON" analyze_table6.py

echo
echo "=== Table 12 (Appendix F) — schema vs proxy decomposition ==="
"$PYTHON" analyze_table12.py

echo
echo "[run_repro] Reproduced cells written to data/rq3_643/ and data/rq3_layer_isolation/"
