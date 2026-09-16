#!/usr/bin/env python3
"""Dedicated UI-TARS browser runner for AgentCloak measurements.

Key points:
- Uses Playwright directly (no magentic orchestration).
- Uses a robust UI-TARS-style action parser (point/box aliases, escaped text).
- Executes one action per model turn.
"""

from __future__ import annotations

import ast
import argparse
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
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# Pre-load official UI-TARS repository paths
import sys
sys.path.insert(0, os.environ.get("UI_TARS_AGENT_PATH",
                                "${PROJECT_ROOT}/models/UI-TARS/codes"))
try:
    from ui_tars.prompt import COMPUTER_USE_DOUBAO
    from ui_tars.action_parser import parse_action_to_structure_output
except ImportError as e:
    print(f"Warning: Could not import ui_tars modules: {e}")
    COMPUTER_USE_DOUBAO = "{instruction}"

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
    args: dict[str, str | float | int | bool | None]
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


def _to_data_url_png(image_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")


def _chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_text: str,
    image_bytes: bytes,
    max_tokens: int,
) -> str:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": _to_data_url_png(image_bytes)}},
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

    try:
        return str(body["choices"][0]["message"]["content"])
    except Exception as exc:
        raise RuntimeError(f"Unexpected LLM response schema: {json.dumps(body)[:1000]}") from exc


def _strip_fences(text: str) -> str:
    out = text.strip()
    if out.startswith("```"):
        parts = out.splitlines()
        if parts and parts[0].startswith("```"):
            parts = parts[1:]
        if parts and parts[-1].startswith("```"):
            parts = parts[:-1]
        out = "\n".join(parts).strip()
    return out


def _extract_thought(text: str) -> str:
    t = text.strip()
    m = re.search(r"Thought:\s*(.+?)(?=\s*Action:|$)", t, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"Action_Summary:\s*(.+?)(?=\s*Action:|$)", t, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(
        r"Reflection:\s*(.+?)\s*Action_Summary:\s*(.+?)(?=\s*Action:|$)",
        t,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        return f"Reflection: {m.group(1).strip()} | Summary: {m.group(2).strip()}"
    return ""


async def _execute_action(
    page: "Page",
    action: ParsedAction,
    *,
    viewport_w: int = 1280,
    viewport_h: int = 720,
) -> tuple[bool, str, str | None]:
    """Execute a parsed UI-TARS action on the Playwright page.

    Returns (done, observation, final_answer).
    """
    name = action.name.lower().strip()
    args = action.args or {}

    try:
        if name in ("finished", "done", "finish", "complete"):
            answer = args.get("content", "") or args.get("text", "") or action.raw
            return True, "Task marked as finished.", str(answer)

        if name == "wait":
            import asyncio as _aio
            await _aio.sleep(1.0)
            return False, "Waited 1 second.", None

        # --- Coordinate-based actions ---
        # Extract coordinates (normalized 0-1000 or pixel)
        def _coord(key_x: str = "x", key_y: str = "y") -> tuple[int, int]:
            x = float(args.get(key_x, args.get("coordinate", [0, 0])[0] if isinstance(args.get("coordinate"), list) else 0))
            y = float(args.get(key_y, args.get("coordinate", [0, 0])[1] if isinstance(args.get("coordinate"), list) else 0))
            # If coordinates are in 0-1000 range (UI-TARS default), scale to viewport
            if 0 < x <= 1000 and 0 < y <= 1000:
                x = x * viewport_w / 1000
                y = y * viewport_h / 1000
            return int(x), int(y)

        if name in ("click", "left_single"):
            x, y = _coord()
            await page.mouse.click(x, y)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            return False, f"Clicked at ({x}, {y}).", None

        if name == "left_double":
            x, y = _coord()
            await page.mouse.dblclick(x, y)
            return False, f"Double-clicked at ({x}, {y}).", None

        if name == "right_single":
            x, y = _coord()
            await page.mouse.click(x, y, button="right")
            return False, f"Right-clicked at ({x}, {y}).", None

        if name == "hover":
            x, y = _coord()
            await page.mouse.move(x, y)
            return False, f"Hovered at ({x}, {y}).", None

        if name == "type":
            content = str(args.get("content", args.get("text", "")))
            await page.keyboard.type(content, delay=20)
            return False, f"Typed '{content[:50]}'.", None

        if name == "press":
            key = str(args.get("key", args.get("hotkey", "Enter")))
            await page.keyboard.press(key)
            return False, f"Pressed '{key}'.", None

        if name == "hotkey":
            keys_str = str(args.get("key", args.get("hotkey", "")))
            keys = [k.strip() for k in keys_str.replace("+", " ").split() if k.strip()]
            if keys:
                # Hold modifiers, press last key
                for k in keys[:-1]:
                    await page.keyboard.down(k)
                await page.keyboard.press(keys[-1])
                for k in reversed(keys[:-1]):
                    await page.keyboard.up(k)
            return False, f"Hotkey '{keys_str}'.", None

        if name == "scroll":
            x = int(float(args.get("x", viewport_w // 2)))
            y = int(float(args.get("y", viewport_h // 2)))
            direction = str(args.get("direction", "down")).lower()
            amount = int(float(args.get("amount", 3)))
            delta = amount * 120
            if direction == "up":
                delta = -delta
            elif direction == "left":
                await page.mouse.wheel(delta_x=-delta, delta_y=0)
                return False, f"Scrolled left at ({x}, {y}).", None
            elif direction == "right":
                await page.mouse.wheel(delta_x=delta, delta_y=0)
                return False, f"Scrolled right at ({x}, {y}).", None
            await page.mouse.wheel(delta_x=0, delta_y=delta)
            return False, f"Scrolled {direction} at ({x}, {y}).", None

        if name in ("drag", "select"):
            sx = float(args.get("startX", args.get("x", 0)))
            sy = float(args.get("startY", args.get("y", 0)))
            ex = float(args.get("endX", args.get("x2", 0)))
            ey = float(args.get("endY", args.get("y2", 0)))
            if 0 < sx <= 1000:
                sx = sx * viewport_w / 1000
                sy = sy * viewport_h / 1000
                ex = ex * viewport_w / 1000
                ey = ey * viewport_h / 1000
            await page.mouse.move(int(sx), int(sy))
            await page.mouse.down()
            await page.mouse.move(int(ex), int(ey), steps=10)
            await page.mouse.up()
            return False, f"Dragged from ({int(sx)},{int(sy)}) to ({int(ex)},{int(ey)}).", None

        # Unknown action → treat as no-op
        return False, f"Unknown action '{name}', skipped.", None

    except Exception as exc:
        return False, f"Action '{name}' failed: {exc}", None


def _build_user_prompt(task: str, start_url: str, current_url: str, history: list[str]) -> str:
    hist = "\\n".join(f"- {h}" for h in history[-8:]) if history else "- (none)"
    instruction = textwrap.dedent(
        f"""\
        Task:
        {task}

        Start URL: {start_url}
        Current URL: {current_url}

        Recent action/observation history:
        {hist}
        """
    )
    # The official prompt template requires the {instruction} parameter
    try:
        return COMPUTER_USE_DOUBAO.format(language="English", instruction=instruction)
    except NameError:
        return instruction




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

        for i in range(1, args.max_steps + 1):
            screenshot = await page.screenshot(type="png", full_page=False)
            current_url = page.url or "about:blank"
            user_prompt = _build_user_prompt(task_text, start_url, current_url, history)

            try:
                model_text = await asyncio.to_thread(
                    _chat_completion,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    model=args.model,
                    system_prompt="", # Instruct passed inside user_text entirely for OFFICIAL ui_tars prompt
                    user_text=user_prompt,
                    image_bytes=screenshot,
                    max_tokens=args.max_tokens,
                )
            except Exception as exc:
                history.append(f"Model call failed: {exc}")
                print(f"Model call failed at step {i}: {exc}", file=sys.stderr)
                await asyncio.sleep(1.0)
                continue

            try:
                # Use official UI-TARS action parser
                struct_actions = parse_action_to_structure_output(
                    model_text,
                    factor=1,
                    origin_resized_height=args.viewport_height,
                    origin_resized_width=args.viewport_width,
                    model_type="qwen2vl" # UI-TARS emits normalized relative box scaled by 1000 or absolute. The Qwen2VL branch handles it best or relative.
                )
                if not struct_actions:
                    raise ValueError("No actions obtained from parser")
                first = struct_actions[0]
                action_name = first.get("action_type", "wait")
                action_inputs = first.get("action_inputs", {})
                thought = first.get("thought", "")
                raw = first.get("text", "")
            except Exception as e:
                # Fallback to wait if parsing fully fails
                action_name = "wait"
                action_inputs = {}
                thought = f"(Parser error: {e})"
                raw = model_text[:100]

            parsed = ParsedAction(name=action_name, args=action_inputs, raw=raw)
            print(f"Thought #{i}: {thought}")
            print(f"Action #{i}: {parsed.raw}")

            done, observation, maybe_final = await _execute_action(
                page,
                parsed,
                viewport_w=args.viewport_width,
                viewport_h=args.viewport_height,
            )
            print(f"Observation#{i}: {observation}")
            history.append(f"{parsed.raw} -> {observation}")

            if done:
                final_answer = maybe_final or thought or "done"
                break

        await context.close()
        await browser.close()

    print(f"Final Answer: {final_answer or 'Task ended without finished()'}")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Dedicated UI-TARS runner for AgentCloak")
    p.add_argument("--task", required=True, help="Task text, '-' for stdin, or task file path")
    p.add_argument("--base-url", default=os.environ.get("UI_TARS_BASE_URL", "http://127.0.0.1:8001/v1"))
    p.add_argument("--api-key", default=os.environ.get("UI_TARS_API_KEY", os.environ.get("OPENAI_API_KEY", "EMPTY")))
    p.add_argument("--model", default=os.environ.get("UI_TARS_MODEL", "ByteDance-Seed/UI-TARS-1.5-7B"))
    p.add_argument("--max-steps", type=int, default=int(os.environ.get("UI_TARS_MAX_STEPS", "8")))
    p.add_argument("--max-tokens", type=int, default=int(os.environ.get("UI_TARS_MAX_TOKENS", "512")))
    p.add_argument("--viewport-width", type=int, default=int(os.environ.get("UI_TARS_VIEWPORT_WIDTH", "1440")))
    p.add_argument("--viewport-height", type=int, default=int(os.environ.get("UI_TARS_VIEWPORT_HEIGHT", "900")))
    p.add_argument("--nav-timeout-ms", type=int, default=int(os.environ.get("UI_TARS_NAV_TIMEOUT_MS", "30000")))
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

