#!/usr/bin/env bash
# Smoke-test multiple tasks (different start URLs) sequentially to verify:
#   - chromium loads each site without crashing
#   - capture.jsonl + js_telemetry.jsonl accumulate per session
#   - tracker domain hits show up where expected
#
# Usage:
#   PID=P001 WAIT_SECS=20 bash multi_task_smoke.sh
set -euo pipefail

WEB_URL="${WEB_URL:-http://127.0.0.1:8888}"
PID="${PID:-P001}"
WAIT_SECS="${WAIT_SECS:-20}"
STUDY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${USER_STUDY_PYTHON:-$(command -v python3)}"

# Test a representative sample of the 10 tasks. We hit the participant's
# task orders 1, 3, 6, 8, 10 (different sites due to Latin square).
TASK_ORDERS=(1 3 6 8 10)

JAR=$(mktemp)
trap "rm -f '${JAR}'; '${PY}' '${STUDY_DIR}/proto/spawn_session.py' stop --pid '${PID}' 2>&1 | tail -1" EXIT

# Establish session cookies
curl -s -c "${JAR}" -b "${JAR}" -d "ack=on" -o /dev/null "${WEB_URL}/briefing"
curl -s -c "${JAR}" -b "${JAR}" -d "pid=${PID}" -o /dev/null "${WEB_URL}/login"

printf "%-4s %-20s %8s %8s %8s %s\n" "ord" "task_id" "reqs" "telem" "hosts" "top-host"
printf "%-4s %-20s %8s %8s %8s %s\n" "---" "-------" "----" "-----" "-----" "-------"

for ORDER in "${TASK_ORDERS[@]}"; do
    # Trigger spawn
    curl -s -b "${JAR}" -o /tmp/iframe.html "${WEB_URL}/task/${ORDER}/iframe" >/dev/null
    state="${STUDY_DIR}/runtime/sessions/${PID}/state.json"
    cap=$("$PY" -c "import json; print(json.load(open('${state}'))['capture_file'])")
    fp=$("$PY" -c "import json; print(json.load(open('${state}'))['fp_capture_file'])")
    task=$("$PY" -c "import json; print(json.load(open('${state}'))['task_id'])")

    # Wait
    sleep "${WAIT_SECS}"

    # Inspect
    reqs=$(wc -l < "${cap}" 2>/dev/null || echo 0)
    telem=$(wc -l < "${fp}" 2>/dev/null || echo 0)
    hosts=$("$PY" -c "
import json
hs = set()
for line in open('${cap}', encoding='utf-8'):
    try: hs.add(json.loads(line).get('hostname',''))
    except: pass
print(len(hs))
")
    top=$("$PY" -c "
import json
from collections import Counter
c = Counter()
for line in open('${cap}', encoding='utf-8'):
    try: c[json.loads(line).get('hostname','')] += 1
    except: pass
top, _ = c.most_common(1)[0] if c else ('(none)', 0)
print(top)
")
    printf "%-4s %-20s %8s %8s %8s %s\n" "$ORDER" "$task" "$reqs" "$telem" "$hosts" "$top"

    # Stop this session before starting next
    "$PY" "${STUDY_DIR}/proto/spawn_session.py" stop --pid "${PID}" >/dev/null 2>&1
    sleep 1
done

echo
echo "[done] multi-task smoke complete."
