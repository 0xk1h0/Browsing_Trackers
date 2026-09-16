# SoM-GLM

Visible-element set-of-marks agent. `zai-org/GLM-4.1V-9B-Thinking`.

## Setup

```bash
huggingface-cli download zai-org/GLM-4.1V-9B-Thinking --local-dir ~/models/glm-4-1v-9b
python -m playwright install chromium
bash serve.sh        # vLLM at http://127.0.0.1:8000/v1
```

## Run

```bash
export SOM_GLM_BASE_URL=http://127.0.0.1:8000/v1
export SOM_GLM_MODEL=zai-org/GLM-4.1V-9B-Thinking
export SOM_GLM_MAX_STEPS=100
export HTTPS_PROXY=http://127.0.0.1:8080
python run_agent.py --task /tmp/task.json
```
