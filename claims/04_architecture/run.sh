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
"$PYTHON" analyze_table2.py
"$PYTHON" analyze_table3_tail.py
echo
echo "--- independent recomputation from the per-session records ---"
"$PYTHON" "$HERE/expected/recompute_table2.py"
echo
echo "--- reproduced per-agent exposure (Table 2 inputs) ---"
cat expected_outputs/table2.tsv
