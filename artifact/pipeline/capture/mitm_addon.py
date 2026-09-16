"""Mitmproxy addon for capturing HTTP traffic to a JSON Lines file.

Loaded by mitmdump via: mitmdump -s mitm_capture_addon.py
Writes each completed HTTP flow as a JSON line to the file specified
by the AGENTCLOAK_CAPTURE_FILE environment variable.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

import mitmproxy.http
from pathlib import Path


CAPTURE_FILE = os.environ.get("AGENTCLOAK_CAPTURE_FILE", "/tmp/agentcloak_capture.jsonl")
# Hashing controls: reduce overhead by limiting which responses are hashed.
# - AGENTCLOAK_HASH_JS_ONLY=1 => only hash JS responses
# - AGENTCLOAK_HASH_MAX_BYTES=N => only hash if body size <= N bytes (0 = no limit)
HASH_JS_ONLY = os.environ.get("AGENTCLOAK_HASH_JS_ONLY", "0").strip() == "1"
HASH_MAX_BYTES = int(os.environ.get("AGENTCLOAK_HASH_MAX_BYTES", "0") or 0)

# E6 transition budget enforcement: set E6_TRANSITION_BUDGET to enable (e.g., "4")
E6_BUDGET = int(os.environ.get("E6_TRANSITION_BUDGET", "0") or 0)  # 0 = disabled
# E6+: also block third-party sub-resources after budget exhaustion
E6_BLOCK_SUBRESOURCES = os.environ.get("E6_BLOCK_SUBRESOURCES", "0").strip() == "1"

# RQ3 action-space ablation enforcement (paired with tool-schema removal):
#   RQ3_BLOCK_SEARCH=1   -> drop any request to a known search-engine host
#   RQ3_BLOCK_OFFDOMAIN=1 -> drop any navigation to an off-domain eTLD+1
# Both record blocked requests in capture.jsonl with rq3_blocked=True so the
# analysis pipeline can attribute condition-side blocks.
RQ3_BLOCK_SEARCH = os.environ.get("RQ3_BLOCK_SEARCH", "0").strip() == "1"
RQ3_BLOCK_OFFDOMAIN = os.environ.get("RQ3_BLOCK_OFFDOMAIN", "0").strip() == "1"
# RQ3_BLOCK_CMP=1 -> drop requests to known consent-management-platform (CMP)
# CDNs so the consent banner never renders; the site falls back to its
# default (typically pre-consent) state and the agent has no chance to
# Accept/Reject. Targets the same finding documented in E1 (78.3% Accept).
RQ3_BLOCK_CMP = os.environ.get("RQ3_BLOCK_CMP", "0").strip() == "1"

# Search-engine host patterns to drop under RQ3_BLOCK_SEARCH.
# Matched by exact hostname or registrable-domain match.
_SEARCH_ENGINE_HOSTS = frozenset({
    "www.google.com",        # only /search and /url paths in practice; we drop any www.google.com under BLOCK_SEARCH only if the path is /search
    "google.com",
    "www.bing.com",
    "bing.com",
    "duckduckgo.com",
    "www.duckduckgo.com",
    "search.yahoo.com",
    "www.yahoo.com",
    "www.baidu.com",
    "baidu.com",
    "yandex.com",
    "www.yandex.com",
})

# Conservative path-based search-page detector (used when the host is a known
# multi-purpose host such as google.com whose root is also a normal landing page).
_SEARCH_PATH_PATTERNS = ("/search", "/s?", "/s/", "/results")


# CMP / consent-banner CDN hosts (registrable-domain match).
# These CDNs ship the JS that draws and orchestrates the consent banner.
# Blocking them prevents the banner from rendering, so the agent never gets a
# chance to click Accept/Reject; the site renders in its default state.
_CMP_REGISTRABLE_DOMAINS = frozenset({
    "cookielaw.org",      # OneTrust
    "cookiebot.com",      # Cookiebot
    "trustarc.com",       # TrustArc
    "didomi.io",          # Didomi
    "usercentrics.eu",    # Usercentrics
    "usercentrics.com",
    "consensu.org",       # IAB TCF vendor list CDN
    "sp-prod.net",        # Sourcepoint
    "sourcepoint.com",    # Sourcepoint
    "sourcepointcmp.com",
    "iubenda.com",        # iubenda
    "civiccomputing.com", # CookieControl
    "trustcommander.net", # Commanders Act
})


def _is_cmp_request(hostname: str) -> bool:
    """True if `hostname` belongs to a known CMP CDN."""
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    reg = _extract_registrable_domain(host)
    return reg in _CMP_REGISTRABLE_DOMAINS


def _is_search_request(hostname: str, path: str) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if host in _SEARCH_ENGINE_HOSTS:
        # google.com root is not a search page; require path-based match for hosts
        # that double as landing pages.
        if host in ("google.com", "www.google.com", "www.bing.com", "bing.com", "yahoo.com", "search.yahoo.com"):
            return any(path.startswith(p) for p in _SEARCH_PATH_PATTERNS)
        # duckduckgo.com / yandex.com / baidu.com root *is* a search page.
        return True
    return False


def _extract_registrable_domain(hostname: str) -> str:
    """Best-effort eTLD+1 extraction."""
    host = (hostname or "").strip().lower().rstrip(".")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    cc_prefixes = {"co", "com", "org", "net", "ac", "edu", "gov", "ne", "or"}
    if parts[-2] in cc_prefixes and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


class CaptureAddon:
    """Mitmproxy addon that records HTTP flows to a JSONL file.

    When E6_TRANSITION_BUDGET > 0, additionally enforces a cross-site
    transition budget by blocking navigation requests that exceed the limit.
    """

    def __init__(self) -> None:
        self._file = open(CAPTURE_FILE, "a", encoding="utf-8")
        # E6 state (only active when E6_BUDGET > 0)
        self._e6_active = E6_BUDGET > 0
        self._e6_budget = E6_BUDGET
        self._e6_target_domain = ""
        self._e6_nav_hosts: list[str] = []
        self._e6_cross_site_count = 0
        self._e6_blocked_count = 0
        # RQ3 ablation state
        self._rq3_target_domain = ""  # captured on first non-search navigation
        self._rq3_blocked_search = 0
        self._rq3_blocked_offdomain = 0
        self._rq3_blocked_cmp = 0

    def _e6_is_first_party(self, hostname: str) -> bool:
        host = (hostname or "").strip().lower().rstrip(".")
        target = self._e6_target_domain
        if not host or not target:
            return False
        return host == target or host.endswith(f".{target}")

    def _e6_is_navigation(self, flow: mitmproxy.http.HTTPFlow) -> bool:
        return self._is_navigation_like(flow)

    def _is_navigation_like(self, flow: mitmproxy.http.HTTPFlow) -> bool:
        if flow.request.method.upper() != "GET":
            return False
        accept = flow.request.headers.get("accept", "")
        sec_dest = flow.request.headers.get("sec-fetch-dest", "")
        if "text/html" in accept:
            return True
        if sec_dest in ("document", "iframe", "frame"):
            return True
        return False

    def request(self, flow: mitmproxy.http.HTTPFlow) -> None:
        """Hook request to intercept telemetry and block navigations if E6 is active."""
        
        # Intercept telemetry beacons from the injected JS hook
        if flow.request.path == "/__agentcloak_telemetry":
            try:
                payload = flow.request.content.decode("utf-8", errors="replace")
                fp_file_path = os.environ.get("AGENTCLOAK_FP_CAPTURE_FILE", "js_telemetry.jsonl")
                with open(fp_file_path, "a", encoding="utf-8") as f:
                    f.write(payload + "\n")
            except Exception:
                pass
            flow.response = mitmproxy.http.Response.make(200, b"OK", {"Access-Control-Allow-Origin": "*"})
            return

        # RQ3 action-space ablation enforcement (proxy layer).
        # Runs before E6 budget so a search/off-domain block is attributed to RQ3.
        if RQ3_BLOCK_SEARCH or RQ3_BLOCK_OFFDOMAIN or RQ3_BLOCK_CMP:
            host = (flow.request.host or "").strip().lower().rstrip(".")
            path = flow.request.path or ""

            if RQ3_BLOCK_CMP and _is_cmp_request(host):
                self._rq3_blocked_cmp += 1
                self._write_record_from_request(flow, blocked=True, rq3_reason="cmp")
                flow.response = mitmproxy.http.Response.make(
                    451,
                    b"RQ3: CMP host blocked",
                    {"Content-Type": "text/plain", "X-RQ3-Block": "cmp"},
                )
                return

            if RQ3_BLOCK_SEARCH and _is_search_request(host, path):
                self._rq3_blocked_search += 1
                self._write_record_from_request(flow, blocked=True, rq3_reason="search")
                flow.response = mitmproxy.http.Response.make(
                    451,
                    b"RQ3: search-engine host blocked",
                    {"Content-Type": "text/plain", "X-RQ3-Block": "search"},
                )
                return

            if RQ3_BLOCK_OFFDOMAIN and self._is_navigation_like(flow):
                # Lazily latch the target domain on the first non-search navigation.
                if not self._rq3_target_domain and host and not _is_search_request(host, path):
                    self._rq3_target_domain = _extract_registrable_domain(host)
                if self._rq3_target_domain:
                    request_etld = _extract_registrable_domain(host)
                    if request_etld and request_etld != self._rq3_target_domain:
                        self._rq3_blocked_offdomain += 1
                        self._write_record_from_request(flow, blocked=True, rq3_reason="offdomain")
                        flow.response = mitmproxy.http.Response.make(
                            451,
                            b"RQ3: off-domain navigation blocked",
                            {"Content-Type": "text/plain", "X-RQ3-Block": "offdomain"},
                        )
                        return

        if not self._e6_active:
            return

        host = (flow.request.host or "").strip().lower().rstrip(".")

        # E6+: block third-party sub-resources after budget exhaustion
        if E6_BLOCK_SUBRESOURCES and self._e6_cross_site_count >= self._e6_budget:
            if not self._e6_is_first_party(host) and not self._e6_is_navigation(flow):
                self._write_record_from_request(flow, blocked=True)
                flow.response = mitmproxy.http.Response.make(
                    403, b"E6+: 3P sub-resource blocked", {"Content-Type": "text/plain"})
                return

        if not self._e6_is_navigation(flow):
            return

        # Auto-detect target domain from first navigation
        if not self._e6_target_domain and host:
            self._e6_target_domain = _extract_registrable_domain(host)

        # Check if this would cause a cross-site transition
        if self._e6_nav_hosts and self._e6_nav_hosts[-1] != host:
            prev_fp = self._e6_is_first_party(self._e6_nav_hosts[-1])
            curr_fp = self._e6_is_first_party(host)
            if prev_fp != curr_fp:
                # Would exceed budget?
                if self._e6_cross_site_count >= self._e6_budget:
                    self._e6_blocked_count += 1
                    # Write blocked record BEFORE killing the flow
                    self._write_record_from_request(flow, blocked=True)
                    flow.response = mitmproxy.http.Response.make(
                        403,
                        f"E6: budget exhausted (k={self._e6_budget})".encode(),
                        {"Content-Type": "text/plain"},
                    )
                    return
                self._e6_cross_site_count += 1

        # Record navigation host (deduplicated consecutive)
        if not self._e6_nav_hosts or self._e6_nav_hosts[-1] != host:
            self._e6_nav_hosts.append(host)

    def _write_record_from_request(
        self,
        flow: mitmproxy.http.HTTPFlow,
        blocked: bool,
        rq3_reason: str | None = None,
    ) -> None:
        """Write a minimal record for a blocked request (no response yet)."""
        record = {
            "url": flow.request.pretty_url,
            "method": flow.request.method,
            "headers": dict(flow.request.headers),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "hostname": (flow.request.host or "").strip().lower().rstrip("."),
            "response_status": 451 if rq3_reason else 403,
            "e6_blocked": rq3_reason is None,  # only mark E6 if not RQ3
            "e6_cross_site_transitions": self._e6_cross_site_count,
            "e6_budget": self._e6_budget,
        }
        if rq3_reason is not None:
            record["rq3_blocked"] = True
            record["rq3_reason"] = rq3_reason
            record["rq3_blocked_search_total"] = self._rq3_blocked_search
            record["rq3_blocked_offdomain_total"] = self._rq3_blocked_offdomain
            record["rq3_blocked_cmp_total"] = self._rq3_blocked_cmp
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    def response(self, flow: mitmproxy.http.HTTPFlow) -> None:
        """Called when a server response has been received. Injects JS hook."""
        if flow.response is None:
            return
        response_headers = dict(flow.response.headers)
        set_cookies = []
        try:
            set_cookies = flow.response.headers.get_all("set-cookie")
        except Exception:
            # Fallback for older mitmproxy versions
            sc = response_headers.get("set-cookie") or response_headers.get("Set-Cookie")
            if sc:
                set_cookies = [sc]

        content_type = response_headers.get("content-type") or response_headers.get("Content-Type") or ""
        
        # Inject JS Hook into HTML
        if flow.response.status_code == 200 and "text/html" in content_type and flow.response.content:
            try:
                html = flow.response.get_text(strict=False)
                if html and "<head" in html.lower():
                    # Strip Content Security Policy so our hook executes
                    if "content-security-policy" in flow.response.headers:
                        del flow.response.headers["content-security-policy"]
                    if "Content-Security-Policy" in flow.response.headers:
                        del flow.response.headers["Content-Security-Policy"]
                        
                    hook_path = Path(__file__).resolve().parent / "instrumentation" / "hook.js"
                    if hook_path.exists():
                        hook_js = hook_path.read_text(encoding="utf-8")
                        # Add defer tag to circumvent inline script restrictions if any remain
                        injected = f"<script>{hook_js}</script>"
                        
                        idx = html.lower().find("<head")
                        end_idx = html.find(">", idx) + 1
                        if idx != -1 and end_idx != 0:
                            new_html = html[:end_idx] + injected + html[end_idx:]
                            flow.response.set_text(new_html)
            except Exception as e:
                pass

        body = flow.response.content or b""
        body_sha256 = None
        if body:
            is_js = any(ct in content_type for ct in ("javascript", "ecmascript"))
            size_ok = (HASH_MAX_BYTES <= 0) or (len(body) <= HASH_MAX_BYTES)
            should_hash = size_ok and (is_js if HASH_JS_ONLY else True)
            if should_hash:
                body_sha256 = hashlib.sha256(body).hexdigest()
        # Store small snippet for scripts to enable lightweight analysis
        body_snippet = None
        if body and any(ct in content_type for ct in ("javascript", "ecmascript")):
            snippet_bytes = body[:4096]
            body_snippet = snippet_bytes.decode("utf-8", errors="replace")

        record = {
            "url": flow.request.pretty_url,
            "method": flow.request.method,
            "headers": dict(flow.request.headers),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "hostname": (flow.request.host or "").strip().lower().rstrip("."),
            "response_status": flow.response.status_code,
            "response_headers": response_headers,
            "set_cookies": set_cookies,
            "content_type": content_type,
            "response_size": len(body) if body else 0,
            "response_body_sha256": body_sha256,
            "response_body_snippet": body_snippet,
        }
        if self._e6_active:
            record["e6_blocked"] = False
            record["e6_cross_site_transitions"] = self._e6_cross_site_count
            record["e6_budget"] = self._e6_budget

        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    def done(self) -> None:
        """Called when the addon is shutting down."""
        self._file.close()


addons = [CaptureAddon()]
