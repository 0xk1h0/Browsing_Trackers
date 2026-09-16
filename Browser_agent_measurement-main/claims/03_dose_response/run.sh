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

cd "$ART/rq2_dose_response"
"$PYTHON" analyze_table3.py
"$PYTHON" plot_dose_response.py
"$PYTHON" analyze_paired.py
echo
echo "--- dose-response bins (the file this claim is checked against) ---"
cat expected_outputs/table3.tsv
echo
echo "--- per-agent paired summary ---"
cat expected_outputs/paired_per_agent.tsv
