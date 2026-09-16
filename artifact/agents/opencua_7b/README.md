# OpenCUA-7B

Pixel-only CUA. `xlangai/OpenCUA-7B` (Qwen2.5-VL-7B backbone).
pyautogui-style actions; no URL primitives.

## Setup

```bash
huggingface-cli download xlangai/OpenCUA-7B --local-dir ~/models/opencua-7b
python -m playwright install chromium
bash serve.sh        # vLLM at http://127.0.0.1:8003/v1
```

## Run

```bash
export OPENCUA_BASE_URL=http://127.0.0.1:8003/v1
export OPENCUA_MODEL=xlangai/OpenCUA-7B
export OPENCUA_MAX_STEPS=100
export HTTPS_PROXY=http://127.0.0.1:8080
python run_agent.py --task /tmp/task.json
```
