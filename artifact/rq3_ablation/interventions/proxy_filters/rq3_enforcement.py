"""RQ3 proxy-layer enforcement — mitmproxy addon.

Drops requests at the network layer based on environment-controlled toggles:

    RQ3_BLOCK_SEARCH=1    drop any request to a known search-engine host/path
    RQ3_BLOCK_OFFDOMAIN=1 drop any cross-eTLD+1 navigation
    RQ3_BLOCK_CMP=1       drop any request to a known consent-management CDN

Paired with the agent-side schema patches in `../schema_patches/`. The schema
patches remove the tool primitives from the agent's action vocabulary; this
proxy addon enforces the same restriction at the wire level so the agent
cannot bypass it by composing the same outcome out of other primitives. See
Appendix F (Table 12) for the schema-vs-proxy decomposition.

Load with mitmproxy:

    mitmdump -s rq3_enforcement.py

Blocked requests are still written to the JSONL capture (via the main capture
addon) with `rq3_blocked: true` and a `rq3_reason` of "search" / "offdomain" /
"cmp" so the analysis pipeline can attribute condition-side blocks.
"""
from __future__ import annotations

import os
from typing import Optional

import mitmproxy.http

# ---- toggles (environment-controlled) --------------------------------------

RQ3_BLOCK_SEARCH = os.environ.get("RQ3_BLOCK_SEARCH", "0").strip() == "1"
RQ3_BLOCK_OFFDOMAIN = os.environ.get("RQ3_BLOCK_OFFDOMAIN", "0").strip() == "1"
RQ3_BLOCK_CMP = os.environ.get("RQ3_BLOCK_CMP", "0").strip() == "1"

# ---- target host sets ------------------------------------------------------

# Search-engine hosts dropped under RQ3_BLOCK_SEARCH.
# google.com / bing.com / yahoo / yandex root pages are NOT search pages, so
# they are matched only when the path starts with one of the patterns below.
SEARCH_ENGINE_HOSTS = frozenset({
    "www.google.com", "google.com",
    "www.bing.com", "bing.com",
    "duckduckgo.com", "www.duckduckgo.com",
    "search.yahoo.com", "www.yahoo.com",
    "www.baidu.com", "baidu.com",
    "yandex.com", "www.yandex.com",
})
SEARCH_PATH_PATTERNS = ("/search", "/s?", "/s/", "/results")

# Multi-purpose hosts whose root is a normal landing page, not a search page.
# For these, only path-based matches count.
SEARCH_PATH_REQUIRED_HOSTS = frozenset({
    "google.com", "www.google.com",
    "bing.com", "www.bing.com",
    "yahoo.com", "search.yahoo.com",
})

# CMP / consent-banner CDNs (registrable-domain match).
# Blocking these prevents the consent banner from rendering, so the site falls
# back to its default (typically pre-consent) state and the agent has no
# Accept/Reject button to click. Targets the 78.3% Accept finding documented
# in the RQ2 results section of the paper.
CMP_REGISTRABLE_DOMAINS = frozenset({
    "cookielaw.org",       # OneTrust
    "cookiebot.com",       # Cookiebot
    "trustarc.com",        # TrustArc
    "didomi.io",           # Didomi
    "usercentrics.eu",     # Usercentrics
    "usercentrics.com",
    "consensu.org",        # IAB TCF vendor list CDN
    "sp-prod.net",         # Sourcepoint
    "sourcepoint.com",
    "sourcepointcmp.com",
    "iubenda.com",         # iubenda
    "civiccomputing.com",  # CookieControl
    "trustcommander.net",  # Commanders Act
})


# ---- helpers ---------------------------------------------------------------

def extract_registrable_domain(hostname: str) -> str:
    """Best-effort eTLD+1 extraction without an external PSL dependency."""
    host = (hostname or "").strip().lower().rstrip(".")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    cc_prefixes = {"co", "com", "org", "net", "ac", "edu", "gov", "ne", "or"}
    if parts[-2] in cc_prefixes and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_search_request(hostname: str, path: str) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if host not in SEARCH_ENGINE_HOSTS:
        return False
    if host in SEARCH_PATH_REQUIRED_HOSTS:
        return any(path.startswith(p) for p in SEARCH_PATH_PATTERNS)
    return True


def is_cmp_request(hostname: str) -> bool:
    reg = extract_registrable_domain(hostname)
    return reg in CMP_REGISTRABLE_DOMAINS


def is_navigation_like(flow: mitmproxy.http.HTTPFlow) -> bool:
    """Approximate the browser's notion of a top-level / iframe navigation."""
    if flow.request.method.upper() != "GET":
        return False
    accept = flow.request.headers.get("accept", "")
    sec_dest = flow.request.headers.get("sec-fetch-dest", "")
    if "text/html" in accept:
        return True
    if sec_dest in ("document", "iframe", "frame"):
        return True
    return False


# ---- addon -----------------------------------------------------------------

class RQ3EnforcementAddon:
    def __init__(self) -> None:
        self._target_domain: str = ""
        self.blocked_search = 0
        self.blocked_offdomain = 0
        self.blocked_cmp = 0

    def _block(self, flow: mitmproxy.http.HTTPFlow, reason: str) -> None:
        flow.response = mitmproxy.http.Response.make(
            451,
            f"RQ3: {reason} blocked".encode(),
            {"Content-Type": "text/plain", "X-RQ3-Block": reason},
        )

    def request(self, flow: mitmproxy.http.HTTPFlow) -> None:
        if not (RQ3_BLOCK_SEARCH or RQ3_BLOCK_OFFDOMAIN or RQ3_BLOCK_CMP):
            return

        host = (flow.request.host or "").strip().lower().rstrip(".")
        path = flow.request.path or ""

        if RQ3_BLOCK_CMP and is_cmp_request(host):
            self.blocked_cmp += 1
            self._block(flow, "cmp")
            return

        if RQ3_BLOCK_SEARCH and is_search_request(host, path):
            self.blocked_search += 1
            self._block(flow, "search")
            return

        if RQ3_BLOCK_OFFDOMAIN and is_navigation_like(flow):
            # Latch the target eTLD+1 from the first non-search navigation.
            if not self._target_domain and host and not is_search_request(host, path):
                self._target_domain = extract_registrable_domain(host)
            if self._target_domain:
                req_etld = extract_registrable_domain(host)
                if req_etld and req_etld != self._target_domain:
                    self.blocked_offdomain += 1
                    self._block(flow, "offdomain")
                    return


addons = [RQ3EnforcementAddon()]
