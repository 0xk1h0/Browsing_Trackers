"""643-task WebVoyager sensitivity runner (parallel-shard friendly).

Runs Fara-7B or Browser-Use on a shard of the full WebVoyager 643-task pool
under a single ablation condition.  Designed to be launched 4-way in parallel:

    Worker 0  Fara on GPU 0 (vLLM port 5001/v1)  -> task shard 0
    Worker 1  Fara on GPU 2 (vLLM port 5101/v1)  -> task shard 1
    Worker 2  BU   on GPU 1 (vLLM port 8002/v1)  -> task shard 0
    Worker 3  BU   on GPU 3 (vLLM port 8102/v1)  -> task shard 1

Each worker iterates over its shard sequentially.  Each (task, condition) pair
is run in a fresh subprocess of ``run_fara_ablation.py`` or ``run_bu_ablation.py``
to avoid in-process state leakage across sessions.

CLI::

    python -m experiments.rq7_agent_tracker_measurement.run_643_sensitivity \\
        --agent fara-7b --base-url http://localhost:5001/v1 \\
        --conditions C0 C2 C7 C8 \\
        --shard 0 --num-shards 2 \\
        --output-root data/rq7_policy_ablation_643 \\
        --max-rounds 8 --timeout 240
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARTIFACT_ROOT = ROOT.parents[1]
WEBVOYAGER_FILE = Path(
    os.environ.get("WEBVOYAGER_FILE",
                   str(ARTIFACT_ROOT / "data" / "webvoyager" / "WebVoyager_data.jsonl"))
)
DEFAULT_OUTPUT = Path(
    os.environ.get("RQ3_RAW_OUTPUT_ROOT",
                   str(ARTIFACT_ROOT / "rq3_ablation" / "raw" / "rq7_policy_ablation_643"))
)


def _load_webvoyager_tasks() -> list[dict]:
    tasks = []
    with open(WEBVOYAGER_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            tasks.append(json.loads(line))
    return tasks


def _safe_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")


def _session_dir(output_root: Path, agent: str, condition: str,
                 task_id: str, rep: int) -> Path:
    return (
        output_root
        / f"agent={agent}"
        / f"condition={condition}"
        / f"task={_safe_id(task_id)}"
        / f"rep={rep:02d}"
    )


def _has_stopped(session_dir: Path) -> bool:
    """Skip a session if stopped.json already exists (idempotent restart)."""
    return (session_dir / "stopped.json").exists()


def _run_one_session(
    agent: str,
    condition: str,
    task: dict,
    base_url: str,
    output_root: Path,
    max_rounds: int,
    timeout: int,
    worker_id: int,
) -> dict:
    """Spawn a fresh subprocess running one session (task × condition).

    Returns a dict summary.
    """
    task_id = task["id"]
    web_name = task.get("web_name", "unknown")
    web_url = task["web"]
    instruction = task["ques"]

    sdir = _session_dir(output_root, agent, condition, task_id, rep=1)
    if _has_stopped(sdir):
        return {"task_id": task_id, "condition": condition,
                "status": "skipped_exists", "session_dir": str(sdir)}

    sdir.mkdir(parents=True, exist_ok=True)

    # Build the subprocess invocation.  We piggy-back on the per-session
    # _run_one logic in the existing runners by passing the task data via
    # env vars, then invoking a tiny inline Python that constructs the
    # BenchmarkTask and calls _run_one directly.
    env = {**os.environ}
    env["BROWSER_USE_BASE_URL"] = base_url
    env["FARA_BASE_URL_OVERRIDE"] = base_url  # consumed by inline script

    # Inline driver: builds BenchmarkTask, calls the per-session runner
    # function.  This avoids modifying run_fara_ablation.py to accept
    # WebVoyager tasks directly.
    inline = f'''
import asyncio, os, sys, json
from pathlib import Path

sys.path.insert(0, os.environ.get("AGENTCLOAK_REPO_ROOT", str(Path.cwd())))
from agentcloak.measurement.benchmark_tasks import BenchmarkTask

agent = {agent!r}
condition = {condition!r}
task_dict = {json.dumps(task)}
output_root = Path({str(output_root)!r})
max_rounds = {max_rounds}
timeout = {timeout}
base_url = {base_url!r}

start_url = BenchmarkTask.normalize_start_url(task_dict["web"])
bt = BenchmarkTask(
    task_id=task_dict["id"],
    benchmark="webvoyager",
    split="643",
    instruction=task_dict["ques"],
    start_url=start_url,
    domain=task_dict.get("web_name") or BenchmarkTask.extract_domain(start_url),
    metadata={{}},
    tags=["webvoyager643"],
)

# Override the output-root constant the runners use.
import experiments.rq7_agent_tracker_measurement.run_fara_ablation as fr
import experiments.rq7_agent_tracker_measurement.run_bu_ablation as br
fr.OUT_ROOT = output_root
br.OUT_ROOT = output_root

if agent == "fara-7b":
    summary = fr._run_one(
        bt, condition, rep=1,
        max_rounds=max_rounds,
        base_url=base_url,
        timeout_seconds=timeout,
    )
elif agent == "browser-use":
    # BU runner uses env var for base_url, set it before invoke
    os.environ["BROWSER_USE_BASE_URL"] = base_url
    br.VLLM_URL = base_url
    summary = asyncio.run(br._run_one(
        bt, condition, rep=1,
        max_steps=max_rounds,
        timeout_seconds=timeout,
    ))
else:
    raise SystemExit(f"unknown agent: {{agent}}")
print("DONE", json.dumps({{"task_id": bt.task_id, "rc": summary.get("return_code")}}))
'''

    log_file = sdir / "worker.log"
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", inline],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout + 60,  # subprocess wrapper timeout
            check=False,
        )
        elapsed = time.monotonic() - t0
        log_file.write_bytes(proc.stdout or b"")
        return {
            "worker": worker_id,
            "agent": agent,
            "condition": condition,
            "task_id": task_id,
            "session_dir": str(sdir),
            "rc": proc.returncode,
            "duration_seconds": round(elapsed, 1),
        }
    except subprocess.TimeoutExpired as e:
        elapsed = time.monotonic() - t0
        log_file.write_bytes(e.stdout or b"")
        return {
            "worker": worker_id,
            "agent": agent,
            "condition": condition,
            "task_id": task_id,
            "session_dir": str(sdir),
            "rc": -9,
            "error": "subprocess_timeout",
            "duration_seconds": round(elapsed, 1),
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--agent", required=True, choices=["fara-7b", "browser-use"])
    ap.add_argument("--base-url", required=True,
                    help="vLLM /v1 endpoint, e.g. http://localhost:5001/v1")
    ap.add_argument("--conditions", nargs="+", required=True,
                    help="Conditions to run (subset of C0..C8)")
    ap.add_argument("--shard", type=int, default=0,
                    help="Shard index (0-based)")
    ap.add_argument("--num-shards", type=int, default=1,
                    help="Total number of shards (workers per agent)")
    ap.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--max-rounds", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--worker-id", type=int, default=0,
                    help="Worker label for logging")
    args = ap.parse_args()

    all_tasks = _load_webvoyager_tasks()
    # Round-robin shard
    shard_tasks = [
        t for i, t in enumerate(all_tasks)
        if i % args.num_shards == args.shard
    ]
    total_units = len(shard_tasks) * len(args.conditions)
    print(f"[worker {args.worker_id}] agent={args.agent} shard={args.shard}/{args.num_shards} "
          f"tasks={len(shard_tasks)} conditions={args.conditions} "
          f"total_units={total_units}")
    print(f"[worker {args.worker_id}] base_url={args.base_url}")

    args.output_root.mkdir(parents=True, exist_ok=True)
    progress_path = args.output_root / f"worker_{args.worker_id}_progress.jsonl"

    done = 0
    skipped = 0
    failed = 0
    t_start = time.monotonic()
    for cond in args.conditions:
        for task in shard_tasks:
            summary = _run_one_session(
                agent=args.agent,
                condition=cond,
                task=task,
                base_url=args.base_url,
                output_root=args.output_root,
                max_rounds=args.max_rounds,
                timeout=args.timeout,
                worker_id=args.worker_id,
            )
            done += 1
            if summary.get("status") == "skipped_exists":
                skipped += 1
            elif (summary.get("rc") or 0) != 0:
                failed += 1
            with open(progress_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(summary, ensure_ascii=False) + "\n")
            elapsed_min = (time.monotonic() - t_start) / 60.0
            if done % 10 == 0 or done == total_units:
                eta_min = (elapsed_min / done) * (total_units - done) if done else 0
                print(
                    f"[worker {args.worker_id}] {done}/{total_units} "
                    f"(skip {skipped}, fail {failed}) "
                    f"elapsed {elapsed_min:.1f}min eta {eta_min:.1f}min "
                    f"last: {summary.get('agent')} {summary.get('condition')} "
                    f"{summary.get('task_id')} rc={summary.get('rc')}"
                )

    print(f"[worker {args.worker_id}] DONE: {done}/{total_units} "
          f"(skipped {skipped}, failed {failed})")


if __name__ == "__main__":
    main()
