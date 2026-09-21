#!/usr/bin/env python3
"""Browser-Use agent runner for the measurement harness.

Runs browser-use's Python API (not CLI) with proxy support for mitmproxy
capture. Accepts --task and --start-url arguments, outputs JSON result.

Usage:
    python browser_use_runner.py --task "Find a recipe" --start-url "https://allrecipes.com/"

Environment variables:
    OPENAI_API_KEY          - Required for GPT-4o backend
    HTTP_PROXY/HTTPS_PROXY  - Proxy URL (set by harness)
    BROWSER_USE_LLM         - LLM model name (default: gpt-4o)
    BROWSER_USE_MAX_STEPS   - Max agent steps (default: 15)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time


def parse_args():
    parser = argparse.ArgumentParser(description="Browser-Use agent runner")
    parser.add_argument("--task", type=str, required=True)
    parser.add_argument("--start-url", type=str, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=200.0)
    return parser.parse_args()


async def run():
    args = parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print(json.dumps({"success": False, "error": "OPENAI_API_KEY not set"}))
        return 1

    # Detect proxy from environment (set by harness via BROWSER_USE_PROXY_URL)
    proxy_url = os.environ.get("BROWSER_USE_PROXY_URL", "")

    # CRITICAL: Remove ALL proxy env vars BEFORE importing browser-use.
    # browser-use's local Chrome launcher uses aiohttp which reads HTTP_PROXY
    # for its CDP loopback connection, causing it to route 127.0.0.1 through
    # the proxy and fail. We pass proxy to Chrome via --proxy-server instead.
    for key in list(os.environ.keys()):
        if key.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            del os.environ[key]

    # Increase browser-use event timeouts for proxy environments
    os.environ.setdefault("TIMEOUT_BrowserStartEvent", "90")
    os.environ.setdefault("TIMEOUT_BrowserLaunchEvent", "90")

    llm_model = os.environ.get("BROWSER_USE_LLM", "gpt-4o")
    max_steps = int(os.environ.get("BROWSER_USE_MAX_STEPS", "15"))

    start_time = time.monotonic()
    deadline = start_time + args.timeout_seconds

    try:
        from browser_use import Agent, BrowserSession, BrowserProfile
        from browser_use.browser.profile import ProxySettings
        from browser_use.llm.openai.chat import ChatOpenAI

        # Configure LLM
        base_url = os.environ.get("BROWSER_USE_BASE_URL", "https://api.openai.com/v1")
        llm = ChatOpenAI(
            model=llm_model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.6,
            top_p=0.95,
        )

        # Use system Chromium with CDP support (Playwright's Chromium
        # binary does not support --remote-debugging-port).
        chrome_path = "/snap/bin/chromium"
        if not os.path.exists(chrome_path):
            chrome_path = None  # fallback to browser-use auto-detection

        # Configure browser for agent benchmark:
        # Note: We are using the original Browser-Use default extensions (uBlock, etc.)
        # because disabling them severely degrades baseline success rates due to cookie popups.
        browser_profile = BrowserProfile(
            headless=True,
            disable_security=True,  # --ignore-certificate-errors etc.
            executable_path=chrome_path,
            is_local=True,
            # Leaving extensions_enabled defaults (True) to match original implementation
            extra_chromium_args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        if proxy_url:
            no_proxy = os.environ.get("BROWSER_USE_NO_PROXY", os.environ.get("NO_PROXY", ""))
            browser_profile.proxy = ProxySettings(
                server=proxy_url,
                bypass=no_proxy if no_proxy else None,
            )
            # Proxy env vars already removed at script startup (see above).

        browser_session = BrowserSession(
            browser_profile=browser_profile,
            is_local=True,
            executable_path="/snap/bin/chromium",
            timeout=90,
        )

        # Create agent
        agent = Agent(
            task=args.task,
            llm=llm,
            browser_session=browser_session,
            # using default max_actions_per_step=5
        )

        # Run with timeout
        remaining = max(5.0, deadline - time.monotonic())
        result = await asyncio.wait_for(
            agent.run(max_steps=max_steps),
            timeout=remaining,
        )

        duration = time.monotonic() - start_time

        # Extract result
        is_done = result.is_done() if hasattr(result, 'is_done') else bool(result)
        final_result = result.final_result() if hasattr(result, 'final_result') else str(result)
        steps = result.n_steps() if hasattr(result, 'n_steps') else 0

        output = {
            "success": bool(is_done),
            "task_completed": bool(is_done),
            "final_result": str(final_result)[:500] if final_result else None,
            "steps_executed": steps,
            "model": llm_model,
            "duration_seconds": round(duration, 2),
            "proxy_used": bool(proxy_url),
        }

        print(json.dumps(output))
        return 0

    except asyncio.TimeoutError:
        duration = time.monotonic() - start_time
        print(json.dumps({
            "success": False,
            "error": "timeout",
            "duration_seconds": round(duration, 2),
        }))
        return 1

    except Exception as exc:
        duration = time.monotonic() - start_time
        print(json.dumps({
            "success": False,
            "error": str(exc)[:500],
            "duration_seconds": round(duration, 2),
        }))
        return 1

    finally:
        # Ensure browser cleanup
        try:
            if 'browser_session' in dir():
                await browser_session.stop()
        except Exception:
            pass


def main():
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
