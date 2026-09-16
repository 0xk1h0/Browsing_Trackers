#!/usr/bin/env bash
# Smoke test the full traffic-capture pipeline before pilot study.
#
# Mimics participant flow:
#   GET /         (briefing)
#   POST /briefing (ack)
#   POST /login    (pid)
#   GET /dashboard
#   GET /task/1/iframe  (spawns isolated session for the PID's first task)
#   wait N seconds
#   inspect capture.jsonl
#
# Reports:
#   - Did chromium spawn cleanly?
#   - Is capture.jsonl growing?
#   - What hostnames appear (tracker classifier-ready)?
#   - Are content types diverse (HTML/JS/JSON/images) suggesting full page load?
set -euo pipefail

WEB_URL="${WEB_URL:-http://127.0.0.1:8888}"
PID="${PID:-P_SMOKE}"
TASK_ORDER="${TASK_ORDER:-1}"   # which task in the participant's assignment to start
WAIT_SECS="${WAIT_SECS:-25}"  # let chromium finish initial page load
STUDY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SESSIONS_DIR="${STUDY_DIR}/runtime/sessions"

PY="${USER_STUDY_PYTHON:-$(command -v python3)}"

cleanup() {
    "$PY" "${STUDY_DIR}/proto/spawn_session.py" stop --pid "${PID}" 2>&1 | tail -1
}
trap cleanup EXIT

echo "============================================================"
echo "  Traffic capture smoke test"
echo "    web_app : ${WEB_URL}"
echo "    PID     : ${PID}"
echo "    wait    : ${WAIT_SECS}s"
echo "============================================================"

# Sanity: web_app reachable
if ! curl -sf "${WEB_URL}/" -o /dev/null; then
    echo "[ERROR] web_app not reachable at ${WEB_URL}"
    exit 1
fi
echo "[ok] web_app reachable"

# Sanity: PID exists in assignments
if ! "$PY" -c "
import json,sys
data=json.load(open('${STUDY_DIR}/participant_assignments.json'))
ids=[a['participant_id'] for a in data['assignments']]
sys.exit(0 if '${PID}' in ids else 1)
"; then
    echo "[ERROR] PID '${PID}' not in participant_assignments.json"
    echo "  use a real PID (e.g. P001) or pre-add P_SMOKE to the assignments"
    exit 1
fi
echo "[ok] PID '${PID}' is a known assignment"

JAR=$(mktemp)
trap "rm -f '${JAR}'; cleanup" EXIT

# 1) briefing
curl -s -c "${JAR}" -b "${JAR}" -d "ack=on" -o /dev/null "${WEB_URL}/briefing"
# 2) login
curl -s -c "${JAR}" -b "${JAR}" -d "pid=${PID}" -o /dev/null "${WEB_URL}/login"
# 3) start task TASK_ORDER (triggers spawn_session)
echo "[..] spawning session via /task/${TASK_ORDER}/iframe ..."
t_start=$(date +%s)
curl -s -b "${JAR}" -o /tmp/iframe_page.html -w "  iframe page: HTTP %{http_code}  bytes=%{size_download}\n" "${WEB_URL}/task/${TASK_ORDER}/iframe"
t_iframe=$(($(date +%s) - t_start))
echo "  spawn + iframe response in ${t_iframe}s"

# Verify state.json + chromium process
STATE="${SESSIONS_DIR}/${PID}/state.json"
[ -f "${STATE}" ] || { echo "[FAIL] state.json missing: ${STATE}"; exit 1; }
cap_file=$("$PY" -c "import json; print(json.load(open('${STATE}'))['capture_file'])")
xpra_pid=$("$PY" -c "import json; print(json.load(open('${STATE}'))['xpra_pid'])")
echo "  capture file: ${cap_file}"
echo "  xpra pid    : ${xpra_pid}"

# 4) wait for traffic to accumulate
echo "[..] waiting ${WAIT_SECS}s for chromium to load page + tracker requests ..."
for i in $(seq 1 ${WAIT_SECS}); do
    sleep 1
    n=$(wc -l < "${cap_file}" 2>/dev/null || echo 0)
    if (( i % 5 == 0 )); then
        echo "    t=${i}s  captured=${n} requests"
    fi
done

# 5) verify chromium still alive (regex must look for the binary then the data-dir)
if pgrep -f "chrome-linux/chrome.*sessions/${PID}/" > /dev/null; then
    echo "[ok] chromium still running for ${PID}"
else
    echo "[WARN] chromium process for ${PID} no longer running"
fi

# 6) inspect capture
n_total=$(wc -l < "${cap_file}" 2>/dev/null || echo 0)
echo
echo "============================================================"
echo "  Capture summary (${n_total} requests)"
echo "============================================================"
"$PY" "${STUDY_DIR}/proto/inspect_capture.py" "${cap_file}" 2>&1

# Quick tracker classification — count any host matching common tracker domains
echo
echo "  Quick tracker hit count (loose match against well-known domains):"
"$PY" <<PY
import json
trackers = [
    "doubleclick.net", "googlesyndication.com", "google-analytics.com",
    "googletagmanager.com", "facebook.net", "facebook.com",
    "rubiconproject.com", "pubmatic.com", "adnxs.com", "adsrvr.org",
    "id5-sync.com", "amazon-adsystem.com", "criteo.com", "scorecardresearch.com",
    "bing.com/v1/sync", "ads.linkedin.com",
]
counts = {t: 0 for t in trackers}
with open("${cap_file}") as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try:
            url = json.loads(line).get("url","")
        except Exception: continue
        for t in trackers:
            if t in url:
                counts[t] += 1
for t, n in sorted(counts.items(), key=lambda kv: -kv[1]):
    if n > 0:
        print(f"    {n:4d}  {t}")
total = sum(counts.values())
print(f"    ----")
print(f"    {total:4d}  total tracker hits across well-known endpoints")
PY

echo
echo "[ok] smoke test complete."
