#!/usr/bin/env python3
"""Measurement harness runner for GUI-Owl-1.5-32B-Think.

Uses the OFFICIAL Mobile-Agent-v3.5 browser_use framework (SoM-based action space).
Outputs a single JSON object to stdout on completion.

Usage:
    python run_gui_owl_agent.py --task <file_or_string>

Env vars:
    GUI_OWL_BASE_URL     vLLM endpoint (default: http://127.0.0.1:8004/v1)
    GUI_OWL_MODEL        model name (default: mPLUG/GUI-Owl-1.5-32B-Think)
    GUI_OWL_MAX_STEPS    max agent iterations (default: 15)
    API_KEY              API key for the vLLM endpoint (default: EMPTY)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import tempfile
from pathlib import Path
from types import SimpleNamespace

# ---------------------------------------------------------------------------
# Resolve the browser_use package location before importing anything from it.
# ---------------------------------------------------------------------------
_BROWSER_USE_DIR = Path(__file__).parent.parent / "models" / "gui_owl" / "browser_use"
if str(_BROWSER_USE_DIR) not in sys.path:
    sys.path.insert(0, str(_BROWSER_USE_DIR))

from browser.playwright.browser_playwright import PlaywrightComputer  # noqa: E402
from agent import Agent  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

URL_RE = re.compile(r"https?://[^\s)\"']+")
START_AT_RE = re.compile(r"(?im)^\s*Start at:\s*(https?://\S+)\s*$")


def _read_task(task_arg: str) -> str:
    if task_arg == "-":
        return sys.stdin.read()
    p = Path(task_arg)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8")
    return task_arg


def _extract_start_url(task_text: str) -> str:
    m = START_AT_RE.search(task_text)
    if m:
        return m.group(1).strip()
    m = URL_RE.search(task_text)
    if m:
        return m.group(0).strip()
    return "https://www.google.com"


def _build_args(task_text: str, start_url: str, task_dir: str) -> SimpleNamespace:
    """Build the args namespace expected by Agent and PlaywrightComputer."""
    max_iter = int(os.environ.get("GUI_OWL_MAX_STEPS", "15"))
    model = os.environ.get("GUI_OWL_MODEL", "mPLUG/GUI-Owl-1.5-32B-Think")
    base_url = os.environ.get("GUI_OWL_BASE_URL", "http://127.0.0.1:8004/v1")

    return SimpleNamespace(
        # task
        task=task_text,
        web=start_url,
        task_dir=task_dir,
        task_id="measurement_run",
        rollout_id="0",
        init_image_path="",
        download_dir="downloads",
        # agent
        model=model,
        base_url=base_url,
        max_iter=max_iter,
        max_tokens=2048,
        temperature=0.0,
        top_p=0.95,
        repetition_penalty=1,
        top_k=20,
        seed=1234,
        image_type="base64",
        provider=None,
        # browser
        headless=True,
        window_width=1080,
        window_height=1440,
        keep_user_info=False,
        highlight_mouse=False,
        use_css_som=False,
        use_omni_som=False,
        omni_url="",
        save_accessibility_tree=False,
        force_device_scale=False,
        fix_box_color=False,
        text_only=False,
        # eval (disabled for measurement harness)
        eval=False,
        eval_only=False,
        eval_mode="",
        eval_model="",
        eval_score_threshold=3,
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

async def _run(task_text: str, task_dir: str) -> dict:
    start_url = _extract_start_url(task_text)
    args = _build_args(task_text, start_url, task_dir)

    t_start = time.monotonic()
    success = False
    final_result = ""
    steps_executed = 0
    error_msg = ""

    try:
        env = PlaywrightComputer(args, highlight_mouse=args.highlight_mouse)
        async with env as web:
            agent = Agent(web, args)
            await agent._agent_loop()

            steps_executed = len(agent.history_action_info)

            # Determine success: last action was "answer"
            if agent.history_action_info and agent.history_action_info[-1]["action"] == "answer":
                success = True
                final_result = (
                    agent.history_action_info[-1]["info"]
                    .get("tool_call", {})
                    .get("arguments", {})
                    .get("text", "")
                )
            else:
                # Terminated early (step limit or error) — pull last action text as result
                if agent.history_action_info:
                    last = agent.history_action_info[-1]
                    final_result = last["info"].get("action_text", "")

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        print(f"[ERROR] Agent run failed: {error_msg}", file=sys.stderr)

    duration = time.monotonic() - t_start

    return {
        "success": success,
        "task_completed": success,
        "final_result": final_result,
        "steps_executed": steps_executed,
        "duration_seconds": round(duration, 2),
        **({"error": error_msg} if error_msg else {}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="GUI-Owl-1.5-32B-Think measurement harness (official browser_use framework)"
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Task text, path to a task file, or '-' to read from stdin",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("GUI_OWL_OUTPUT_DIR", ""),
        help="Directory to store trajectory files (default: auto temp dir)",
    )
    args = parser.parse_args(argv or sys.argv[1:])

    task_text = _read_task(args.task).strip()
    if not task_text:
        print(json.dumps({"success": False, "task_completed": False,
                          "final_result": "", "steps_executed": 0,
                          "duration_seconds": 0.0,
                          "error": "Empty task"}))
        return 1

    # Set up trajectory output directory
    if args.output_dir:
        task_dir = os.path.join(args.output_dir, "measurement_run", "rollout_0")
        os.makedirs(task_dir, exist_ok=True)
        tmp_obj = None
    else:
        tmp_obj = tempfile.TemporaryDirectory(prefix="gui_owl_run_")
        task_dir = os.path.join(tmp_obj.name, "measurement_run", "rollout_0")
        os.makedirs(task_dir, exist_ok=True)

    try:
        result = asyncio.run(_run(task_text, task_dir))
    except KeyboardInterrupt:
        result = {
            "success": False,
            "task_completed": False,
            "final_result": "",
            "steps_executed": 0,
            "duration_seconds": 0.0,
            "error": "Interrupted",
        }
    finally:
        if tmp_obj is not None:
            tmp_obj.cleanup()

    print(json.dumps(result))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
