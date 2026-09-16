# Install + run

Both profiles are installed by `install.sh` at the repository root. It uses
[`uv`](https://docs.astral.sh/uv/) when it is on PATH and falls back to
`python3 -m venv` plus `pip` when it is not, so `uv` is a convenience rather
than a requirement.

## A. Reproduction only (no GPU, ~1 min total)

Verifies the paper tables / figures from the Tier-1 aggregates shipped here.

```bash
bash ../install.sh              # creates .venv, installs requirements-repro.txt
source .venv/bin/activate       # or prefix subsequent commands with `uv run`
bash REPRODUCE.sh               # runs every per-RQ run_repro.sh
python verify.py                # 19 checks: regenerated outputs vs the golden/ snapshot
```

## B. End-to-end measurement (GPU + mitmproxy + Playwright)

Re-collects raw mitmproxy flows by running the agents against the live web,
then re-aggregates Tier-1.

### Hardware

| Resource     | Minimum                                                |
|--------------|--------------------------------------------------------|
| GPU          | 1× NVIDIA, ≥24 GB VRAM for 7-8B agents; ≥48 GB for Browser-Use MoE (or 2× 24 GB at FP8 with `TP=2`) |
| RAM          | 32 GB                                                  |
| Disk         | 200 GB (≈50 MB per session × thousands of sessions)    |
| OS           | Linux (tested on Ubuntu 22.04, kernel 6.x)             |
| Network      | Outbound to Hugging Face, OpenAI, and the public web   |

### Environment

```bash
bash ../install.sh --full       # requirements-full.txt + playwright chromium
source .venv/bin/activate
```

### mitmproxy CA cert

The capture proxy decrypts HTTPS by terminating the TLS connection at a
locally-trusted CA. Generate the CA once, install it into the Chromium
profile that Playwright uses, and the agents' browser will accept the proxy's
certificates:

```bash
mitmdump --listen-port 8080 &      # first run creates ~/.mitmproxy/*
PROXY_PID=$!; sleep 2; kill $PROXY_PID
# Install the CA into Playwright's Chromium profile (one-time):
mkdir -p ~/.pki/nssdb
certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n mitmproxy \
  -i ~/.mitmproxy/mitmproxy-ca-cert.pem
```

### Per-agent setup

Each agent has its own `agents/<name>/README.md` with the upstream Hugging
Face weights URL, the vLLM serve script (`serve.sh`), and the per-task runner
(`run_agent.py`). Step budget, vLLM port, and action vocabulary are
documented per-agent. The canonical configuration matrix is in
`agents/registry.json`.

Example for Fara-7B:

```bash
# Fetch weights (~14 GB, gated — accept the model license on HF)
huggingface-cli download microsoft/Fara-7B --local-dir ~/models/fara-7b
pip install -e "${UPSTREAM_FARA_REPO_CHECKOUT}"   # provides `fara-cli`

# Serve via vLLM (port 5001 by default; see serve.sh for knobs)
bash agents/fara_7b/serve.sh &
```

### Single end-to-end session

Once the proxy CA is trusted and one agent is being served, capture one
task through the full pipeline:

```bash
export AGENT=fara-7b
export TASK_FILE=$PWD/data/webvoyager/example_task.json
export OUTPUT_DIR=$PWD/run/single_session
bash pipeline/collect_session.sh
ls run/single_session/   # capture.jsonl + js_telemetry.jsonl + stopped.json + agent.{stdout,stderr}.log
```

`pipeline/collect_session.sh` runs the canonical 5 steps:

1. Launch `mitmdump` with the capture addon (`pipeline/capture/mitm_addon.py`)
   and the JS fingerprint shim (`pipeline/capture/hook.js`).
2. Point `HTTPS_PROXY` / `HTTP_PROXY` at the proxy.
3. Inject the JS shim via Playwright `add_init_script`.
4. Dispatch to the matching `agents/<name>/run_agent.py`.
5. Emit `stopped.json` with operational status + policy env.

### Full sweeps used in the paper

| Sweep                                         | Driver script                                            |
|-----------------------------------------------|----------------------------------------------------------|
| 643-task WebVoyager, single agent / condition | `rq3_ablation/scripts/run_643_sensitivity.py`            |
| RQ3 16-session smoke (2 agents × 4 cond × 2 tasks) | `rq3_ablation/scripts/rq3_smoke_test.py`            |
| Per-agent single-task ablation (Fara-7B)      | `rq3_ablation/scripts/run_fara_ablation.py`              |
| Per-agent single-task ablation (Browser-Use)  | `rq3_ablation/scripts/run_bu_ablation.py`                |

Example: launch a 4-way parallel sweep of Fara-7B + Browser-Use over the
643-task pool under the 4 primary conditions:

```bash
# Each worker iterates a task shard sequentially. Adjust GPUs / ports.
python -m rq3_ablation.scripts.run_643_sensitivity \
    --agent fara-7b --base-url http://localhost:5001/v1 \
    --conditions C0 C2 C7 C8 \
    --shard 0 --num-shards 2 \
    --output-root rq3_ablation/raw/rq7_policy_ablation_643 \
    --max-rounds 30 --timeout 300
```

Outputs land under
`<output-root>/agent=<name>/condition=<C>/task=<T>/rep=<r>/{capture,js_telemetry}.jsonl + stopped.json`,
matching the layout the rebuild scripts (`scripts/rebuild_*.py` in each per-RQ
directory) expect.

### Re-aggregating Tier-1 from raw flows

After a fresh sweep, regenerate the shipped Tier-1 CSVs:

```bash
# RQ3 ablation: rebuild task_success_ablation.json from stopped.json files
python rq3_ablation/scripts/compute_task_success_ablation.py \
    --raw-root rq3_ablation/raw/rq7_policy_ablation_643/

# RQ3 ablation: rebuild layer-isolation per-session.csv
python rq3_ablation/scripts/rebuild_layer_isolation.py \
    --raw-root rq3_ablation/raw/rq7_policy_ablation/

# Mind2Web: rebuild per-session.csv
python cross_benchmark/scripts/rebuild_mind2web_per_session.py \
    --runs-root /path/to/runs/

# RQ2 dose-response binning
python rq2_dose_response/scripts/rebuild_dose_response_643.py \
    --runs-root /path/to/runs/
```

Then re-run `bash REPRODUCE.sh` to regenerate tables and figures.

## Release-time anonymization gate

```bash
bash scripts/anonymize_check.sh
```

Greps the artifact for local-machine paths, Korean text in code/config,
internal hostnames and emails, RFC1918 internal IPs, and API-key signatures.
Non-zero exit on any hit. Run before publishing.
