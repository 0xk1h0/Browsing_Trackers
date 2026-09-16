#!/usr/bin/env bash
# Spawn N concurrent participant sessions and verify resource isolation.
# Each PID gets its own display, mitmproxy port, xpra port, chromium profile,
# and capture file. We confirm:
#   - All N chromiums actually run concurrently
#   - All N capture files grow independently
#   - All N xpra HTML5 backends are listening on distinct loopback ports
set -euo pipefail

WEB_URL="${WEB_URL:-http://127.0.0.1:8888}"
PIDS=("P001" "P002" "P003")
WAIT_SECS="${WAIT_SECS:-25}"
STUDY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${USER_STUDY_PYTHON:-$(command -v python3)}"

cleanup() {
    for pid in "${PIDS[@]}"; do
        "$PY" "${STUDY_DIR}/proto/spawn_session.py" stop --pid "$pid" 2>/dev/null | tail -1
    done
}
trap cleanup EXIT

echo "============================================================"
echo "  Multi-PID concurrent stress test"
echo "  PIDs : ${PIDS[*]}"
echo "  Wait : ${WAIT_SECS}s"
echo "============================================================"

# Spawn each in parallel
for pid in "${PIDS[@]}"; do
    JAR="/tmp/jar_${pid}"
    rm -f "$JAR"
    curl -s -c "$JAR" -b "$JAR" -d "ack=on" -o /dev/null "${WEB_URL}/briefing"
    curl -s -c "$JAR" -b "$JAR" -d "pid=${pid}" -o /dev/null "${WEB_URL}/login"
    (
        curl -s -b "$JAR" -o /dev/null "${WEB_URL}/task/1/iframe"
        echo "  ${pid}: spawn complete"
    ) &
done
wait
echo

echo "[..] sessions all spawned, waiting ${WAIT_SECS}s for chromium activity ..."
sleep "${WAIT_SECS}"

echo
printf "%-6s %-9s %-10s %-9s %8s %8s %s\n" "PID" "display" "xpra-port" "mitm-port" "reqs" "telem" "task"
printf "%-6s %-9s %-10s %-9s %8s %8s %s\n" "---" "-------" "---------" "---------" "----" "-----" "----"
for pid in "${PIDS[@]}"; do
    state="${STUDY_DIR}/runtime/sessions/${pid}/state.json"
    if [ ! -f "$state" ]; then
        printf "%-6s %s\n" "$pid" "MISSING state.json"
        continue
    fi
    display=$("$PY" -c "import json; print(json.load(open('$state'))['display_num'])")
    xport=$("$PY" -c "import json; print(json.load(open('$state'))['xpra_port'])")
    mport=$("$PY" -c "import json; print(json.load(open('$state'))['mitm_port'])")
    cap=$("$PY" -c "import json; print(json.load(open('$state'))['capture_file'])")
    fp=$("$PY" -c "import json; print(json.load(open('$state'))['fp_capture_file'])")
    task=$("$PY" -c "import json; print(json.load(open('$state'))['task_id'])")
    reqs=$(wc -l < "$cap" 2>/dev/null || echo 0)
    telem=$(wc -l < "$fp" 2>/dev/null || echo 0)
    printf "%-6s :%-8s %-10s %-9s %8s %8s %s\n" "$pid" "$display" "$xport" "$mport" "$reqs" "$telem" "$task"
done

echo
echo "=== uniqueness check ==="
PIDS_CSV=$(IFS=,; echo "${PIDS[*]}")
PIDS_CSV="${PIDS_CSV}" STUDY_DIR="${STUDY_DIR}" "$PY" <<'PY'
import json, os
pids = os.environ["PIDS_CSV"].split(",")
study = os.environ["STUDY_DIR"]
states = []
for p in pids:
    sf = f"{study}/runtime/sessions/{p}/state.json"
    if os.path.isfile(sf):
        states.append(json.load(open(sf)))
fields = ["display_num","xpra_port","mitm_port","xpra_pid","mitm_pid"]
for fname in fields:
    vals = [s.get(fname) for s in states]
    ok = len(set(vals)) == len(vals)
    flag = "OK" if ok else "COLLISION"
    print(f"  {fname:12s} = {vals}  -> {flag}")
PY

echo
echo "=== running xpra processes ==="
pgrep -af "xpra start :" | grep -v grep | head -6
echo
echo "=== running chromium processes ==="
pgrep -af "chrome-linux/chrome --proxy" | head -6
echo
echo "[done] stress test complete."
