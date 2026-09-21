#!/usr/bin/env bash
# ACSAC 2026 artifact evaluation — automated installation.
#
# Creates a Python environment and installs the four pinned dependencies needed
# to reproduce every table and figure in the paper. No GPU, no proxy, no API
# keys. Expected time: about 30 seconds.
#
# Usage:
#   bash install.sh            # reproduction dependencies (this is what AE needs)
#   bash install.sh --full     # additionally install the collection pipeline
#                              # (vLLM, mitmproxy, Playwright) - GPU box only
#
# Then:
#   bash claims/01_matched_exposure/run.sh     # one claim
#   bash artifact/REPRODUCE.sh                 # everything
#   python3 artifact/verify.py                 # check against reference values
#
# Environment variables:
#   PYTHON   interpreter to build the venv from (default: python3)
#   VENV     where to create the venv          (default: ./.venv)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ART="$ROOT/artifact"
[ -d "$ART" ] || ART="$ROOT"

PROFILE="repro"
[[ "${1:-}" == "--full" ]] && PROFILE="full"

REQ="$ART/requirements-repro.txt"
[[ "$PROFILE" == "full" ]] && REQ="$ART/requirements-full.txt"
if [ ! -f "$REQ" ]; then
  echo "[install] cannot find $REQ" >&2
  exit 2
fi

VENV="${VENV:-$ROOT/.venv}"
BASE_PYTHON="${PYTHON:-python3}"

if ! command -v "$BASE_PYTHON" >/dev/null 2>&1; then
  echo "[install] $BASE_PYTHON not found. Install Python 3.12 or newer." >&2
  exit 2
fi

echo "[install] profile       : $PROFILE"
echo "[install] artifact root : $ART"
echo "[install] requirements  : $REQ"
echo "[install] virtualenv    : $VENV"

# uv is faster and provisions Python itself, but is not required.
if command -v uv >/dev/null 2>&1; then
  echo "[install] using uv"
  # PYTHON, when set, picks the interpreter on both paths.
  uv venv --allow-existing ${PYTHON:+--python "$PYTHON"} "$VENV" 2>/dev/null \
    || uv venv ${PYTHON:+--python "$PYTHON"} "$VENV"
  uv pip install --python "$VENV/bin/python" -r "$REQ"
else
  echo "[install] uv not found, falling back to venv + pip"
  # Debian and Ubuntu ship python3 without ensurepip; the venv module is
  # present but cannot seed pip, so creation fails part way through. Detect it
  # first and say what to do, rather than surfacing Python's traceback.
  if ! "$BASE_PYTHON" -c "import ensurepip" >/dev/null 2>&1; then
    cat >&2 <<EOM
[install] $BASE_PYTHON cannot create a virtual environment: the ensurepip
[install] module is missing. This is the stock python3 on Debian and Ubuntu.
[install] Do one of the following, then re-run install.sh:
[install]   sudo apt install python3-venv          (adds ensurepip)
[install]   pip install uv   or   pipx install uv  (install.sh prefers uv)
[install] Or skip local setup entirely: the Dockerfile at the repository root
[install] and the Colab notebook in infrastructure/url need neither.
EOM
    exit 2
  fi
  "$BASE_PYTHON" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV/bin/python" -m pip install -r "$REQ"
fi

if [[ "$PROFILE" == "full" ]]; then
  echo
  echo "[install] installing Playwright Chromium"
  "$VENV/bin/python" -m playwright install chromium || \
    echo "[install] playwright install failed; see artifact/INSTALL.md" >&2
fi

echo
echo "[install] verifying imports"
"$VENV/bin/python" - <<'PY'
import numpy, scipy, matplotlib, pandas, sys
print(f"  python     {sys.version.split()[0]}")
print(f"  numpy      {numpy.__version__}")
print(f"  scipy      {scipy.__version__}")
print(f"  matplotlib {matplotlib.__version__}")
print(f"  pandas     {pandas.__version__}")
PY

echo
echo "[install] done."
echo "[install] Run everything with:"
echo "    PYTHON=$VENV/bin/python bash $ART/REPRODUCE.sh"
echo "    $VENV/bin/python $ART/verify.py"
echo "[install] Or run a single claim, for example:"
echo "    bash $ROOT/claims/01_matched_exposure/run.sh"
