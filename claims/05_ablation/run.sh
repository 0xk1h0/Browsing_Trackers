#!/usr/bin/env bash
# ACSAC AE claim runner. Resolves the artifact tree, then runs the module that
# produces this claim's evidence and prints the relevant output.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ART="$ROOT/artifact"; [ -d "$ART" ] || ART="$ROOT"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"
  elif [ -x "$ART/.venv/bin/python" ]; then PY="$ART/.venv/bin/python"
  else PY=python3; fi
fi
export PYTHON="$PY"

bash "$ART/rq3_ablation/scripts/run_repro.sh"
echo
echo "--- reproduced per-cell ablation metrics (Table 6 inputs) ---"
cat "$ART/rq3_ablation/data/rq3_643/full_metrics_per_cell.csv"
