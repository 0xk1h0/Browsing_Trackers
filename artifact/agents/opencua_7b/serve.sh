#!/usr/bin/env bash
# Serve OpenCUA-7B via vLLM at http://127.0.0.1:8003/v1.
set -euo pipefail
MODEL_DIR="${OPENCUA_MODEL_DIR:-${HOME}/models/opencua-7b}"
PORT="${OPENCUA_VLLM_PORT:-8003}"

exec vllm serve "${MODEL_DIR}" \
  --port "${PORT}" \
  --served-model-name "xlangai/OpenCUA-7B" \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.85 \
  --tensor-parallel-size 1
