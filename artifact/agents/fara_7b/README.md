# Fara-7B

Browser-tooling agent. `microsoft/Fara-7B` (Qwen2.5-VL-7B backbone).

## Setup

```bash
huggingface-cli download microsoft/Fara-7B --local-dir ~/models/fara-7b
pip install -e "${UPSTREAM_FARA_REPO_CHECKOUT}"   # provides fara-cli
bash serve.sh        # vLLM at http://127.0.0.1:5001/v1
```

## Run

```bash
export HTTPS_PROXY=http://127.0.0.1:8080
fara-cli --task "..." --start_page "https://..." --max_rounds 30
```

## RQ3 ablation

`FARA_DISABLED_ACTIONS=<csv>` removes actions from the tool schema.
See `../../rq3_ablation/interventions/schema_patches/`.
