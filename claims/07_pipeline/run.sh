#!/usr/bin/env bash
# ACSAC AE claim runner. Demonstrates that the released measurement instrument
# turns raw captured sessions back into the published per-session metrics,
# without a GPU, a proxy, a model, or network access.
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

ARCHIVE="$ART/rq1_human_vs_agent/user_study/runtime/participants_sessions.tar.xz"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "--- unpacking the released raw human sessions ---"
tar -xJf "$ARCHIVE" -C "$WORK"
echo "sessions unpacked: $(find "$WORK" -name capture.jsonl | wc -l)"

echo
echo "--- re-running the released classifier and aggregator over those raw captures ---"
"$PY" "$ART/rq1_human_vs_agent/scripts/rebuild_per_session_full.py" \
    --human-root "$WORK/sessions" --out "$WORK/rebuilt.csv"

echo
echo "--- agreement with the shipped per-session table ---"
"$PY" "$HERE/expected/compare_rebuild.py" \
    --shipped "$ART/rq1_human_vs_agent/data/per_session_full.csv" \
    --rebuilt "$WORK/rebuilt.csv"
