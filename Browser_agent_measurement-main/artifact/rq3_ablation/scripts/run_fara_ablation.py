"""Standalone Fara-7B ablation runner (RQ3 smoke).

Pure-process runner that exercises the full RQ3 ablation pipeline on Fara-7B
without depending on the heavyweight measurement orchestrator. Per session:

    1. Start a fresh mitmdump on a free port with the condition's env merged
       (FARA_DISABLED_ACTIONS, RQ3_BLOCK_SEARCH, RQ3_BLOCK_OFFDOMAIN,
       AGENTCLOAK_CAPTURE_FILE, AGENTCLOAK_FP_CAPTURE_FILE).
    2. Run ``fara-cli --task '<instruction>' --start_page '<url>'`` with
       HTTP(S)_PROXY set to that mitmdump port and the same condition env.
    3. Tear down mitmdump.  Inspect capture.jsonl + stopped.json.

Assumes the Fara-7B vLLM server is already running (default base URL
``http://localhost:5000``).  Start it via::

    nohup bash scripts/run_fara_vllm.sh > /tmp/fara_vllm_logs/server.log 2>&1 &

Usage::

    # default: 1 task × 1 condition (C0) × 1 rep
    python -m experiments.rq7_agent_tracker_measurement.run_fara_ablation \\
        --task-id "Amazon--32" --conditions C0

    # ablation pair: C0 baseline + C3 fully restricted
    python -m experiments.rq7_agent_tracker_measurement.run_fara_ablation \\
        --task-id "Amazon--32" --conditions C0 C3 --max-rounds 10

Outputs land in ``data/rq7_policy_ablation/agent=fara-7b/condition=<cond>/task=<id>/rep=01/``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from agentcloak.measurement.policy_condition import policy_condition_env

ROOT = Path(__file__).resolve().parent
OUT_ROOT = ROOT / "data" / "rq7_policy_ablation"
ADDON_PATH = ROOT.parent.parent / "agentcloak" / "measurement" / "mitm_capture_addon.py"


def _load_task(task_id: str):
    from experiments.rq7_agent_tracker_measurement.load_user_study_tasks import (
        load_master_tasks,
    )
    for t in load_master_tasks():
        if t.task_id == task_id:
            return t
    raise SystemExit(
        f"task_id {task_id!r} not found. Available: "
        f"{[t.task_id for t in load_master_tasks()]}"
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_proxy(port: int, deadline_s: float = 15.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < deadline_s:
        with contextlib.suppress(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        time.sleep(0.3)
    return False


def _safe_task_dir(task_id: str) -> str:
    return task_id.replace("/", "_").replace(" ", "_")


def _run_one(
    task,
    condition: str,
    rep: int,
    *,
    max_rounds: int,
    base_url: str,
    timeout_seconds: int,
) -> dict:
    cond_dir = (
        OUT_ROOT
        / "agent=fara-7b"
        / f"condition={condition}"
        / f"task={_safe_task_dir(task.task_id)}"
        / f"rep={rep:02d}"
    )
    cond_dir.mkdir(parents=True, exist_ok=True)
    capture_file = cond_dir / "capture.jsonl"
    fp_file = cond_dir / "js_telemetry.jsonl"
    stdout_file = cond_dir / "agent.stdout.log"
    stderr_file = cond_dir / "agent.stderr.log"
    for f in (capture_file, fp_file, stdout_file, stderr_file):
        f.unlink(missing_ok=True)

    # Build env: parent env + condition env + capture file pointers.
    cond_env = policy_condition_env(condition)
    base_env = {**os.environ}
    base_env.update(cond_env)
    base_env["AGENTCLOAK_CAPTURE_FILE"] = str(capture_file)
    base_env["AGENTCLOAK_FP_CAPTURE_FILE"] = str(fp_file)

    port = _free_port()
    mitm_cmd = [
        "mitmdump",
        "--listen-port", str(port),
        "--set", "block_global=false",
        "--set", "ssl_insecure=true",
        "-s", str(ADDON_PATH),
        "--quiet",
    ]
    summary = {
        "agent": "fara-7b",
        "task_id": task.task_id,
        "condition": condition,
        "rep": rep,
        "session_dir": str(cond_dir),
        "policy_env": cond_env,
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "max_rounds": max_rounds,
        "base_url": base_url,
    }

    mitm = subprocess.Popen(
        mitm_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=base_env,
    )
    try:
        if not _wait_proxy(port):
            raise RuntimeError("mitmdump did not start listening in 15s")

        fara_env = {
            **base_env,
            "HTTP_PROXY": f"http://127.0.0.1:{port}",
            "HTTPS_PROXY": f"http://127.0.0.1:{port}",
            "http_proxy": f"http://127.0.0.1:{port}",
            "https_proxy": f"http://127.0.0.1:{port}",
            "AGENTCLOAK_BROWSER_PROXY_URL": f"http://127.0.0.1:{port}",
            # Skip the vLLM control-plane traffic through our mitm so we don't
            # capture LLM API calls in capture.jsonl.
            "NO_PROXY": "localhost,127.0.0.1,5000,5001",
            "no_proxy": "localhost,127.0.0.1,5000,5001",
        }
        # Resolved-path for chromium ignoring https cert errors.
        fara_env.setdefault("AGENTCLOAK_IGNORE_HTTPS_ERRORS", "1")

        fara_cmd = [
            "fara-cli",
            "--task", task.instruction,
            "--start_page", task.start_url,
            "--max_rounds", str(max_rounds),
            "--base_url", base_url,
            "--downloads_folder", str(cond_dir / "downloads"),
        ]
        summary["fara_cmd"] = fara_cmd

        print(f"  [{condition}] mitm port={port}, running fara-cli...")
        t0 = time.monotonic()
        proc = subprocess.run(
            fara_cmd,
            env=fara_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        elapsed = time.monotonic() - t0
        stdout_file.write_bytes(proc.stdout or b"")
        stderr_file.write_bytes(proc.stderr or b"")
        summary["return_code"] = proc.returncode
        summary["duration_seconds"] = round(elapsed, 1)
    except subprocess.TimeoutExpired as e:
        summary["return_code"] = -9
        summary["error"] = f"timeout after {timeout_seconds}s"
        if e.stdout:
            stdout_file.write_bytes(e.stdout)
        if e.stderr:
            stderr_file.write_bytes(e.stderr)
    except Exception as e:
        summary["error"] = f"{type(e).__name__}: {e}"
    finally:
        mitm.terminate()
        try:
            mitm.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mitm.kill()
            mitm.wait()
        summary["finished_at"] = datetime.now(tz=timezone.utc).isoformat()

    # Roll up capture summary.
    blocks = {"search": 0, "offdomain": 0, "e6": 0}
    total = 0
    visit_url_calls = 0
    web_search_calls = 0
    if capture_file.exists():
        for line in capture_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            if rec.get("rq3_blocked"):
                blocks[rec.get("rq3_reason", "?")] = (
                    blocks.get(rec.get("rq3_reason", "?"), 0) + 1
                )
            elif rec.get("e6_blocked"):
                blocks["e6"] += 1
            url = rec.get("url", "")
            if "bing.com/search" in url or "google.com/search" in url:
                web_search_calls += 1
            # Heuristic for direct visit_url: any navigation-class request that
            # is not part of a redirect chain. Rough proxy: count documents.
            if rec.get("response_status") == 200 and ".html" in url:
                visit_url_calls += 1

    summary["capture_records"] = total
    summary["rq3_blocks"] = blocks
    summary["search_engine_url_hits"] = web_search_calls
    summary["document_responses"] = visit_url_calls

    # Task-success extraction from agent stdout/stderr.
    # Fara-7B emits one of:
    #   * a "terminate" action with status="success"/"failure" -> reached an end-of-task decision
    #   * exhaust max_rounds -> no terminate (we mark as "hit_max_rounds")
    #   * error stack -> "agent_error"
    # We record the agent's *self-reported* status; ground-truth answer
    # correctness is left to a separate LLM-as-judge pass.
    stdout_text = stdout_file.read_text(errors="replace") if stdout_file.exists() else ""
    stderr_text = stderr_file.read_text(errors="replace") if stderr_file.exists() else ""
    combined = stdout_text + "\n" + stderr_text

    self_status = None
    # Action emission patterns Fara prints in stdout: "'action': 'terminate'", "'status': 'success'"
    if "'action': 'terminate'" in combined or '"action": "terminate"' in combined:
        if "'status': 'success'" in combined or '"status": "success"' in combined:
            self_status = "success"
        elif "'status': 'failure'" in combined or '"status": "failure"' in combined:
            self_status = "failure"
        else:
            self_status = "terminate_unknown_status"
    elif "Error occurred:" in combined or "NotFoundError" in combined or "ConnectionError" in combined:
        self_status = "agent_error"
    elif summary.get("return_code") == 0:
        # Non-zero return is already an error; rc==0 with no terminate means
        # the agent hit max_rounds and exited cleanly.
        self_status = "hit_max_rounds"
    else:
        self_status = "unknown"

    # Best-effort final answer extraction (last non-empty Fara "thoughts").
    final_answer = None
    for line in reversed(combined.splitlines()):
        if "'thoughts'" in line or '"thoughts"' in line:
            final_answer = line.strip()
            break

    summary["agent_self_status"] = self_status
    summary["agent_final_answer_excerpt"] = (final_answer or "")[:500]

    (cond_dir / "stopped.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )
    print(
        f"  [{condition}] rc={summary.get('return_code')} "
        f"records={total} blocks={blocks} dir={cond_dir}"
    )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task-id", default="Amazon--32",
                    help="Master task_id to run (default: Amazon--32).")
    ap.add_argument("--conditions", nargs="+", default=["C0"],
                    help="Conditions (subset of C0/C1/C2/C3).")
    ap.add_argument("--reps", type=int, default=1,
                    help="Number of repetitions to run (rep numbers are "
                         "--start-rep .. --start-rep + reps - 1).")
    ap.add_argument("--start-rep", type=int, default=1,
                    help="First repetition index (use to extend an existing run, "
                         "e.g. --start-rep 2 --reps 2 adds reps 02 and 03).")
    ap.add_argument("--max-rounds", type=int, default=15,
                    help="Fara-7B max_rounds budget (default 15 for smoke).")
    ap.add_argument("--base-url", default="http://localhost:5001/v1",
                    help="Fara-7B vLLM OpenAI-compatible base URL (with /v1 suffix).")
    ap.add_argument("--timeout", type=int, default=420,
                    help="Per-session timeout seconds.")
    args = ap.parse_args()

    task = _load_task(args.task_id)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"[fara-smoke] task={task.task_id}  start_url={task.start_url}")
    print(f"[fara-smoke] conditions={args.conditions} reps={args.reps} "
          f"max_rounds={args.max_rounds}")

    summaries = []
    for cond in args.conditions:
        for rep in range(args.start_rep, args.start_rep + args.reps):
            print(f"\n[fara-smoke] === condition={cond} rep={rep:02d} ===")
            summaries.append(_run_one(
                task, cond, rep,
                max_rounds=args.max_rounds,
                base_url=args.base_url,
                timeout_seconds=args.timeout,
            ))

    agg_path = OUT_ROOT / "fara_ablation_aggregate.json"
    agg_path.write_text(json.dumps(summaries, indent=2, ensure_ascii=False))
    print(f"\n[fara-smoke] aggregate -> {agg_path}")

    # Verdict table.
    print(
        "\n  cond  | rc | records | s-blk | n-blk | cmp-blk |   status     | duration"
    )
    print(
        "  ------+----+---------+-------+-------+---------+--------------+---------"
    )
    for s in summaries:
        b = s.get("rq3_blocks", {})
        print(
            f"  {s['condition']:5s} |"
            f" {str(s.get('return_code', '?')):>2s} |"
            f" {s.get('capture_records', 0):7d} |"
            f" {b.get('search', 0):5d} |"
            f" {b.get('offdomain', 0):5d} |"
            f" {b.get('cmp', 0):7d} |"
            f" {s.get('agent_self_status', '?'):12s} |"
            f" {s.get('duration_seconds', 0):>6}s"
        )


if __name__ == "__main__":
    main()
