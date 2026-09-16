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

cd "$ART/rq1_human_vs_agent"
"$PYTHON" analyze_table1.py
echo
echo "--- reproduced Table 1 (unique tracker hosts per task) ---"
cat expected_outputs/table1_trk_hosts.tsv
