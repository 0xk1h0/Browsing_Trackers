#!/usr/bin/env python3
"""Dedicated SoM-GLM runner for AgentCloak measurements.

This bypasses Magentic orchestration and drives a local browser directly.
It supports either OpenAI-style tool_calls or text-form action outputs.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import base64
import json
import os
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, os.environ.get("SOM_GLM_AGENT_PATH",
                                "${PROJECT_ROOT}/models/GLM-V/examples/gui-agent/glm-41v"))
try:
    from gui_agent_41v import get_pc_prompt, parse_pc_response, build_history_images
except ImportError as e:
    print(f"Warning: Could not import gui_agent_41v modules: {e}")
    # Fallbacks in case code does not exist exactly
    parse_pc_response = None
    get_pc_prompt = None

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


URL_RE = re.compile(r"https?://[^\s)]+")
START_AT_RE = re.compile(r"(?im)^\s*Start at:\s*(https?://\S+)\s*$")
POINT_TAG_RE = re.compile(
    r"<point>\s*([0-9]+(?:\.[0-9]+)?)\s+([0-9]+(?:\.[0-9]+)?)\s*</point>",
    re.IGNORECASE,
)
COORD_RE = re.compile(
    r"\(?\s*(-?[0-9]+(?:\.[0-9]+)?)\s*,\s*(-?[0-9]+(?:\.[0-9]+)?)\s*\)?"
)
BOX_RE = re.compile(
    r"\(?\s*(-?[0-9]+(?:\.[0-9]+)?)\s*,\s*(-?[0-9]+(?:\.[0-9]+)?)\s*,\s*(-?[0-9]+(?:\.[0-9]+)?)\s*,\s*(-?[0-9]+(?:\.[0-9]+)?)\s*\)?"
)


@dataclass
class ParsedAction:
    name: str
    args: dict[str, Any]
    raw: str


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
        os.environ.get("AGENTCLOAK_BROWSER_PROXY_URL")
        or os.environ.get("agentcloak_browser_proxy_url")
        or os.environ.get("HTTPS_PROXY")
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


def _to_data_url_png(image_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")


def _system_prompt() -> str:
    return textwrap.dedent(
        """\
        You are a browser control agent.
        Return exactly one action for the next step.

        Prefer function-style actions:
        - visit_url(url="https://...")
        - web_search(query="...")
        - click(start_box="(x,y)")
        - type(content="...", press_enter=true)
        - keypress(keys="Enter")
        - scroll(direction="down")
        - sleep(duration=2)
        - finished(content="...")

        Rules:
        - Return one action only.
        - If task complete, use finished(content="...").
        - Do not output long prose.
        """
    )


def _build_user_prompt(task: str, start_url: str, current_url: str, history: list[str]) -> str:
    hist = "\n".join(f"- {h}" for h in history[-10:]) if history else "- (none)"
    return textwrap.dedent(
        f"""\
        Task:
        {task}

        Start URL: {start_url}
        Current URL: {current_url}

        Recent history:
        {hist}
        """
    )


_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "visit_url",
            "description": "Navigate to a URL",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Run a web search query",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click a point",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_box": {"type": "string"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type",
            "description": "Type text",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "press_enter": {"type": "boolean"},
                    "start_box": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keypress",
            "description": "Press one or more keys",
            "parameters": {
                "type": "object",
                "properties": {"keys": {"type": "string"}},
                "required": ["keys"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll page",
            "parameters": {
                "type": "object",
                "properties": {"direction": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sleep",
            "description": "Wait",
            "parameters": {
                "type": "object",
                "properties": {"duration": {"type": "number"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finished",
            "description": "Finish task",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
            },
        },
    },
]


def _chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_text: str,
    image_bytes: bytes,
    max_tokens: int,
) -> dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _to_data_url_png(image_bytes)}},
                    {"type": "text", "text": user_text},
                ],
            },
        ],
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
        with urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {err[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to reach LLM endpoint {endpoint}: {exc}") from exc
    return body


def _decode_escapes(v: Any) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        return str(v)
    try:
        return bytes(v, "utf-8").decode("unicode_escape")
    except Exception:
        return v


def _scale_coord(v: float, max_dim: int) -> int:
    if abs(v) <= 1.0:
        px = int(v * max_dim)
    elif abs(v) <= 1000.0:
        px = int((v / 1000.0) * max_dim)
    else:
        px = int(v)
    if px < 0:
        return 0
    if px >= max_dim:
        return max_dim - 1
    return px


def _parse_point(raw: str, viewport_w: int, viewport_h: int) -> tuple[int, int]:
    t = raw.strip()
    t = t.replace("<|box_start|>", "").replace("<|box_end|>", "")
    t = t.replace("<|begin_of_box|>", "").replace("<|end_of_box|>", "")
    t = t.replace("<bbox>", "").replace("</bbox>", "")
    t = t.replace("[", "(").replace("]", ")")

    pm = POINT_TAG_RE.search(t)
    if pm:
        return _scale_coord(float(pm.group(1)), viewport_w), _scale_coord(float(pm.group(2)), viewport_h)

    bm = BOX_RE.search(t)
    if bm:
        cx = (float(bm.group(1)) + float(bm.group(3))) / 2.0
        cy = (float(bm.group(2)) + float(bm.group(4))) / 2.0
        return _scale_coord(cx, viewport_w), _scale_coord(cy, viewport_h)

    cm = COORD_RE.search(t)
    if cm:
        return _scale_coord(float(cm.group(1)), viewport_w), _scale_coord(float(cm.group(2)), viewport_h)

    try:
        parsed = ast.literal_eval(t)
        if isinstance(parsed, (tuple, list)):
            if len(parsed) == 2:
                return _scale_coord(float(parsed[0]), viewport_w), _scale_coord(float(parsed[1]), viewport_h)
            if len(parsed) == 4:
                cx = (float(parsed[0]) + float(parsed[2])) / 2.0
                cy = (float(parsed[1]) + float(parsed[3])) / 2.0
                return _scale_coord(cx, viewport_w), _scale_coord(cy, viewport_h)
    except Exception:
        pass

    raise ValueError(f"Unrecognized point format: {raw}")


def _extract_balanced_call(text: str, start_idx: int) -> str | None:
    depth = 0
    in_quote: str | None = None
    escape = False
    for idx in range(start_idx, len(text)):
        ch = text[idx]
        if in_quote is not None:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_quote:
                in_quote = None
            continue
        if ch in {"'", '"'}:
            in_quote = ch
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth -= 1
            if depth == 0:
                return text[start_idx : idx + 1]
    return None


def _parse_text_action(text: str) -> ParsedAction | None:
    merged = text.strip()
    if not merged:
        return None
    merged = merged.replace("<answer>", "").replace("</answer>", "")
    merged = merged.replace("<|begin_of_box|>", "").replace("<|end_of_box|>", "")
    merged = merged.replace("```json", "").replace("```", "")

    action_names = [
        "visit_url",
        "web_search",
        "click",
        "type",
        "keypress",
        "scroll",
        "sleep",
        "finished",
        "stop_action",
        "terminate",
        "wait",
    ]
    for name in action_names:
        m = re.search(rf"\b{re.escape(name)}\s*\(", merged)
        if not m:
            continue
        call_expr = _extract_balanced_call(merged, m.start())
        if not call_expr:
            continue
        try:
            parsed = ast.parse(call_expr, mode="eval")
        except SyntaxError:
            continue
        if not isinstance(parsed, ast.Expression) or not isinstance(parsed.body, ast.Call):
            continue
        call = parsed.body
        args: dict[str, Any] = {}
        for kw in call.keywords:
            if kw.arg is None:
                continue
            try:
                args[kw.arg] = ast.literal_eval(kw.value)
            except Exception:
                pass
        pos_vals: list[Any] = []
        for a in call.args:
            try:
                pos_vals.append(ast.literal_eval(a))
            except Exception:
                pass
        if name == "visit_url" and pos_vals and "url" not in args:
            args["url"] = pos_vals[0]
        elif name == "web_search" and pos_vals and "query" not in args:
            args["query"] = pos_vals[0]
        elif name == "sleep" and pos_vals and "duration" not in args:
            args["duration"] = pos_vals[0]
        elif name == "keypress" and pos_vals and "keys" not in args:
            args["keys"] = pos_vals[0]
        elif name == "finished" and pos_vals and "content" not in args:
            args["content"] = pos_vals[0]
        return ParsedAction(name=name, args=args, raw=call_expr)
    return None


def _parse_action(body: dict) -> tuple[str, ParsedAction]:
    """Parse an OpenAI-style chat completion response into (thought, ParsedAction)."""
    try:
        msg = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return ("", ParsedAction(name="sleep", args={"duration": 2}, raw="sleep(duration=2)"))

    reasoning = ""
    if isinstance(msg.get("reasoning_content"), str):
        reasoning = msg["reasoning_content"].strip()

    content = _decode_escapes(msg.get("content"))
    if not content:
        return ("", ParsedAction(name="sleep", args={"duration": 2}, raw="sleep(duration=2)"))

    try:
        # Use OFFICIAL SoM-GLM parser logic
        parsed_dict = parse_pc_response(content)
        raw_action = parsed_dict.get("action")
        # parsed_dict["action"] is e.g. "left_click(start_box='[802,40]')"
        # We parse this string back into a ParsedAction object using ast logic
        parsed = _parse_text_action(raw_action or "")
        thought = parsed_dict.get("action_text", reasoning)
        if not parsed:
            parsed = ParsedAction(name="sleep", args={"duration": 2}, raw="sleep()")
        return thought, parsed
    except Exception:
        # Fallback
        parsed = _parse_text_action(content)
        if parsed is None and reasoning:
            parsed = _parse_text_action(reasoning)
        if parsed is None:
            parsed = ParsedAction(name="sleep", args={"duration": 2}, raw="sleep(duration=2)")
        return (reasoning, parsed)


def _map_key(key: str) -> str:
    k = key.strip().lower()
    mapping = {
        "ctrl": "Control",
        "control": "Control",
        "alt": "Alt",
        "shift": "Shift",
        "cmd": "Meta",
        "command": "Meta",
        "enter": "Enter",
        "esc": "Escape",
        "tab": "Tab",
        "arrowleft": "ArrowLeft",
        "arrowright": "ArrowRight",
        "arrowup": "ArrowUp",
        "arrowdown": "ArrowDown",
    }
    return mapping.get(k, key.strip())


async def _execute_action(
    page: Page,
    action: ParsedAction,
    *,
    viewport_w: int,
    viewport_h: int,
) -> tuple[bool, str, str | None]:
    name = action.name.strip().lower()
    args = action.args or {}

    if name in {"finished", "stop_action", "terminate"}:
        final = _decode_escapes(args.get("content") or args.get("answer"))
        return True, "Finished", final or None

    if name == "visit_url":
        url = _decode_escapes(args.get("url"))
        if not url:
            return False, "visit_url missing url", None
        if "://" not in url:
            url = "https://" + url
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return False, f"Opened {url}", None

    if name == "web_search":
        query = _decode_escapes(args.get("query"))
        if not query:
            return False, "web_search missing query", None
        url = f"https://www.bing.com/search?q={quote_plus(query)}&FORM=QBLH"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return False, f"Searched '{query}'", None

    if name in {"click", "left_click", "right_click", "left_double_click"}:
        if "x" in args and "y" in args:
            x = _scale_coord(float(args["x"]), viewport_w)
            y = _scale_coord(float(args["y"]), viewport_h)
        else:
            raw = _decode_escapes(args.get("start_box") or args.get("point"))
            if not raw:
                return False, "click missing coordinates", None
            x, y = _parse_point(raw, viewport_w, viewport_h)
            
        button = "right" if name == "right_click" else "left"
        if name == "left_double_click":
            await page.mouse.dblclick(x, y, button=button)
        else:
            await page.mouse.click(x, y, button=button)
        return False, f"Clicked ({x},{y})", None

    if name == "type":
        raw = _decode_escapes(args.get("start_box") or args.get("point"))
        if raw:
            x, y = _parse_point(raw, viewport_w, viewport_h)
            await page.mouse.click(x, y, button="left")
        text = _decode_escapes(args.get("content") or args.get("text") or args.get("text_value"))
        if text:
            await page.keyboard.type(text)
        press_enter = bool(args.get("press_enter", False))
        if press_enter:
            await page.keyboard.press("Enter")
        return False, "Typed text", None

    if name in {"keypress", "press", "hotkey"}:
        keys_raw = _decode_escapes(args.get("keys") or args.get("key"))
        if not keys_raw:
            return False, "keypress missing keys", None
        tokens = [t for t in re.split(r"[+\s]+", keys_raw) if t]
        combo = "+".join(_map_key(k) for k in tokens)
        await page.keyboard.press(combo)
        return False, f"Pressed {combo}", None

    if name == "scroll":
        direction = _decode_escapes(args.get("direction") or "down").lower()
        if direction == "up":
            await page.mouse.wheel(0, -900)
        elif direction == "left":
            await page.mouse.wheel(-900, 0)
        elif direction == "right":
            await page.mouse.wheel(900, 0)
        else:
            await page.mouse.wheel(0, 900)
        return False, f"Scrolled {direction}", None

    if name in {"sleep", "wait", "WAIT", "DONE", "FAIL"}:
        if name in {"DONE", "FAIL"}:
            return True, f"Finished: {name}", name
        duration = args.get("duration", args.get("time", 2))
        try:
            sec = float(duration)
        except (TypeError, ValueError):
            sec = 2.0
        if sec > 30:
            sec = sec / 1000.0
        sec = max(0.1, min(sec, 30.0))
        await asyncio.sleep(sec)
        return False, f"Slept {sec:.1f}s", None

    await asyncio.sleep(1.0)
    return False, f"Unknown action '{name}', slept 1s", None


async def _run(args: argparse.Namespace) -> int:
    task_text = _read_task(args.task).strip()
    if not task_text:
        print("Empty task.", file=sys.stderr)
        return 2

    start_url = _extract_start_url(task_text)
    proxy = _proxy_settings_from_env()
    history: list[str] = []
    final_answer: str | None = None

    async with async_playwright() as pw:
        launch_kwargs: dict[str, Any] = {
            "headless": args.headless,
            "args": [
                "--disable-dev-shm-usage",
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
            await page.goto(start_url, wait_until="domcontentloaded", timeout=args.nav_timeout_ms)
            obs = f"Opened start URL {start_url}"
        except Exception as exc:
            obs = f"Failed to open start URL {start_url}: {exc}"
        history.append(obs)
        print(obs)

        for step in range(1, args.max_steps + 1):
            screenshot = await page.screenshot(type="png", full_page=False)
            current_url = page.url or "about:blank"
            
            # Using original SoM-GLM prompt logic
            history_str = [h.replace("->", " ") for h in history] # Avoid arrows in history matching
            memory_data = "[]" # In a full system we persist memory between steps, dummy here as in original runner demo
            user_prompt = get_pc_prompt(task_text, history_str[-10:], memory=memory_data)

            try:
                body = await asyncio.to_thread(
                    _chat_completion,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    model=args.model,
                    system_prompt="", # Instruct is all inside get_pc_prompt
                    user_text=user_prompt,
                    image_bytes=screenshot,
                    max_tokens=args.max_tokens,
                )
            except Exception as exc:
                print(f"Model call failed at step {step}: {exc}", file=sys.stderr)
                history.append(f"model_error -> {exc}")
                await asyncio.sleep(1.0)
                continue

            thought, action = _parse_action(body)
            print(f"Thought #{step}: {thought[:220] if thought else '(none)'}")
            print(f"Action #{step}: {action.raw}")

            try:
                done, observation, maybe_final = await _execute_action(
                    page,
                    action,
                    viewport_w=args.viewport_width,
                    viewport_h=args.viewport_height,
                )
            except Exception as exc:
                observation = f"action_error: {exc}"
                done, maybe_final = False, None
            print(f"Observation#{step}: {observation}")
            history.append(f"{action.raw} -> {observation}")

            if done:
                final_answer = maybe_final or thought or "done"
                break

        await context.close()
        await browser.close()

    print(f"Final Answer: {final_answer or 'Task ended without finished()'}")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Dedicated SoM-GLM runner for AgentCloak")
    p.add_argument("--task", required=True, help="Task text, '-' for stdin, or task file path")
    p.add_argument("--base-url", default=os.environ.get("SOM_GLM_BASE_URL", "http://127.0.0.1:8000/v1"))
    p.add_argument("--api-key", default=os.environ.get("SOM_GLM_API_KEY", os.environ.get("OPENAI_API_KEY", "EMPTY")))
    p.add_argument("--model", default=os.environ.get("SOM_GLM_MODEL", "zai-org/GLM-4.1V-9B-Thinking"))
    p.add_argument("--max-steps", type=int, default=int(os.environ.get("SOM_GLM_MAX_STEPS", "10")))
    p.add_argument("--max-tokens", type=int, default=int(os.environ.get("SOM_GLM_MAX_TOKENS", "512")))
    p.add_argument("--viewport-width", type=int, default=int(os.environ.get("SOM_GLM_VIEWPORT_WIDTH", "1440")))
    p.add_argument("--viewport-height", type=int, default=int(os.environ.get("SOM_GLM_VIEWPORT_HEIGHT", "900")))
    p.add_argument("--nav-timeout-ms", type=int, default=int(os.environ.get("SOM_GLM_NAV_TIMEOUT_MS", "30000")))
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

