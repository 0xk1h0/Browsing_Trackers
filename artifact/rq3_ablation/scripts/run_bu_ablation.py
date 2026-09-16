"""Standalone Browser-Use ablation runner (RQ3 smoke).

Mirror of ``run_fara_ablation.py`` but invokes Browser-Use via its Python API
(``Agent`` + ``BrowserSession`` + ``ChatOpenAI`` pointing at the local bu-30b
vLLM). Per session:

    1. Merge policy-condition env (FARA_DISABLED_ACTIONS, BU_DISABLED_ACTIONS,
       RQ3_BLOCK_SEARCH, RQ3_BLOCK_OFFDOMAIN, RQ3_BLOCK_CMP) into os.environ
       BEFORE importing browser_use, so the ablation patch applies to Tools().
    2. Start a fresh mitmdump on a free port with the same env.
    3. Build a BrowserSession routed through that proxy + an Agent.
    4. Run agent.run(max_steps=...).
    5. Teardown.  Parse capture.jsonl + agent history.

Requires: bu-30b vLLM on http://127.0.0.1:8002/v1 (start via
``CUDA_DEVICES=1 PORT=8002 bash scripts/run_bu_vllm.sh``).

Outputs go to ``data/rq7_policy_ablation/agent=browser-use/condition=*/task=*/rep=*/``.
"""

from __future__ import annotations

import argparse
import asyncio
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

# Strip any stray HTTP proxy env vars that would break the BU bootstrap
# (the runner sets its own proxy when needed inside the per-session block).
for _k in list(os.environ.keys()):
    if _k.lower() in ("http_proxy", "https_proxy", "all_proxy"):
        del os.environ[_k]

# Conservative BU lifecycle timeouts so the agent doesn't fail-fast on a
# slow bu-30b cold start.
os.environ.setdefault("TIMEOUT_BrowserStartEvent", "90")
os.environ.setdefault("TIMEOUT_BrowserLaunchEvent", "90")

from agentcloak.measurement.policy_condition import (
    PolicyCondition,
    policy_condition_env,
    policy_condition_summary,
)

ROOT = Path(__file__).resolve().parent
OUT_ROOT = ROOT / "data" / "rq7_policy_ablation"
ADDON_PATH = ROOT.parent.parent / "agentcloak" / "measurement" / "mitm_capture_addon.py"

VLLM_URL = os.environ.get("BROWSER_USE_BASE_URL", "http://127.0.0.1:8002/v1")
MODEL = os.environ.get("BROWSER_USE_LLM", "browser-use/bu-30b-a3b-preview")


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


def _start_mitmdump(port: int, env: dict) -> subprocess.Popen:
    cmd = [
        "mitmdump",
        "--listen-port", str(port),
        "--set", "block_global=false",
        "--set", "ssl_insecure=true",
        "-s", str(ADDON_PATH),
        "--quiet",
    ]
    return subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env,
    )


async def _run_one(
    task,
    condition: str,
    rep: int,
    *,
    max_steps: int,
    timeout_seconds: int,
) -> dict:
    cond_dir = (
        OUT_ROOT
        / "agent=browser-use"
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

    # Clear ALL policy env keys before applying this condition's delta, so a
    # prior condition's settings cannot leak into this one (the most common
    # mode of failure when running multiple conditions in the same process).
    for k in (
        "FARA_DISABLED_ACTIONS",
        "BU_DISABLED_ACTIONS",
        "RQ3_BLOCK_SEARCH",
        "RQ3_BLOCK_OFFDOMAIN",
        "RQ3_BLOCK_CMP",
        "RQ3_POLICY_CONDITION",
    ):
        os.environ.pop(k, None)

    cond_env = policy_condition_env(condition)
    for k, v in cond_env.items():
        os.environ[k] = v
    os.environ["AGENTCLOAK_CAPTURE_FILE"] = str(capture_file)
    os.environ["AGENTCLOAK_FP_CAPTURE_FILE"] = str(fp_file)

    summary = {
        "agent": "browser-use",
        "task_id": task.task_id,
        "condition": condition,
        "rep": rep,
        "session_dir": str(cond_dir),
        "policy_env": cond_env,
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "max_steps": max_steps,
        "model": MODEL,
        "base_url": VLLM_URL,
    }

    # Start mitm with the condition env (subprocess inherits os.environ).
    port = _free_port()
    mitm_env = {**os.environ}
    mitm = _start_mitmdump(port, mitm_env)

    if not _wait_proxy(port):
        summary["error"] = "mitmdump did not start"
        summary["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
        (cond_dir / "stopped.json").write_text(json.dumps(summary, indent=2))
        return summary

    # Apply the Browser-Use ablation monkeypatch (no-op when BU_DISABLED_ACTIONS unset).
    # Re-import is safe; apply_patch is idempotent.
    import agentcloak.measurement.browser_use_ablation  # noqa: F401
    agentcloak_mod = sys.modules["agentcloak.measurement.browser_use_ablation"]
    # The monkeypatch reads env at import time; for a fresh per-session env we
    # need to reset the flag and re-apply.
    agentcloak_mod._PATCH_APPLIED = False
    agentcloak_mod.apply_patch()

    try:
        from browser_use import Agent, BrowserSession, BrowserProfile
        from browser_use.browser.profile import ProxySettings
        from browser_use.llm.openai.chat import ChatOpenAI

        llm = ChatOpenAI(
            model=MODEL,
            api_key="dummy",
            base_url=VLLM_URL,
            temperature=0.6,
            top_p=0.95,
        )
        chromium_path = os.environ.get("BU_CHROMIUM_PATH", "/snap/bin/chromium")
        profile = BrowserProfile(
            headless=True,
            disable_security=True,
            is_local=True,
            executable_path=chromium_path,
            extensions_enabled=False,
            extra_chromium_args=["--no-sandbox", "--disable-dev-shm-usage"],
            proxy=ProxySettings(
                server=f"http://127.0.0.1:{port}",
                bypass="127.0.0.1,localhost",
            ),
        )
        # NOTE: do NOT pass `timeout=` here. In recent browser-use versions,
        # any non-None timeout kwarg is aliased to `cloud_timeout` and forces
        # use_cloud=True, which then requires BROWSER_USE_API_KEY.  We want
        # local-Chromium only, so omit the kwarg entirely.
        session = BrowserSession(
            browser_profile=profile,
            is_local=True,
            executable_path=chromium_path,
        )
        # Under navigate-ablation Browser-Use Agent's auto-initial-action
        # extractor would emit a {'navigate': ...} action and crash with
        # KeyError because the action was removed from the registry. We
        # therefore disable directly_open_url and instead bring the browser
        # to the start page via a Playwright-level navigation as soon as the
        # session launches.
        task_text = task.instruction

        agent = Agent(
            task=task_text,
            llm=llm,
            browser_session=session,
            max_actions_per_step=3,
            directly_open_url=False,
        )

        # Manually navigate to the start URL via Playwright (independent of
        # the agent's action registry).  This works for ALL conditions and
        # avoids the registry-lookup crash under -nav ablation.
        async def _preload_start_url():
            try:
                await session.start()
                # `session.start()` returns once the browser is up; the
                # current page may be about:blank. Issue a low-level goto.
                page = await session.get_current_page()
                if page is not None:
                    with contextlib.suppress(Exception):
                        await page.goto(task.start_url, timeout=15_000)
            except Exception as e:
                summary.setdefault("preload_error", str(e)[:200])
        await _preload_start_url()

        t0 = time.monotonic()
        success_self = False
        steps = 0
        try:
            result = await asyncio.wait_for(
                agent.run(max_steps=max_steps), timeout=timeout_seconds,
            )
            success_self = (
                result.is_done() if hasattr(result, "is_done") else True
            )
            steps = (
                result.n_steps() if hasattr(result, "n_steps") else 0
            )
            try:
                ablation_report = getattr(
                    agent.tools, "_bu_ablation_report", None
                )
                summary["bu_ablation_report"] = ablation_report
                summary["registered_actions"] = sorted(
                    agent.tools.registry.registry.actions.keys()
                )
            except Exception as e:
                summary["actions_inspect_error"] = str(e)[:200]
            # Best-effort final URL + final answer for diagnostics
            try:
                page = await session.get_current_page()
                if page is not None:
                    summary["final_url"] = page.url
            except Exception:
                pass
            try:
                if hasattr(result, "final_result"):
                    fr = result.final_result()
                    summary["final_answer_excerpt"] = (
                        str(fr)[:300] if fr is not None else None
                    )
                elif hasattr(result, "extracted_content"):
                    summary["final_answer_excerpt"] = str(
                        result.extracted_content()
                    )[:300]
            except Exception:
                pass
        except asyncio.TimeoutError:
            summary["error"] = f"timeout after {timeout_seconds}s"
        except Exception as e:
            summary["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        finally:
            with contextlib.suppress(Exception):
                await session.stop()
            elapsed = time.monotonic() - t0
            summary["duration_seconds"] = round(elapsed, 1)
            summary["steps"] = steps
            summary["agent_self_status"] = (
                "success" if success_self else (
                    "hit_max_steps" if steps >= max_steps else "agent_error"
                )
            )
            summary["return_code"] = 0 if "error" not in summary else 1
    finally:
        mitm.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            mitm.wait(timeout=5)
        if mitm.poll() is None:
            mitm.kill()
            mitm.wait()
        summary["finished_at"] = datetime.now(tz=timezone.utc).isoformat()

    # Roll up the capture file.
    blocks = {"search": 0, "offdomain": 0, "cmp": 0, "e6": 0}
    total = 0
    if capture_file.exists():
        for line in capture_file.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
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

    summary["capture_records"] = total
    summary["rq3_blocks"] = blocks

    (cond_dir / "stopped.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )
    print(
        f"  [{condition}] rc={summary.get('return_code', '?')} "
        f"records={total} blocks={blocks} "
        f"status={summary.get('agent_self_status', '?')} "
        f"dur={summary.get('duration_seconds', '?')}s"
    )
    return summary


async def _async_main(args) -> int:
    task = _load_task(args.task_id)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"[bu-smoke] task={task.task_id}  start_url={task.start_url}")
    print(f"[bu-smoke] conditions={args.conditions} "
          f"reps=[{args.start_rep}..{args.start_rep + args.reps - 1}] "
          f"max_steps={args.max_steps}")
    print(f"[bu-smoke] vLLM at {VLLM_URL}, model={MODEL}")

    summaries = []
    for cond in args.conditions:
        for rep in range(args.start_rep, args.start_rep + args.reps):
            print(f"\n[bu-smoke] === condition={cond} rep={rep:02d} ===")
            s = await _run_one(
                task, cond, rep,
                max_steps=args.max_steps,
                timeout_seconds=args.timeout,
            )
            summaries.append(s)
            # Reset patched flags between sessions so each respects its own env.
            import agentcloak.measurement.browser_use_ablation as bua
            bua._PATCH_APPLIED = False

    agg_path = OUT_ROOT / "bu_ablation_aggregate.json"
    agg_path.write_text(json.dumps(summaries, indent=2, ensure_ascii=False))
    print(f"\n[bu-smoke] aggregate -> {agg_path}")

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
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task-id", default="Amazon--32")
    ap.add_argument("--conditions", nargs="+", default=["C0"],
                    help="Conditions to run (subset of C0..C8).")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--start-rep", type=int, default=1)
    ap.add_argument("--max-steps", type=int, default=15)
    ap.add_argument("--timeout", type=int, default=420)
    args = ap.parse_args()

    sys.exit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
