#!/usr/bin/env python3
"""Dedicated SoM-GPT runner for AgentCloak measurements.

Why this runner:
- Uses direct browser control + OpenAI-compatible tool calls.
- Loads endpoint configs in the same shape as Fara WebEval oai_clients
  (CHAT_COMPLETION_PROVIDER / CHAT_COMPLETION_KWARGS_JSON).
- Supports endpoint file or directory (simple round-robin retry across endpoints).
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


@dataclass
class EndpointSpec:
    provider: str  # openai | azure
    model: str
    base_url: str
    api_key: str
    api_version: str = "2025-01-01-preview"
    azure_deployment: str = ""
    timeout_sec: int = 60

    @property
    def endpoint_for_log(self) -> str:
        if self.provider == "azure":
            return f"{self.base_url}/openai/deployments/{self.azure_deployment}"
        return self.base_url


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


def _build_endpoint_url(spec: EndpointSpec) -> str:
    if spec.provider == "azure":
        base = spec.base_url.rstrip("/")
        dep = spec.azure_deployment.strip()
        if not dep:
            raise RuntimeError("Azure endpoint requires azure_deployment")
        return f"{base}/openai/deployments/{dep}/chat/completions?api-version={spec.api_version}"
    return spec.base_url.rstrip("/") + "/chat/completions"


def _build_headers(spec: EndpointSpec) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if spec.provider == "azure":
        if spec.api_key:
            headers["api-key"] = spec.api_key
        elif os.environ.get("AZURE_OPENAI_API_KEY"):
            headers["api-key"] = os.environ["AZURE_OPENAI_API_KEY"]
        else:
            raise RuntimeError("Azure endpoint requires api_key (or AZURE_OPENAI_API_KEY)")
    else:
        if not spec.api_key:
            raise RuntimeError("OpenAI endpoint requires api_key")
        headers["Authorization"] = f"Bearer {spec.api_key}"
    return headers


def _chat_completion(
    *,
    spec: EndpointSpec,
    system_prompt: str,
    user_text: str,
    image_bytes: bytes,
    max_tokens: int,
    tool_choice: str,
    temperature: float | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    endpoint = _build_endpoint_url(spec)

    payload: dict[str, Any] = {
        "model": spec.model,
        "tool_choice": tool_choice,
        "tools": _TOOLS,
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
    if spec.provider == "openai":
        payload["max_completion_tokens"] = max_tokens
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
    else:
        payload["max_tokens"] = max_tokens
    if temperature is not None:
        payload["temperature"] = temperature

    req = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=_build_headers(spec),
        method="POST",
    )
    try:
        with urlopen(req, timeout=spec.timeout_sec) as resp:
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


def _parse_action(body: dict[str, Any]) -> tuple[str, ParsedAction]:
    choices = body.get("choices") or []
    if not isinstance(choices, list) or not choices:
        return ("", ParsedAction(name="sleep", args={"duration": 2}, raw="sleep(duration=2)"))
    msg = (choices[0] or {}).get("message") or {}
    if not isinstance(msg, dict):
        msg = {}

    reasoning = _decode_escapes(msg.get("reasoning"))

    tool_calls = msg.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        tc = tool_calls[0]
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            if isinstance(fn, dict):
                name = str(fn.get("name") or "").strip()
                raw_args = fn.get("arguments")
                args: dict[str, Any] = {}
                if isinstance(raw_args, str) and raw_args.strip():
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {}
                elif isinstance(raw_args, dict):
                    args = raw_args
                if name:
                    return (reasoning, ParsedAction(name=name, args=args, raw=f"{name}({args})"))

    content = _decode_escapes(msg.get("content"))
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

    if name == "click":
        if "x" in args and "y" in args:
            x = _scale_coord(float(args["x"]), viewport_w)
            y = _scale_coord(float(args["y"]), viewport_h)
        else:
            raw = _decode_escapes(args.get("start_box") or args.get("point"))
            if not raw:
                return False, "click missing coordinates", None
            x, y = _parse_point(raw, viewport_w, viewport_h)
        await page.mouse.click(x, y, button="left")
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

    if name in {"sleep", "wait"}:
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


def _parse_provider_config(config_obj: dict[str, Any], *, default_model: str, default_base_url: str, default_api_key: str, default_timeout_sec: int) -> EndpointSpec:
    # WebEval style: CHAT_COMPLETION_PROVIDER + CHAT_COMPLETION_KWARGS_JSON
    if "CHAT_COMPLETION_KWARGS_JSON" in config_obj:
        provider = str(config_obj.get("CHAT_COMPLETION_PROVIDER", "openai")).strip().lower()
        kwargs_json = config_obj.get("CHAT_COMPLETION_KWARGS_JSON") or {}
        if not isinstance(kwargs_json, dict):
            raise ValueError("CHAT_COMPLETION_KWARGS_JSON must be an object")

        if provider == "azure":
            azure_endpoint = str(kwargs_json.get("azure_endpoint") or default_base_url).strip()
            azure_deployment = str(kwargs_json.get("azure_deployment") or kwargs_json.get("model") or "").strip()
            api_version = str(kwargs_json.get("api_version") or "2025-01-01-preview").strip()
            api_key = str(
                kwargs_json.get("api_key")
                or os.environ.get("AZURE_OPENAI_API_KEY", "")
                or default_api_key
            ).strip()
            model = str(kwargs_json.get("model") or azure_deployment or default_model).strip()
            timeout_sec = int(kwargs_json.get("timeout") or kwargs_json.get("request_timeout") or default_timeout_sec)
            return EndpointSpec(
                provider="azure",
                model=model,
                base_url=azure_endpoint,
                api_key=api_key,
                api_version=api_version,
                azure_deployment=azure_deployment,
                timeout_sec=timeout_sec,
            )

        model = str(kwargs_json.get("model") or default_model).strip()
        base_url = str(kwargs_json.get("base_url") or default_base_url).strip()
        api_key = str(kwargs_json.get("api_key") or default_api_key).strip()
        timeout_sec = int(kwargs_json.get("timeout") or kwargs_json.get("request_timeout") or default_timeout_sec)
        return EndpointSpec(
            provider="openai",
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout_sec=timeout_sec,
        )

    # Simple style: model/base_url/api_key
    model = str(config_obj.get("model") or default_model).strip()
    base_url = str(config_obj.get("base_url") or default_base_url).strip()
    api_key = str(config_obj.get("api_key") or default_api_key).strip()
    timeout_sec = int(config_obj.get("timeout") or default_timeout_sec)
    return EndpointSpec(
        provider="openai",
        model=model,
        base_url=base_url,
        api_key=api_key,
        timeout_sec=timeout_sec,
    )


def _load_endpoint_specs(args: argparse.Namespace) -> list[EndpointSpec]:
    default_model = args.model
    default_base_url = args.base_url
    default_api_key = args.api_key

    cfg_path = args.endpoint_config
    if not cfg_path:
        return [
            EndpointSpec(
                provider="openai",
                model=default_model,
                base_url=default_base_url,
                api_key=default_api_key,
                timeout_sec=args.request_timeout_sec,
            )
        ]

    path = Path(cfg_path)
    if not path.exists():
        raise RuntimeError(f"endpoint config path does not exist: {cfg_path}")

    files: list[Path]
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.suffix.lower() == ".json")
        if not files:
            raise RuntimeError(f"no json endpoint config files found in: {cfg_path}")
    else:
        files = [path]

    specs: list[EndpointSpec] = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"failed to parse endpoint config: {f} ({exc})") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"invalid endpoint config object: {f}")
        spec = _parse_provider_config(
            data,
            default_model=default_model,
            default_base_url=default_base_url,
            default_api_key=default_api_key,
            default_timeout_sec=args.request_timeout_sec,
        )
        specs.append(spec)

    return specs


async def _run(args: argparse.Namespace) -> int:
    task_text = _read_task(args.task).strip()
    if not task_text:
        print("Empty task.", file=sys.stderr)
        return 2

    endpoint_specs = _load_endpoint_specs(args)
    if not endpoint_specs:
        print("No endpoint specs available.", file=sys.stderr)
        return 3

    # Validate credentials once up-front for openai mode.
    if all(spec.provider == "openai" for spec in endpoint_specs):
        if all(not spec.api_key or spec.api_key == "EMPTY" for spec in endpoint_specs):
            print("Missing OPENAI_API_KEY (or SOM_GPT_API_KEY).", file=sys.stderr)
            return 4

    start_url = _extract_start_url(task_text)
    proxy = _proxy_settings_from_env()
    history: list[str] = []
    final_answer: str | None = None

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
            await page.goto(start_url, wait_until="domcontentloaded", timeout=args.nav_timeout_ms)
            obs = f"Opened start URL {start_url}"
        except Exception as exc:
            obs = f"Failed to open start URL {start_url}: {exc}"
        history.append(obs)
        print(obs)

        for step in range(1, args.max_steps + 1):
            screenshot = await page.screenshot(type="png", full_page=False)
            current_url = page.url or "about:blank"
            user_prompt = _build_user_prompt(task_text, start_url, current_url, history)

            body: dict[str, Any] | None = None
            last_exc: Exception | None = None
            for attempt in range(args.max_model_retries):
                spec = endpoint_specs[(step + attempt - 1) % len(endpoint_specs)]
                try:
                    body = await asyncio.to_thread(
                        _chat_completion,
                        spec=spec,
                        system_prompt=_system_prompt(),
                        user_text=user_prompt,
                        image_bytes=screenshot,
                        max_tokens=args.max_tokens,
                        tool_choice=args.tool_choice,
                        temperature=args.temperature,
                        reasoning_effort=args.reasoning_effort,
                    )
                    if args.verbose_model_calls:
                        print(
                            f"[model] step={step} attempt={attempt+1} "
                            f"provider={spec.provider} model={spec.model} endpoint={spec.endpoint_for_log}"
                        )
                    break
                except Exception as exc:
                    last_exc = exc
                    print(
                        f"Model call failed at step {step} attempt {attempt+1}/{args.max_model_retries}: {exc}",
                        file=sys.stderr,
                    )
                    await asyncio.sleep(0.8)

            if body is None:
                history.append(f"model_error -> {last_exc}")
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
    p = argparse.ArgumentParser(description="Dedicated SoM-GPT runner for AgentCloak")
    p.add_argument("--task", required=True, help="Task text, '-' for stdin, or task file path")
    p.add_argument(
        "--endpoint-config",
        default=os.environ.get("SOM_GPT_ENDPOINT_CONFIG", ""),
        help=(
            "Path to endpoint JSON file or directory. Supports WebEval-style config "
            "(CHAT_COMPLETION_PROVIDER + CHAT_COMPLETION_KWARGS_JSON)."
        ),
    )
    p.add_argument("--base-url", default=os.environ.get("SOM_GPT_BASE_URL", "https://api.openai.com/v1"))
    p.add_argument("--api-key", default=os.environ.get("SOM_GPT_API_KEY", os.environ.get("OPENAI_API_KEY", "")))
    p.add_argument("--model", default=os.environ.get("SOM_GPT_MODEL", "gpt-5"))
    p.add_argument("--tool-choice", default=os.environ.get("SOM_GPT_TOOL_CHOICE", "required"))
    p.add_argument("--max-steps", type=int, default=int(os.environ.get("SOM_GPT_MAX_STEPS", "10")))
    p.add_argument("--max-tokens", type=int, default=int(os.environ.get("SOM_GPT_MAX_TOKENS", "512")))
    p.add_argument(
        "--reasoning-effort",
        default=os.environ.get("SOM_GPT_REASONING_EFFORT", "minimal"),
        help="OpenAI reasoning_effort (minimal/low/medium/high). Empty to omit.",
    )
    p.add_argument(
        "--max-model-retries",
        type=int,
        default=int(os.environ.get("SOM_GPT_MAX_MODEL_RETRIES", "3")),
    )
    p.add_argument(
        "--request-timeout-sec",
        type=int,
        default=int(os.environ.get("SOM_GPT_REQUEST_TIMEOUT_SEC", "60")),
    )
    temp = os.environ.get("SOM_GPT_TEMPERATURE", "")
    p.add_argument(
        "--temperature",
        type=float,
        default=(float(temp) if temp else None),
        help="Optional temperature. Omit to use model default.",
    )
    p.add_argument("--viewport-width", type=int, default=int(os.environ.get("SOM_GPT_VIEWPORT_WIDTH", "1440")))
    p.add_argument("--viewport-height", type=int, default=int(os.environ.get("SOM_GPT_VIEWPORT_HEIGHT", "900")))
    p.add_argument("--nav-timeout-ms", type=int, default=int(os.environ.get("SOM_GPT_NAV_TIMEOUT_MS", "30000")))
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--verbose-model-calls", action="store_true", default=False)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
