#!/usr/bin/env bash
set -euo pipefail

# Start a local vLLM server for bu-30b-a3b-preview

MODEL_URL="${MODEL_URL:-browser-use/bu-30b-a3b-preview}"
MODEL_NAME="${MODEL_NAME:-$MODEL_URL}"
PORT="${PORT:-8002}"
TP="${TP:-1}"
DTYPE="${DTYPE:-auto}"
ENFORCE_EAGER="${ENFORCE_EAGER:-0}"
TRUST_REMOTE_CODE="${TRUST_REMOTE_CODE:-1}"
CUDA_DEVICES="${CUDA_DEVICES:-2}"

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

echo "[run_bu_vllm] CUDA_VISIBLE_DEVICES=${CUDA_DEVICES}"
echo "[run_bu_vllm] ${CMD[*]}"

CUDA_VISIBLE_DEVICES="${CUDA_DEVICES}" "${CMD[@]}"
