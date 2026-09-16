# SoM-GPT-5

Visible-element SoM agent over the OpenAI Responses API (`gpt-5`).

## Setup

```bash
export OPENAI_API_KEY=sk-...           # YOUR key — never commit it
python -m playwright install chromium
```

## Run

```bash
export SOM_GPT_MODEL=gpt-5
export SOM_GPT_REASONING_EFFORT=minimal
export SOM_GPT_MAX_STEPS=100
export HTTPS_PROXY=http://127.0.0.1:8080
python run_agent.py --task /tmp/task.json
```

`scripts/anonymize_check.sh` in the artifact root fails on any committed
`sk-…` pattern. Keep the key in your shell environment only.
