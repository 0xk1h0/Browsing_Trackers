#!/usr/bin/env bash
set -euo pipefail

# Start a local vLLM server for SoM-GLM (GLM-4.1V-9B-Thinking).
# Based on GLM-V recommended serving options for multimodal + tool calling.
#
# Optional env vars:
#   MODEL_URL (default: zai-org/GLM-4.1V-9B-Thinking)
#   MODEL_NAME (default: MODEL_URL)
#   PORT (default: 8000)
#   TP (default: 4)
#   CUDA_DEVICES (default: 0,1,2,3 for TP=4)
#   DTYPE (default: auto)
#   TOOL_CALL_PARSER (default: glm45)
#   REASONING_PARSER (default: glm45)
#   MM_ENCODER_TP_MODE (default: data)
#   MM_PROCESSOR_CACHE_TYPE (default: shm)
#   GENERATION_CONFIG (default: vllm)
#   ENFORCE_EAGER (default: 0)

MODEL_URL="${MODEL_URL:-zai-org/GLM-4.1V-9B-Thinking}"
MODEL_NAME="${MODEL_NAME:-$MODEL_URL}"
PORT="${PORT:-8000}"
TP="${TP:-4}"
DTYPE="${DTYPE:-auto}"
TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-glm45}"
REASONING_PARSER="${REASONING_PARSER:-glm45}"
MM_ENCODER_TP_MODE="${MM_ENCODER_TP_MODE:-data}"
MM_PROCESSOR_CACHE_TYPE="${MM_PROCESSOR_CACHE_TYPE:-shm}"
GENERATION_CONFIG="${GENERATION_CONFIG:-vllm}"
ENFORCE_EAGER="${ENFORCE_EAGER:-0}"

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
  --enable-auto-tool-choice
  --tool-call-parser "${TOOL_CALL_PARSER}"
  --reasoning-parser "${REASONING_PARSER}"
  --mm-encoder-tp-mode "${MM_ENCODER_TP_MODE}"
  --mm_processor_cache_type "${MM_PROCESSOR_CACHE_TYPE}"
  --generation-config "${GENERATION_CONFIG}"
)

if [[ "${ENFORCE_EAGER}" == "1" ]]; then
  CMD+=(--enforce-eager)
fi

echo "[run_som_glm_vllm] CUDA_VISIBLE_DEVICES=${CUDA_DEVICES}"
echo "[run_som_glm_vllm] ${CMD[*]}"

CUDA_VISIBLE_DEVICES="${CUDA_DEVICES}" "${CMD[@]}"
