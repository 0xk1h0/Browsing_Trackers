#!/usr/bin/env bash
set -euo pipefail

# Start a local vLLM server for UI-TARS-1.5-7B.
#
# Optional env vars:
#   MODEL_URL (default: ByteDance-Seed/UI-TARS-1.5-7B)
#   MODEL_NAME (default: MODEL_URL)
#   PORT (default: 8001)
#   TP (default: 4)
#   CUDA_DEVICES (default: 0,1,2,3 for TP=4)
#   DTYPE (default: auto)
#   ENFORCE_EAGER (default: 0)
#   TRUST_REMOTE_CODE (default: 0)

MODEL_URL="${MODEL_URL:-ByteDance-Seed/UI-TARS-1.5-7B}"
MODEL_NAME="${MODEL_NAME:-$MODEL_URL}"
PORT="${PORT:-8001}"
TP="${TP:-4}"
DTYPE="${DTYPE:-auto}"
ENFORCE_EAGER="${ENFORCE_EAGER:-0}"
TRUST_REMOTE_CODE="${TRUST_REMOTE_CODE:-0}"

if ! [[ "${TP}" =~ ^[0-9]+$ ]] || [[ "${TP}" -lt 1 ]]; then
  echo "TP must be a positive integer." >&2
  exit 1
fi

if [[ -z "${CUDA_DEVICES:-}" ]]; then
  CUDA_DEVICES="$(seq -s, 0 $((TP-1)))"
fi

CMD=(
  vllm serve "${MODEL_URL}"
  --port "${PORT}"
  --served-model-name "${MODEL_NAME}"
  --tensor-parallel-size "${TP}"
  --dtype "${DTYPE}"
)

if [[ "${ENFORCE_EAGER}" == "1" ]]; then
  CMD+=(--enforce-eager)
fi
if [[ "${TRUST_REMOTE_CODE}" == "1" ]]; then
  CMD+=(--trust-remote-code)
fi

echo "[run_ui_tars_vllm] CUDA_VISIBLE_DEVICES=${CUDA_DEVICES}"
echo "[run_ui_tars_vllm] ${CMD[*]}"

CUDA_VISIBLE_DEVICES="${CUDA_DEVICES}" "${CMD[@]}"
