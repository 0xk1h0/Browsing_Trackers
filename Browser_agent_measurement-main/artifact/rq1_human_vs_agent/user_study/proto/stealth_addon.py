"""Stealth-JS injection addon for the user-study mitmproxy chain.

Loaded AFTER the agentcloak measurement addon so that both inject into the
same HTML response. The agentcloak addon writes hook.js; this addon also
writes a small puppeteer-stealth-equivalent script that hides obvious
"browser is automated" signals from bot detectors (AWS WAF, Cloudflare
Turnstile, Datadome, etc.).

Why we want this in the human-side measurement:
  * Real participants on their own browser do NOT trigger WAF challenges as
    aggressively as our Xvfb + headed Chromium does (no GPU, missing
    plugins, headless-looking navigator state).
  * Without stealth, sites serve a degraded experience and we under-measure
    the tracker exposure a real human would face. Participants also get
    stuck on captchas, failing the task.

Trade-off documented in study limitations: the existing per-agent
measurements in the paper did NOT enable stealth, so any human↔agent
comparison must either re-run agents with the same stealth or treat the
asymmetry as a conservative-lower-bound caveat.

Activate by setting env var AGENTCLOAK_STEALTH=1 when starting mitmdump.
"""
from __future__ import annotations

import os
import re

import mitmproxy.http


_ENABLED = os.environ.get("AGENTCLOAK_STEALTH", "0").strip() == "1"

STEALTH_JS = """<script>
(function() {
  try {
    // 1. Remove navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    // 2. Spoof plugins (real Chrome has at least PDF + Native Client)
    Object.defineProperty(navigator, 'plugins', {
      get: () => {
        const p = [
          {name: 'PDF Viewer',         filename: 'internal-pdf-viewer',  description: 'Portable Document Format'},
          {name: 'Chrome PDF Viewer',  filename: 'internal-pdf-viewer',  description: ''},
          {name: 'Chromium PDF Viewer',filename: 'internal-pdf-viewer',  description: ''},
        ];
        p.length = 3;
        return p;
      }
    });
    // 3. Spoof navigator.languages
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    // 4. window.chrome runtime stub (absence is a headless flag)
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) {
      window.chrome.runtime = {connect: () => {}, sendMessage: () => {}};
    }
    // 5. permissions.query — Notification.permission must match query state
    const origQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (origQuery) {
      window.navigator.permissions.query = (params) =>
        params && params.name === 'notifications'
          ? Promise.resolve({state: typeof Notification !== 'undefined' ? Notification.permission : 'default'})
          : origQuery(params);
    }
    // 6. WebGL vendor/renderer — replace "Google SwiftShader" (headless fallback)
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
      if (param === 37445) return 'Intel Inc.';                  // UNMASKED_VENDOR_WEBGL
      if (param === 37446) return 'Intel Iris OpenGL Engine';    // UNMASKED_RENDERER_WEBGL
      return getParameter.call(this, param);
    };
  } catch (e) { /* swallow; never break the page */ }
})();
</script>"""


# Headers that can prevent our injected JS (hook.js + stealth.js) from
# executing in the participant's browser. The measurement addon used to
# strip only the canonical "Content-Security-Policy" — but sites like Apple
# and ArXiv send the legacy `X-WebKit-CSP` / `X-Content-Security-Policy`,
# or use `Content-Security-Policy-Report-Only` (no enforcement, but some
# strict-mode browsers still surface violations). COEP / COOP / CORP can
# also gate scripts as cross-origin. We strip them all so the JS-side
# fingerprint hooks always fire — that is the pre-encryption visibility
# the study depends on.
_CSP_HEADERS = (
    "content-security-policy",
    "content-security-policy-report-only",
    "x-content-security-policy",
    "x-webkit-csp",
    "cross-origin-embedder-policy",
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
    "permissions-policy",
    "feature-policy",
)

# <meta http-equiv="Content-Security-Policy" content="..."> — many SPAs
# (Apple's marketing pages included) ship CSP in HTML so a header strip
# isn't enough. The pattern tolerates extra attributes / quoting variants.
_META_CSP_RE = re.compile(
    r'<meta\b[^>]*http-equiv\s*=\s*["\']?content-security-policy[^"\'>]*["\']?[^>]*>',
    re.IGNORECASE,
)


class StripCSP:
    """Strip CSP/COEP-style headers and meta tags from every response so
    the measurement and stealth scripts always execute. Unconditional —
    even when stealth injection is off we still want hook.js to run."""

    def response(self, flow: mitmproxy.http.HTTPFlow) -> None:
        resp = flow.response
        if not resp:
            return
        # mitmproxy header access is case-insensitive; one `del` per name
        # covers all casings used by the origin.
        for h in _CSP_HEADERS:
            if h in resp.headers:
                del resp.headers[h]
        ctype = (resp.headers.get("content-type") or "").lower()
        if "text/html" not in ctype or not resp.content:
            return
        try:
            html = resp.content.decode("utf-8", errors="replace")
        except Exception:
            return
        new_html = _META_CSP_RE.sub("", html)
        if new_html != html:
            new_bytes = new_html.encode("utf-8")
            resp.content = new_bytes
            if "content-length" in resp.headers:
                resp.headers["content-length"] = str(len(new_bytes))


class StealthInject:
    def response(self, flow: mitmproxy.http.HTTPFlow) -> None:
        if not _ENABLED:
            return
        resp = flow.response
        if not resp or resp.status_code != 200:
            return
        ctype = (resp.headers.get("content-type") or "").lower()
        if "text/html" not in ctype:
            return
        try:
            html = resp.content.decode("utf-8", errors="replace")
        except Exception:
            return
        if not html:
            return
        # Inject right after the opening <head> tag if present, else after <html>.
        if re.search(r"<head[^>]*>", html, flags=re.IGNORECASE):
            new_html = re.sub(
                r"(<head[^>]*>)",
                lambda m: m.group(1) + STEALTH_JS,
                html, count=1, flags=re.IGNORECASE,
            )
        elif re.search(r"<html[^>]*>", html, flags=re.IGNORECASE):
            new_html = re.sub(
                r"(<html[^>]*>)",
                lambda m: m.group(1) + "<head>" + STEALTH_JS + "</head>",
                html, count=1, flags=re.IGNORECASE,
            )
        else:
            return
        new_bytes = new_html.encode("utf-8")
        resp.content = new_bytes
        # Update content-length so downstream doesn't truncate.
        if "content-length" in resp.headers:
            resp.headers["content-length"] = str(len(new_bytes))


# Order matters: StripCSP runs first so by the time StealthInject (and the
# upstream measurement addon's hook.js injection) hits, the response has
# no CSP-style hindrance to inline-script execution.
addons = [StripCSP(), StealthInject()]
