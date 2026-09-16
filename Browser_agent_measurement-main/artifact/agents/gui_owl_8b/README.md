# GUI-Owl-8B

Pixel-only CUA. `mPLUG/GUI-Owl-1.5-8B-Think` (Qwen3-VL-8B backbone).

## Setup

```bash
huggingface-cli download mPLUG/GUI-Owl-1.5-8B-Think --local-dir ~/models/gui-owl-8b
python -m playwright install chromium
bash serve.sh        # vLLM at http://127.0.0.1:8004/v1
```

## Run

```bash
export GUI_OWL_BASE_URL=http://127.0.0.1:8004/v1
export GUI_OWL_MODEL=mPLUG/GUI-Owl-1.5-8B-Think
export GUI_OWL_MAX_STEPS=100
export HTTPS_PROXY=http://127.0.0.1:8080
python run_agent.py --task /tmp/task.json
```
