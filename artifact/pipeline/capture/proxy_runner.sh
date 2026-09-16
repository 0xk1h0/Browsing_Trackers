#!/usr/bin/env bash
# proxy_runner.sh — launch mitmdump with the AgentCloak capture addon.
#
# Usage:
#   AGENTCLOAK_CAPTURE_FILE=/path/to/capture.jsonl \
#       bash proxy_runner.sh [--listen-port 8080] [--ssl-insecure]
#
# Environment-controlled toggles:
#   AGENTCLOAK_CAPTURE_FILE   (required) path to write the per-request JSONL log
#   AGENTCLOAK_FP_CAPTURE_FILE          path to write JS fingerprint telemetry
#   AGENTCLOAK_HASH_JS_ONLY=1           hash only JS responses (cuts overhead)
#   AGENTCLOAK_HASH_MAX_BYTES=N         hash only responses <= N bytes (0 = no limit)
#
# RQ3 ablation toggles (paired with agent-side schema patches):
#   RQ3_BLOCK_SEARCH=1     drop search-engine hosts
#   RQ3_BLOCK_OFFDOMAIN=1  drop off-domain navigations
#   RQ3_BLOCK_CMP=1        drop CMP consent-banner CDNs
#
# E6 transition-budget toggles (post-hoc defense, paper appendix):
#   E6_TRANSITION_BUDGET=N   block cross-site navigations after N transitions
#   E6_BLOCK_SUBRESOURCES=1  also block 3P sub-resources after budget exhausted

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${AGENTCLOAK_CAPTURE_FILE:-}" ]]; then
  echo "[proxy_runner] set AGENTCLOAK_CAPTURE_FILE before invoking." >&2
  exit 2
fi

# mitmdump is required.
if ! command -v mitmdump >/dev/null 2>&1; then
  echo "[proxy_runner] mitmdump not on PATH. Install with: pip install mitmproxy" >&2
  exit 3
fi

exec mitmdump \
  -s "$HERE/mitm_addon.py" \
  --set "console_eventlog_verbosity=warn" \
  "$@"
