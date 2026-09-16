"""RQ3 action-space ablation smoke test runner.

Runs the 16-session smoke test specified in the plan:

    2 agents (fara-7b, browser-use) x
    4 conditions (C0/C1/C2/C3) x
    2 tasks (Google Search--28, Amazon--32) x
    1 rep = 16 sessions

Each session merges the policy-condition env from
``agentcloak.measurement.policy_condition.policy_condition_env`` into
``os.environ`` *before* invoking the agent harness, so the disabled-action
env vars and proxy block flags reach both the agent CLI subprocess and the
mitmdump addon (both inherit ``os.environ``).

Outputs land in ``data/rq7_policy_ablation/agent=*/condition=*/task=*/rep=*/``.
For each session we write:

    * ``capture.jsonl``      -- HTTP capture (copied from harness tmpdir)
    * ``js_telemetry.jsonl`` -- JS fingerprint hook telemetry
    * ``stopped.json``       -- run metadata (condition, duration, rc, etc.)

Usage::

    # dry run -- print the plan without running anything
    python -m experiments.rq7_agent_tracker_measurement.rq3_smoke_test

    # actually run
    python -m experiments.rq7_agent_tracker_measurement.rq3_smoke_test --execute

    # narrow scope
    python -m experiments.rq7_agent_tracker_measurement.rq3_smoke_test \
        --execute --agents fara-7b --conditions C0 C3 --tasks-limit 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from agentcloak.measurement.policy_condition import (
    PolicyCondition,
    policy_condition_env,
    policy_condition_summary,
)

# Two-task smoke set from the plan.
SMOKE_TASK_IDS = ["Google Search--28", "Amazon--32"]

# Agents under test.
SMOKE_AGENTS = ["fara-7b", "browser-use"]

# Conditions to run.
ALL_CONDITIONS: list[PolicyCondition] = ["C0", "C1", "C2", "C3"]

# Output root.
ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = ROOT / "data" / "rq7_policy_ablation"


def _load_smoke_tasks(limit: int | None = None) -> list:
    from experiments.rq7_agent_tracker_measurement.load_user_study_tasks import (
        load_master_tasks,
    )

    all_tasks = load_master_tasks()
    chosen = [t for t in all_tasks if t.task_id in SMOKE_TASK_IDS]
    if not chosen:
        raise RuntimeError(
            f"None of the smoke task IDs {SMOKE_TASK_IDS} found in master list."
        )
    if limit:
        chosen = chosen[:limit]
    return chosen


def _session_dir(agent: str, condition: str, task_id: str, rep: int) -> Path:
    safe_task = task_id.replace("/", "_").replace(" ", "_")
    return (
        OUTPUT_ROOT
        / f"agent={agent}"
        / f"condition={condition}"
        / f"task={safe_task}"
        / f"rep={rep:02d}"
    )


def _enumerate_sessions(
    agents: Iterable[str],
    conditions: Iterable[PolicyCondition],
    tasks,
    reps: int,
) -> list[dict]:
    """Return a list of session dicts: {agent, condition, task, rep, dir}."""
    sessions = []
    for agent in agents:
        for condition in conditions:
            for task in tasks:
                for rep in range(1, reps + 1):
                    sessions.append({
                        "agent": agent,
                        "condition": condition,
                        "task": task,
                        "rep": rep,
                        "dir": _session_dir(agent, condition, task.task_id, rep),
                    })
    return sessions


async def _run_one(
    session: dict, harness, timeout_seconds: int
) -> tuple[dict, object | None]:
    """Run a single session, write outputs to its session_dir, return summary."""
    cond_env = policy_condition_env(session["condition"])
    out_dir: Path = session["dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # Merge condition env -- subprocess inherits os.environ.
    prior_env_snapshot = {k: os.environ.get(k) for k in cond_env}
    os.environ.update(cond_env)

    summary = {
        "agent": session["agent"],
        "task_id": session["task"].task_id,
        "condition": session["condition"],
        "rep": session["rep"],
        "session_dir": str(out_dir),
        "policy_env": cond_env,
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    result = None
    try:
        t0 = time.monotonic()
        result = await harness.run(
            agent_name=session["agent"],
            task=session["task"].instruction,
            target_domain=session["task"].domain,
            timeout=timeout_seconds,
            start_url=session["task"].start_url,
        )
        elapsed = time.monotonic() - t0

        summary["return_code"] = result.return_code
        summary["timed_out"] = result.timed_out
        summary["duration_seconds"] = elapsed
        summary["raw_capture_count"] = len(result.raw_capture_records)
        summary["network_request_count"] = len(result.network_requests)

        # Persist stdout/stderr so we can debug rc != 0.
        with open(out_dir / "agent.stdout.log", "w", encoding="utf-8") as f:
            f.write(result.stdout or "")
        with open(out_dir / "agent.stderr.log", "w", encoding="utf-8") as f:
            f.write(result.stderr or "")

        # Copy capture artefacts from harness temp dir to our session_dir.
        # The harness writes to <tmpdir>/traffic.jsonl; we serialize the
        # post-processed records here for analysis convenience.
        with open(out_dir / "capture.jsonl", "w", encoding="utf-8") as f:
            for rec in result.raw_capture_records:
                f.write(json.dumps(rec) + "\n")
        # JS telemetry is in fp_calls.json next to traffic.jsonl;
        # harness AgentRunResult.js_api_calls is the parsed form.
        with open(out_dir / "js_telemetry.jsonl", "w", encoding="utf-8") as f:
            for call in result.js_api_calls:
                payload = (
                    call.model_dump() if hasattr(call, "model_dump") else str(call)
                )
                f.write(json.dumps(payload) + "\n")
    except Exception as e:
        summary["error"] = f"{type(e).__name__}: {e}"
    finally:
        summary["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
        with open(out_dir / "stopped.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        # Restore prior env to avoid leaking across sessions.
        for k, v in prior_env_snapshot.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    return summary, result


async def _async_main(args) -> int:
    tasks = _load_smoke_tasks(limit=args.tasks_limit)
    sessions = _enumerate_sessions(
        agents=args.agents,
        conditions=args.conditions,
        tasks=tasks,
        reps=args.reps,
    )

    print(f"[smoke] planned {len(sessions)} sessions:")
    for s in sessions:
        print(
            f"  - agent={s['agent']:12s} cond={s['condition']:3s} "
            f"task={s['task'].task_id:25s} rep={s['rep']:02d} -> {s['dir'].relative_to(ROOT)}"
        )
    print()
    print("[smoke] policy env per condition:")
    for c in args.conditions:
        print(f"  {policy_condition_summary(c)}")

    if not args.execute:
        print(
            "\n[smoke] dry run; pass --execute to invoke the harness. "
            "Note: real execution requires Fara-7B (GPU+vLLM) and Browser-Use "
            "(OPENAI_API_KEY) prerequisites."
        )
        return 0

    # Lazily import so dry-run path does not require these deps.
    from agentcloak.measurement.agent_harness import AgentHarness
    # Apply Browser-Use ablation patch (no-op if BU_DISABLED_ACTIONS unset).
    import agentcloak.measurement.browser_use_ablation  # noqa: F401

    harness = AgentHarness(timeout_seconds=args.timeout)

    summaries = []
    for s in sessions:
        print(
            f"\n[smoke] running agent={s['agent']} cond={s['condition']} "
            f"task={s['task'].task_id} rep={s['rep']:02d}"
        )
        summary, _ = await _run_one(s, harness, timeout_seconds=args.timeout)
        rc = summary.get("return_code", "?")
        err = summary.get("error", "")
        print(f"  -> rc={rc} err={err or 'none'} dir={summary['session_dir']}")
        summaries.append(summary)

    # Aggregate report.
    aggregate_path = OUTPUT_ROOT / "smoke_aggregate.json"
    aggregate_path.parent.mkdir(parents=True, exist_ok=True)
    with open(aggregate_path, "w", encoding="utf-8") as f:
        json.dump(summaries, f, indent=2, ensure_ascii=False)
    print(f"\n[smoke] wrote aggregate report -> {aggregate_path}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true",
                    help="Actually run sessions (default: dry-run plan only).")
    ap.add_argument("--agents", nargs="+", default=SMOKE_AGENTS,
                    help="Subset of agents to run.")
    ap.add_argument("--conditions", nargs="+", default=ALL_CONDITIONS,
                    choices=ALL_CONDITIONS,
                    help="Subset of conditions to run.")
    ap.add_argument("--tasks-limit", type=int, default=None,
                    help="Use only the first N smoke tasks.")
    ap.add_argument("--reps", type=int, default=1,
                    help="Repetitions per (agent, condition, task) cell.")
    ap.add_argument("--timeout", type=int, default=300,
                    help="Per-session timeout in seconds (default 300).")
    args = ap.parse_args()

    sys.exit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
