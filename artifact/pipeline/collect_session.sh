#!/usr/bin/env bash
# collect_session.sh — end-to-end single-task capture for one agent.
#
# Demonstrates the canonical 5-step measurement flow used to produce
# the per-session Tier-1 aggregates shipped in this artifact.
#
# Required environment:
#   AGENT           one of fara-7b | browser-use | opencua-7b | som-glm | som-gpt-5 | ui-tars | gui-owl-8b
#   TASK_FILE       JSON file with {"instruction": "...", "start_url": "...", "task_id": "..."}
#   OUTPUT_DIR      where capture.jsonl + js_telemetry.jsonl + stopped.json are written
#
# Optional:
#   PROXY_PORT      default 8080
#   FARA_DISABLED_ACTIONS / BU_DISABLED_ACTIONS / RQ3_BLOCK_*  (RQ3 ablation, see rq3_ablation/)
#
# Assumes:
#   * The agent's vLLM server is already running (bash agents/<AGENT>/serve.sh).
#   * mitmproxy is installed (pip install mitmproxy).
#   * Playwright Chromium is installed (python -m playwright install chromium).
set -euo pipefail

AGENT="${AGENT:?set AGENT to one of fara-7b|browser-use|opencua-7b|som-glm|som-gpt-5|ui-tars|gui-owl-8b}"
TASK_FILE="${TASK_FILE:?set TASK_FILE to a path containing {instruction, start_url, task_id}}"
OUTPUT_DIR="${OUTPUT_DIR:?set OUTPUT_DIR to write capture.jsonl + js_telemetry.jsonl + stopped.json}"
PROXY_PORT="${PROXY_PORT:-8080}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$OUTPUT_DIR"

# 1. Launch the capture proxy in the background.
export AGENTCLOAK_CAPTURE_FILE="$OUTPUT_DIR/capture.jsonl"
export AGENTCLOAK_FP_CAPTURE_FILE="$OUTPUT_DIR/js_telemetry.jsonl"
mitmdump -s "$HERE/pipeline/capture/mitm_addon.py" \
  --listen-port "$PROXY_PORT" --ssl-insecure \
  > "$OUTPUT_DIR/mitmdump.log" 2>&1 &
PROXY_PID=$!
trap 'kill "$PROXY_PID" 2>/dev/null || true' EXIT
sleep 2

# 2. Configure the agent to route through the proxy.
export HTTPS_PROXY="http://127.0.0.1:${PROXY_PORT}"
export HTTP_PROXY="http://127.0.0.1:${PROXY_PORT}"

# 3. Inject the JS fingerprint shim into the start-of-page event.
# (The Playwright `page.add_init_script` is called inside each agent runner;
#  the shim payload is at $HERE/pipeline/capture/hook.js.)
export AGENTCLOAK_INIT_SCRIPT="$HERE/pipeline/capture/hook.js"

# 4. Dispatch to the matching agent runner.
case "$AGENT" in
  fara-7b)
    TASK_TEXT=$(python -c "import json; print(json.load(open('$TASK_FILE'))['instruction'])")
    START_URL=$(python -c "import json; print(json.load(open('$TASK_FILE'))['start_url'])")
    fara-cli --task "$TASK_TEXT" --start_page "$START_URL" --max_rounds 30 \
      > "$OUTPUT_DIR/agent.stdout.log" 2> "$OUTPUT_DIR/agent.stderr.log"
    ;;
  browser-use)
    python "$HERE/agents/browser_use/run_agent.py" \
      --task "$(python -c "import json; print(json.load(open('$TASK_FILE'))['instruction'])")" \
      --start-url "$(python -c "import json; print(json.load(open('$TASK_FILE'))['start_url'])")" \
      --timeout-seconds 500 \
      > "$OUTPUT_DIR/agent.stdout.log" 2> "$OUTPUT_DIR/agent.stderr.log"
    ;;
  opencua-7b|som-glm|som-gpt-5|ui-tars|gui-owl-8b)
    AGENT_KEY="${AGENT//-/_}"
    python "$HERE/agents/${AGENT_KEY}/run_agent.py" --task "$TASK_FILE" \
      > "$OUTPUT_DIR/agent.stdout.log" 2> "$OUTPUT_DIR/agent.stderr.log"
    ;;
  *)
    echo "[collect_session] unknown AGENT=$AGENT" >&2
    exit 2
    ;;
esac

# 5. Emit a minimal stopped.json so the per-session aggregator can pick up
#    operational status without re-parsing the capture.
python - <<PY
import json, os, time
from pathlib import Path
out = Path("$OUTPUT_DIR")
records = sum(1 for _ in (out / "capture.jsonl").open()) if (out / "capture.jsonl").exists() else 0
(out / "stopped.json").write_text(json.dumps({
    "agent": "$AGENT",
    "task_file": "$TASK_FILE",
    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "capture_records": records,
    "policy_env": {k: os.environ[k] for k in os.environ
                   if k.startswith(("RQ3_BLOCK_", "FARA_DISABLED_ACTIONS", "BU_DISABLED_ACTIONS"))},
}, indent=2))
PY

echo "[collect_session] wrote $OUTPUT_DIR/{capture,js_telemetry}.jsonl + stopped.json"
