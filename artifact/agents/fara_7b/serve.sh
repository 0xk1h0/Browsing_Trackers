#!/usr/bin/env bash
set -euo pipefail

# Start a local vLLM server for Fara-7B.
# Requires fara to be installed (fara-cli already on PATH).
#
# Optional env vars:
#   MODEL_URL (default: microsoft/Fara-7B)
#   MODEL_NAME (default: MODEL_URL)
#   PORT (default: 5000)        # public API port
#   VLLM_PORT (default: 5001)   # internal vLLM port
#   DEVICE_ID (default: 0)       # comma-separated GPU ids
#   TP (optional)                # tensor-parallel size; sets DEVICE_ID to 0..TP-1 if provided
#   MAX_N_IMAGES (default: 3)
#   DTYPE (default: auto)
#   ENFORCE_EAGER (default: 1)   # set to 0 to disable --enforce_eager

MODEL_URL="${MODEL_URL:-microsoft/Fara-7B}"
MODEL_NAME="${MODEL_NAME:-$MODEL_URL}"
PORT="${PORT:-5000}"
VLLM_PORT="${VLLM_PORT:-5001}"
if [[ -n "${TP:-}" && -z "${DEVICE_ID:-}" ]]; then
  if ! [[ "${TP}" =~ ^[0-9]+$ ]] || [[ "${TP}" -lt 1 ]]; then
    echo "TP must be a positive integer" >&2
    exit 1
  fi
  DEVICE_ID="$(seq -s, 0 $((TP-1)))"
fi
DEVICE_ID="${DEVICE_ID:-0}"
MAX_N_IMAGES="${MAX_N_IMAGES:-3}"
DTYPE="${DTYPE:-auto}"
ENFORCE_EAGER="${ENFORCE_EAGER:-1}"

args=(
  python -m fara.vllm.az_vllm
  --model_url "${MODEL_URL}"
  --model_name "${MODEL_NAME}"
  --port "${PORT}"
  --vllm_port "${VLLM_PORT}"
  --device_id "${DEVICE_ID}"
  --max_n_images "${MAX_N_IMAGES}"
  --dtype "${DTYPE}"
)

if [[ "${ENFORCE_EAGER}" == "1" ]]; then
  args+=(--enforce_eager)
fi

export VLLM_GPU_MEMORY_UTILIZATION=${VLLM_GPU_MEMORY_UTILIZATION:-0.85}

"${args[@]}"
