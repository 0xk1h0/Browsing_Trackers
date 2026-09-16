# UI-TARS-1.5-7B

Pixel-only CUA. `ByteDance-Seed/UI-TARS-1.5-7B`.

## Setup

```bash
huggingface-cli download ByteDance-Seed/UI-TARS-1.5-7B --local-dir ~/models/ui-tars-1.5-7b
python -m playwright install chromium
bash serve.sh        # vLLM at http://127.0.0.1:8001/v1
```

## Run

```bash
export UI_TARS_BASE_URL=http://127.0.0.1:8001/v1
export UI_TARS_MODEL=ByteDance-Seed/UI-TARS-1.5-7B
export UI_TARS_MAX_STEPS=100
export HTTPS_PROXY=http://127.0.0.1:8080
python run_agent.py --task /tmp/task.json
```
