# Browser-Use

Browser-tooling agent. `browser-use/bu-30b-a3b-preview` (Qwen3-VL-MoE, 30B/3B-active).

## Setup

```bash
pip install browser_use==0.12.0
python -m playwright install chromium
huggingface-cli download browser-use/bu-30b-a3b-preview --local-dir ~/models/bu-30b
bash serve.sh        # vLLM at http://127.0.0.1:8002/v1
```

## Run

```bash
export BROWSER_USE_LLM=browser-use/bu-30b-a3b-preview
export BROWSER_USE_BASE_URL=http://127.0.0.1:8002/v1
export OPENAI_API_KEY=dummy
export HTTPS_PROXY=http://127.0.0.1:8080
python run_agent.py --task "..." --start-url "https://..." --timeout-seconds 500
```

## RQ3 ablation

`BU_DISABLED_ACTIONS=search,navigate` etc. Browser-Use's `navigate` is
removed *after* the initial start-URL load (see
`../../rq3_ablation/interventions/schema_patches/browser_use_ablation.py`).
