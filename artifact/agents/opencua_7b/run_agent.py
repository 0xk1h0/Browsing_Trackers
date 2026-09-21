#!/usr/bin/env python3
"""OpenCUA-32B browser runner for AgentCloak measurements.

Uses the OFFICIAL OpenCUA L3_SHORT system prompt and action parsing logic
from /tmp/opencua/evaluation/agentnetbench/agent/opencua.py.

Key design points:
- L3_SHORT system prompt: Observation / Thought / Action with pyautogui output
- Official parse_response() + extract_actions() logic (ported verbatim)
- History: last MAX_PREV_IMAGES=2 screenshots + action strings in context
- pyautogui actions converted to Playwright calls
- computer.terminate(status=...) ends the run
- JSON result written to stdout; diagnostics to stderr
- Proxy from HTTP_PROXY/HTTPS_PROXY env vars
- Env vars: OPENCUA_BASE_URL, OPENCUA_MODEL, OPENCUA_MAX_STEPS, OPENAI_API_KEY
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


# ---------------------------------------------------------------------------
# Official OpenCUA L3_SHORT system prompt (verbatim from opencua.py)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a GUI agent. You are given a task and a screenshot of the screen. "
    "You need to perform a series of pyautogui actions to complete the task.\n\n"
    "For each step, provide your response in this format:\n\n"
    "Observation: Describe the current computer state based on the full screenshot "
    "in detail. Provide any information that is possibly relevant to achieving the "
    "task goal and any elements that may affect the task execution, such as pop-ups, "
    "notifications, error messages, loading states, etc..\n\n"
    "Thought:\n"
    "  - Step by Step Progress Assessment:\n"
    "    - Analyze completed task parts and their contribution to the overall goal\n"
    "    - Reflect on potential errors, unexpected results, or obstacles\n"
    "    - If previous action was incorrect, predict a logical recovery step\n"
    "  - Next Action Analysis:\n"
    "    - List possible next actions based on current state\n"
    "    - Evaluate options considering current state and previous actions\n"
    "    - Propose most logical next action\n"
    "    - Anticipate consequences of the proposed action\n\n"
    "Action:\n"
    "  Provide clear, concise, and actionable instructions.\n\n"
    'Finally, output the action as PyAutoGUI code or the following functions:\n'
    '- {"name": "computer.triple_click", "description": "Triple click on the screen", '
    '"parameters": {"type": "object", "properties": {'
    '"x": {"type": "number", "description": "The x coordinate of the triple click"}, '
    '"y": {"type": "number", "description": "The y coordinate of the triple click"}}, '
    '"required": ["x", "y"]}}\n'
    '- {"name": "computer.terminate", "description": "Terminate the current task and '
    'report its completion status", "parameters": {"type": "object", "properties": {'
    '"status": {"type": "string", "enum": ["success", "failure"], '
    '"description": "The status of the task"}}, "required": ["status"]}}'
)

# From opencua.py INSTRUTION_TEMPLATE (note: original has typo "INSTRUTION")
INSTRUCTION_TEMPLATE = (
    "\n# Task Instruction:\n{instruction}\n\n"
    "Please generate the next move according to the screenshot, task instruction "
    "and previous steps (if provided).\n"
)

# From opencua.py
STEP_TEMPLATE = "# Step {step_num}:\n"
ACTION_HISTORY_TEMPLATE = "## Action:\n{action}\n"

# image_3 mode: include up to 2 previous screenshots + 1 current
MAX_PREV_IMAGES = 2

URL_RE = re.compile(r"https?://[^\s)]+")
START_AT_RE = re.compile(r"(?im)^\s*Start at:\s*(https?://\S+)\s*$")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

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


def _proxy_settings_from_env() -> dict[str, str] | None:
    proxy_url = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        return None
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server = f"{server}:{parsed.port}"
    proxy: dict[str, str] = {"server": server}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


def _encode_image(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")


def _image_content(b64: str) -> dict[str, Any]:
    return {
        "type": "image_url",
        "image_url": {
            "detail": "auto",
            "url": f"data:image/png;base64,{b64}",
        },
    }


# ---------------------------------------------------------------------------
# Official OpenCUA action parsing (ported verbatim from opencua.py)
# No coordinate normalization — we pass pixel coordinates directly to Playwright.
# ---------------------------------------------------------------------------

def parse_response(response: str) -> str | None:
    """Extract pyautogui/computer.* lines from model output.

    Mirrors OpenCUA.parse_response() without the qwen25 coordinate normalization
    (Playwright takes absolute pixel coordinates directly).
    """
    if response is None:
        return None

    lines = response.split("\n")
    action_lines: list[str] = []

    # First pass: lines that start with a command
    for raw in lines:
        line = raw.strip()
        if line.startswith("pyautogui.") or line.startswith("computer."):
            action_lines.append(line)

    if action_lines:
        return "\n".join(action_lines)

    # Second pass: find commands embedded within lines
    for raw in lines:
        line = raw.strip()
        if "pyautogui." in line:
            parts = line.split("pyautogui.")
            action_lines.append("pyautogui." + parts[1].strip())
        elif "computer." in line:
            parts = line.split("computer.")
            action_lines.append("computer." + parts[1].strip())

    return "\n".join(action_lines) if action_lines else None


def extract_actions(action: str) -> list[tuple[str, Any]]:
    """Extract (type, value) tuples from parsed action string.

    Mirrors OpenCUA.extract_actions() verbatim.
    """
    if not action:
        return []

    actions: list[tuple[str, Any]] = []
    action_lines = action.strip().split("\n")

    for raw in action_lines:
        line = raw.strip()

        # computer.terminate
        if line.startswith("computer.terminate"):
            status_match = re.search(r"status=['\"](\w+)['\"]", line)
            if status_match:
                actions.append(("terminate", status_match.group(1)))
                continue

        # computer.triple_click
        if line.startswith("computer.triple_click"):
            coord_match = re.search(r"x=([\d.]+),\s*y=([\d.]+)", line)
            if coord_match:
                x, y = map(float, coord_match.groups())
                actions.append(("triple_click", (x, y)))
                continue

        # pyautogui.*
        if line.startswith("pyautogui."):
            coord_match = re.search(r"x=([\d.]+),\s*y=([\d.]+)", line)
            if coord_match:
                x, y = map(float, coord_match.groups())
                if "click" in line and "doubleClick" not in line and "rightClick" not in line:
                    actions.append(("click", (x, y)))
                elif "moveTo" in line:
                    actions.append(("moveTo", (x, y)))
                elif "doubleClick" in line:
                    actions.append(("doubleClick", (x, y)))
                elif "rightClick" in line:
                    actions.append(("rightClick", (x, y)))
                elif "dragTo" in line:
                    actions.append(("dragTo", (x, y)))

            # write(message=...)
            write_match = re.search(r"message=['\"](.+?)['\"]", line)
            if write_match:
                actions.append(("write", write_match.group(1)))

            # write('...') positional — only if message= not found
            if not write_match:
                write_pos = re.search(r"pyautogui\.write\((['\"])(.*?)\1\)", line)
                if write_pos:
                    actions.append(("write", write_pos.group(2)))

            # typewrite('text', ...) — handles interval= kwargs
            typewrite_match = re.search(r"pyautogui\.typewrite\((['\"])(.*?)\1", line)
            if typewrite_match:
                actions.append(("write", typewrite_match.group(2)))

            # press/hotkey keys=[...]
            keys_match = re.findall(r"keys=\[(.*?)\]", line)
            if keys_match:
                key_string = keys_match[0]
                key_list = re.findall(r"['\"]([^'\"]*)['\"]|(\w+)", key_string)
                keys = [m[0] or m[1] for m in key_list if m[0] or m[1]]
                normalized: list[str] = [
                    "ctrl" if k.strip().lower() in ("cmd", "command") else k.strip()
                    for k in keys
                ]
                if "hotkey" in line:
                    actions.append(("hotkey", normalized))
                else:
                    actions.append(("press", normalized))

            # hotkey positional: pyautogui.hotkey('ctrl', 'v')
            if "hotkey(" in line and "keys=" not in line:
                inside = re.search(r"pyautogui\.hotkey\((.*)\)", line)
                if inside:
                    parts = re.findall(r"['\"]([^'\"]+)['\"]", inside.group(1))
                    if parts:
                        normalized = [
                            "ctrl" if p.strip().lower() in ("cmd", "command") else p.strip()
                            for p in parts
                        ]
                        actions.append(("hotkey", normalized))

            # press positional: pyautogui.press('enter') or pyautogui.press(['ctrl','v'])
            if "press(" in line and "keys=" not in line:
                inside = re.search(r"pyautogui\.press\((.*)\)", line)
                if inside:
                    arg_str = inside.group(1).strip()
                    press_keys: list[str] = []
                    if arg_str.startswith("["):
                        parts = re.findall(r"['\"]([^'\"]+)['\"]", arg_str)
                        press_keys = [p.strip() for p in parts]
                    else:
                        one = re.search(r"['\"]([^'\"]+)['\"]", arg_str)
                        if one:
                            press_keys = [one.group(1).strip()]
                    if press_keys:
                        norm = [
                            "ctrl" if k.lower() in ("cmd", "command") else k
                            for k in press_keys
                        ]
                        if len(norm) > 1:
                            actions.append(("hotkey", norm))
                        else:
                            actions.append(("press", norm))

            # scroll: pyautogui.scroll(-3)
            scroll_match = re.search(r"pyautogui\.scroll\(([-\d]+)\)", line)
            if scroll_match:
                actions.append(("scroll", int(scroll_match.group(1))))

    return actions


# ---------------------------------------------------------------------------
# Key name mapping: pyautogui → Playwright
# ---------------------------------------------------------------------------

_KEY_MAP: dict[str, str] = {
    "ctrl": "Control",
    "control": "Control",
    "alt": "Alt",
    "shift": "Shift",
    "cmd": "Meta",
    "command": "Meta",
    "win": "Meta",
    "enter": "Enter",
    "return": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "del": "Delete",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "space": "Space",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
    "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
    "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
}


def _map_key(key: str) -> str:
    k = key.strip().lower()
    return _KEY_MAP.get(k, key.strip())


# ---------------------------------------------------------------------------
# Execute one extracted action via Playwright
# Returns: (done, observation, final_status_or_None)
# ---------------------------------------------------------------------------

async def _execute_action(
    page: Page,
    action_type: str,
    action_value: Any,
) -> tuple[bool, str, str | None]:
    if action_type == "terminate":
        status = str(action_value)
        return True, f"Terminated with status={status}", status

    if action_type == "click":
        x, y = int(action_value[0]), int(action_value[1])
        await page.mouse.click(x, y)
        return False, f"Clicked at ({x}, {y})", None

    if action_type == "doubleClick":
        x, y = int(action_value[0]), int(action_value[1])
        await page.mouse.dblclick(x, y)
        return False, f"Double-clicked at ({x}, {y})", None

    if action_type == "rightClick":
        x, y = int(action_value[0]), int(action_value[1])
        await page.mouse.click(x, y, button="right")
        return False, f"Right-clicked at ({x}, {y})", None

    if action_type == "triple_click":
        x, y = int(action_value[0]), int(action_value[1])
        await page.mouse.click(x, y, click_count=3)
        return False, f"Triple-clicked at ({x}, {y})", None

    if action_type == "moveTo":
        x, y = int(action_value[0]), int(action_value[1])
        await page.mouse.move(x, y)
        return False, f"Moved to ({x}, {y})", None

    if action_type == "dragTo":
        # dragTo normally follows a moveTo; move mouse to destination
        x, y = int(action_value[0]), int(action_value[1])
        await page.mouse.move(x, y)
        return False, f"Dragged to ({x}, {y})", None

    if action_type == "write":
        text = str(action_value)
        await page.keyboard.type(text)
        return False, f"Typed: {text[:80]!r}", None

    if action_type == "press":
        keys = action_value if isinstance(action_value, list) else [action_value]
        mapped = _map_key(keys[0]) if keys else "Enter"
        await page.keyboard.press(mapped)
        return False, f"Pressed {mapped}", None

    if action_type == "hotkey":
        keys = action_value if isinstance(action_value, list) else [action_value]
        combo = "+".join(_map_key(k) for k in keys)
        await page.keyboard.press(combo)
        return False, f"Hotkey {combo}", None

    if action_type == "scroll":
        # pyautogui.scroll(n): positive=up, negative=down; 100px per unit
        amount = int(action_value)
        delta_y = -amount * 100  # positive pyautogui scroll = wheel up = negative deltaY
        await page.mouse.wheel(0, delta_y)
        direction = "up" if delta_y < 0 else "down"
        return False, f"Scrolled {direction} by {abs(delta_y)}px", None

    return False, f"Unknown action type '{action_type}', skipped", None


# ---------------------------------------------------------------------------
# Build OpenCUA-style messages with screenshot history
# ---------------------------------------------------------------------------

def _build_messages(
    task_text: str,
    current_b64: str,
    history_screenshots: list[str],  # oldest-first, up to MAX_PREV_IMAGES
    history_actions: list[str],       # parsed_action_str per step
    step_num: int,
) -> list[dict[str, Any]]:
    """Build the message list following OpenCUA prompt() method structure.

    Layout:
      [system]
      For each previous step that has a screenshot:
        [user: screenshot]
        [assistant: step header + action history]
      [user: current screenshot + instruction text]
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    base_step = step_num - len(history_screenshots)
    for i, (prev_b64, prev_action) in enumerate(zip(history_screenshots, history_actions)):
        prev_step_num = base_step + i
        messages.append({
            "role": "user",
            "content": [_image_content(prev_b64)],
        })
        messages.append({
            "role": "assistant",
            "content": (
                STEP_TEMPLATE.format(step_num=prev_step_num)
                + ACTION_HISTORY_TEMPLATE.format(action=prev_action)
            ),
        })

    messages.append({
        "role": "user",
        "content": [
            _image_content(current_b64),
            {
                "type": "text",
                "text": INSTRUCTION_TEMPLATE.format(instruction=task_text),
            },
        ],
    })

    return messages


# ---------------------------------------------------------------------------
# Raw HTTP model call (no openai SDK dependency)
# ---------------------------------------------------------------------------

def _chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
) -> str:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    req = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {err[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to reach LLM endpoint {endpoint}: {exc}") from exc

    try:
        return str(body["choices"][0]["message"]["content"])
    except Exception as exc:
        raise RuntimeError(
            f"Unexpected LLM response schema: {json.dumps(body)[:1000]}"
        ) from exc


# ---------------------------------------------------------------------------
# Main run loop
# ---------------------------------------------------------------------------

async def _run(args: argparse.Namespace) -> int:
    task_text = _read_task(args.task).strip()
    if not task_text:
        print("Empty task.", file=sys.stderr)
        return 2

    start_url = _extract_start_url(task_text)
    proxy = _proxy_settings_from_env()

    # Rolling screenshot + action history capped at MAX_PREV_IMAGES
    history_screenshots: list[str] = []
    history_actions: list[str] = []

    result: dict[str, Any] = {
        "task": task_text,
        "start_url": start_url,
        "steps": [],
        "final_status": None,
        "terminated": False,
    }

    async with async_playwright() as pw:
        launch_kwargs: dict[str, Any] = {
            "headless": args.headless,
            "args": [
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--ignore-certificate-errors",
                "--allow-insecure-localhost",
            ],
        }
        if proxy:
            launch_kwargs["proxy"] = proxy

        browser: Browser = await pw.chromium.launch(**launch_kwargs)
        context: BrowserContext = await browser.new_context(
            ignore_https_errors=True,
            viewport={"width": args.viewport_width, "height": args.viewport_height},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            ),
        )
        page: Page = await context.new_page()

        try:
            await page.goto(
                start_url, wait_until="domcontentloaded", timeout=args.nav_timeout_ms
            )
            print(f"Opened start URL: {start_url}", file=sys.stderr)
        except Exception as exc:
            print(f"Failed to open start URL {start_url}: {exc}", file=sys.stderr)

        for step_i in range(1, args.max_steps + 1):
            screenshot_bytes = await page.screenshot(type="png", full_page=False)
            current_b64 = _encode_image(screenshot_bytes)
            current_url = page.url or "about:blank"

            messages = _build_messages(
                task_text=task_text,
                current_b64=current_b64,
                history_screenshots=history_screenshots,
                history_actions=history_actions,
                step_num=step_i,
            )

            try:
                model_text = await asyncio.to_thread(
                    _chat_completion,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    model=args.model,
                    messages=messages,
                    max_tokens=args.max_tokens,
                )
            except Exception as exc:
                print(f"Step {step_i}: model call failed: {exc}", file=sys.stderr)
                result["steps"].append({
                    "step": step_i,
                    "url": current_url,
                    "error": str(exc),
                })
                await asyncio.sleep(1.0)
                continue

            print(f"--- Step {step_i} model output ---\n{model_text}", file=sys.stderr)

            parsed_action_str = parse_response(model_text)
            if parsed_action_str is None:
                print(f"Step {step_i}: no action parsed", file=sys.stderr)
                result["steps"].append({
                    "step": step_i,
                    "url": current_url,
                    "raw_output": model_text,
                    "parsed_action": None,
                    "observation": "no action parsed",
                })
                # Still push to history so model sees its previous (empty) turn
                history_screenshots.append(current_b64)
                history_actions.append("(no action)")
                if len(history_screenshots) > MAX_PREV_IMAGES:
                    history_screenshots.pop(0)
                    history_actions.pop(0)
                continue

            print(f"Step {step_i} parsed: {parsed_action_str}", file=sys.stderr)
            actions = extract_actions(parsed_action_str)

            step_record: dict[str, Any] = {
                "step": step_i,
                "url": current_url,
                "raw_output": model_text,
                "parsed_action": parsed_action_str,
                "executed_actions": [],
                "observation": "",
                "done": False,
            }

            done = False
            observations: list[str] = []

            for action_type, action_value in actions:
                try:
                    done, obs, status = await _execute_action(page, action_type, action_value)
                    observations.append(obs)
                    step_record["executed_actions"].append({
                        "type": action_type,
                        "value": str(action_value),
                        "observation": obs,
                    })
                    if done:
                        step_record["done"] = True
                        result["terminated"] = True
                        result["final_status"] = status
                        break
                except Exception as exc:
                    err_obs = f"Action {action_type} failed: {exc}"
                    observations.append(err_obs)
                    step_record["executed_actions"].append({
                        "type": action_type,
                        "value": str(action_value),
                        "error": str(exc),
                    })

            step_record["observation"] = "; ".join(observations)
            result["steps"].append(step_record)
            print(f"Step {step_i}: {step_record['observation']}", file=sys.stderr)

            # Update rolling history
            history_screenshots.append(current_b64)
            history_actions.append(parsed_action_str)
            if len(history_screenshots) > MAX_PREV_IMAGES:
                history_screenshots.pop(0)
                history_actions.pop(0)

            if done:
                print(
                    f"Task terminated with status={result['final_status']}",
                    file=sys.stderr,
                )
                break

            await asyncio.sleep(0.5)

        await context.close()
        await browser.close()

    if not result["terminated"]:
        result["final_status"] = "max_steps_reached"

    print(json.dumps(result, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="OpenCUA-32B runner for AgentCloak measurements"
    )
    p.add_argument(
        "--task", required=True,
        help="Task text, '-' for stdin, or path to task file",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("OPENCUA_BASE_URL", "http://127.0.0.1:8003/v1"),
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("OPENAI_API_KEY", "EMPTY"),
    )
    p.add_argument(
        "--model",
        default=os.environ.get("OPENCUA_MODEL", "xlangai/OpenCUA-32B"),
    )
    p.add_argument(
        "--max-steps",
        type=int,
        default=int(os.environ.get("OPENCUA_MAX_STEPS", "15")),
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.environ.get("OPENCUA_MAX_TOKENS", "1024")),
    )
    p.add_argument(
        "--viewport-width",
        type=int,
        default=int(os.environ.get("OPENCUA_VIEWPORT_WIDTH", "1440")),
    )
    p.add_argument(
        "--viewport-height",
        type=int,
        default=int(os.environ.get("OPENCUA_VIEWPORT_HEIGHT", "900")),
    )
    p.add_argument(
        "--nav-timeout-ms",
        type=int,
        default=int(os.environ.get("OPENCUA_NAV_TIMEOUT_MS", "30000")),
    )
    p.add_argument("--headless", action="store_true", default=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
