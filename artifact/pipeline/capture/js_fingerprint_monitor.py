"""JavaScript fingerprinting API monitor for AgentCloak measurement framework.

Generates a Playwright CDP Runtime.evaluate injection script that monkey-patches
known fingerprinting APIs (canvas, WebGL, AudioContext, navigator properties, etc.)
to record every call.  The recorded raw call dicts are then parsed into
``FingerprintAPICall`` Pydantic models for downstream analysis.

Requirements: 4.4, 4.5
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from agentcloak.core.models import FingerprintAPICall

# ---------------------------------------------------------------------------
# Monitored fingerprinting APIs
# ---------------------------------------------------------------------------

MONITORED_APIS: list[str] = [
    "HTMLCanvasElement.prototype.toDataURL",
    "WebGLRenderingContext.prototype.getParameter",
    "AudioContext.prototype.createOscillator",
    "navigator.plugins",
    "navigator.languages",
    "screen.width",
    "screen.height",
]


# ---------------------------------------------------------------------------
# JSFingerprintMonitor
# ---------------------------------------------------------------------------


class JSFingerprintMonitor:
    """Monitor JavaScript fingerprinting API calls via CDP injection.

    Usage workflow:
    1. Call ``get_injection_script()`` to obtain a JS snippet.
    2. Inject the snippet into the page via Playwright CDP
       ``Runtime.evaluate`` **before** any page scripts execute.
    3. After page load, retrieve ``window.__fp_calls`` (a JSON array of
       raw call dicts).
    4. Pass the raw dicts to ``parse_api_calls()`` to obtain typed
       ``FingerprintAPICall`` instances.
    """

    def __init__(self) -> None:
        self._monitored_apis: list[str] = list(MONITORED_APIS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_injection_script(self) -> str:
        """Return a JavaScript snippet that monkey-patches monitored APIs.

        The script stores intercepted calls in ``window.__fp_calls`` as an
        array of plain objects with the following shape::

            {
                "api_name": "<fully-qualified API name>",
                "caller_script": "<script URL or 'inline'>",
                "timestamp": "<ISO-8601 string>",
                "arguments": [<string representations>] | null,
                "return_value_hash": "<SHA-256 hex>" | null
            }
        """
        api_patches = []
        for api in self._monitored_apis:
            api_patches.append(self._generate_patch(api))

        patches_js = "\n".join(api_patches)

        return (
            "(function() {\n"
            "  if (window.__fp_calls) return;\n"
            "  window.__fp_calls = [];\n"
            "\n"
            "  function _getCallerScript() {\n"
            "    try {\n"
            "      var err = new Error();\n"
            "      var stack = err.stack || '';\n"
            "      var lines = stack.split('\\n');\n"
            "      for (var i = 2; i < lines.length; i++) {\n"
            "        var m = lines[i].match(/(?:at\\s+)?(?:.*?\\s)?\\(?(https?:\\/\\/[^\\s\\)]+)/);\n"
            "        if (m) return m[1];\n"
            "      }\n"
            "    } catch(e) {}\n"
            "    return 'inline';\n"
            "  }\n"
            "\n"
            "  function _hashValue(val) {\n"
            "    if (val === undefined || val === null) return null;\n"
            "    var s = String(val);\n"
            "    var hash = 0;\n"
            "    for (var i = 0; i < s.length; i++) {\n"
            "      hash = ((hash << 5) - hash) + s.charCodeAt(i);\n"
            "      hash |= 0;\n"
            "    }\n"
            "    return hash.toString(16);\n"
            "  }\n"
            "\n"
            "  function _record(apiName, args, retVal) {\n"
            "    window.__fp_calls.push({\n"
            "      api_name: apiName,\n"
            "      caller_script: _getCallerScript(),\n"
            "      timestamp: new Date().toISOString(),\n"
            "      arguments: args ? Array.prototype.map.call(args, String) : null,\n"
            "      return_value_hash: _hashValue(retVal)\n"
            "    });\n"
            "  }\n"
            "\n"
            f"{patches_js}\n"
            "})();\n"
        )

    def parse_api_calls(
        self,
        raw_calls: list[dict],
    ) -> list[FingerprintAPICall]:
        """Convert raw JS call dicts into typed ``FingerprintAPICall`` models.

        Each dict is expected to have at least ``api_name``, ``caller_script``,
        and ``timestamp`` keys.  Missing or malformed entries are silently
        skipped so that a single bad record does not break the entire batch.
        """
        results: list[FingerprintAPICall] = []
        for raw in raw_calls:
            parsed = self._parse_single(raw)
            if parsed is not None:
                results.append(parsed)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_patch(api_path: str) -> str:
        """Generate a JS monkey-patch snippet for a single API path."""
        parts = api_path.split(".")

        # Property-style APIs (navigator.plugins, screen.width, etc.)
        if "prototype" not in api_path and len(parts) == 2:
            obj, prop = parts
            return (
                f"  try {{\n"
                f"    var _orig_{prop} = Object.getOwnPropertyDescriptor({obj}, '{prop}');\n"
                f"    if (_orig_{prop} && _orig_{prop}.get) {{\n"
                f"      Object.defineProperty({obj}, '{prop}', {{\n"
                f"        get: function() {{\n"
                f"          var val = _orig_{prop}.get.call(this);\n"
                f"          _record('{api_path}', null, val);\n"
                f"          return val;\n"
                f"        }},\n"
                f"        configurable: true\n"
                f"      }});\n"
                f"    }}\n"
                f"  }} catch(e) {{}}\n"
            )

        # Method-style APIs (HTMLCanvasElement.prototype.toDataURL, etc.)
        # Split into object path and method name
        obj_path = ".".join(parts[:-1])
        method = parts[-1]
        safe_name = api_path.replace(".", "_")
        return (
            f"  try {{\n"
            f"    var _orig_{safe_name} = {obj_path}.{method};\n"
            f"    if (typeof _orig_{safe_name} === 'function') {{\n"
            f"      {obj_path}.{method} = function() {{\n"
            f"        var ret = _orig_{safe_name}.apply(this, arguments);\n"
            f"        _record('{api_path}', arguments, ret);\n"
            f"        return ret;\n"
            f"      }};\n"
            f"    }}\n"
            f"  }} catch(e) {{}}\n"
        )

    @staticmethod
    def _parse_single(raw: dict) -> FingerprintAPICall | None:
        """Parse a single raw dict into a ``FingerprintAPICall``, or ``None``."""
        try:
            # Handle new hook.js format
            if "source" in raw and raw["source"] == "fingerprint":
                api_name = str(raw.get("type", ""))
                stack = str(raw.get("stack", ""))
                # simple extraction for first url in stack trace
                caller_script = "inline"
                if stack and "http" in stack:
                    import re
                    m = re.search(r'(https?://[^\s\)]+)', stack)
                    if m:
                        caller_script = m.group(1)
                ts_raw = raw.get("timestamp")
                
                details = raw.get("details") or {}
                arguments = details.get("args")
                if arguments is not None:
                    arguments = [str(a) for a in arguments]
                return_value_hash = None
            else:
                # Handle old Playwright CDP format
                api_name = str(raw.get("api_name", ""))
                caller_script = str(raw.get("caller_script", "inline"))
                ts_raw = raw.get("timestamp")
                arguments = raw.get("arguments")
                if arguments is not None:
                    arguments = [str(a) for a in arguments]
                return_value_hash = raw.get("return_value_hash")
                if return_value_hash is not None:
                    return_value_hash = str(return_value_hash)

            if not api_name:
                return None

            # Parse timestamp
            if isinstance(ts_raw, str):
                # Handle ISO-8601 with or without timezone
                ts_raw = ts_raw.replace("Z", "+00:00")
                timestamp = datetime.fromisoformat(ts_raw)
            elif isinstance(ts_raw, (int, float)):
                timestamp = datetime.fromtimestamp(ts_raw / 1000, tz=timezone.utc)
            else:
                timestamp = datetime.now(tz=timezone.utc)

            return FingerprintAPICall(
                api_name=api_name,
                caller_script=caller_script,
                timestamp=timestamp,
                arguments=arguments,
                return_value_hash=return_value_hash,
            )
        except Exception:
            return None
