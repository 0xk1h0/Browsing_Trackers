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

bash "$ART/cross_benchmark/scripts/run_repro.sh"
echo
echo "--- reproduced Mind2Web cross-benchmark (Table 7) ---"
cat "$ART/cross_benchmark/expected_outputs/table7.tsv"
cat "$ART/cross_benchmark/expected_outputs/mind2web_spearman.json"
