"""MetricAnalyzer — tracking exposure metrics M1-M6.

Calculates tracking metrics from session data:
  M1: Unique tracker domain count
  M2: Cookie count partitioned by 1st/3rd party
  M3: Fingerprinting API call count
  M4: Exposed data field types
  M5: Cross-session linkability score (0.0-1.0)
  M6: Agent-specific identifier leakage detection
"""

from __future__ import annotations

from itertools import combinations
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from agentcloak.core.enums import DataFieldType
from agentcloak.core.metrics import AgentLeakageResult
from agentcloak.core.models import SessionData


class CookieMetrics(BaseModel):
    """M2 result: cookie counts partitioned by party."""

    first_party: int
    third_party: int


# Known automation / headless markers
_HEADLESS_USER_AGENT_MARKERS = [
    "HeadlessChrome",
    "HeadlessFirefox",
    "PhantomJS",
]

_AUTOMATION_SIGNATURES = [
    "__selenium_unwrapped",
    "__webdriver_evaluate",
    "__driver_evaluate",
    "__webdriver_unwrapped",
    "__selenium_evaluate",
    "__fxdriver_evaluate",
    "__fxdriver_unwrapped",
    "calledSelenium",
    "_Selenium_IDE_Recorder",
    "_selenium",
    "callPhantom",
    "_phantom",
    "phantom",
    "domAutomation",
    "domAutomationController",
]

_CDP_HEADER_MARKERS = [
    "X-DevTools",
    "X-Chrome-DevTools",
]


class MetricAnalyzer:
    """Calculates tracking exposure metrics M1-M6 from session data."""

    # ------------------------------------------------------------------ M1
    def calculate_m1_tracker_domains(self, session: SessionData) -> int:
        """M1: Count of unique tracker domains contacted during the session.

        A domain is counted if it appears as ``tracker_domain`` on any
        ``NetworkRequest`` flagged ``is_tracker=True``, **or** as the
        ``domain`` on any ``TrackerDetection``.
        """
        domains: set[str] = set()

        for req in session.network_requests:
            if req.is_tracker and req.tracker_domain:
                domains.add(req.tracker_domain.lower())

        for det in session.tracker_detections:
            if det.domain:
                domains.add(det.domain.lower())

        return len(domains)

    # ------------------------------------------------------------------ M2
    def calculate_m2_cookies(self, session: SessionData) -> CookieMetrics:
        """M2: Cookie count partitioned by 1st-party / 3rd-party."""
        first = 0
        third = 0
        for cookie in session.cookies:
            if cookie.is_third_party:
                third += 1
            else:
                first += 1
        return CookieMetrics(first_party=first, third_party=third)

    # ------------------------------------------------------------------ M3
    def calculate_m3_fingerprint_calls(self, session: SessionData) -> int:
        """M3: Total number of fingerprinting API calls detected."""
        return len(session.fingerprint_api_calls)

    # ------------------------------------------------------------------ M4
    def calculate_m4_data_types(self, session: SessionData) -> list[DataFieldType]:
        """M4: Distinct data field types exposed across all tracker detections."""
        types: set[DataFieldType] = set()
        for det in session.tracker_detections:
            for field in det.collected_fields:
                types.add(field.field_type)
        return sorted(types, key=lambda t: t.value)

    # ------------------------------------------------------------------ M5
    def calculate_m5_linkability(self, sessions: list[SessionData]) -> float:
        """M5: Cross-session linkability score (0.0-1.0).

        Uses average pairwise Jaccard similarity across cookie names and
        fingerprint API return-value hashes.  Identical patterns across
        all sessions yield 1.0; completely disjoint patterns yield 0.0.
        """
        if len(sessions) < 2:
            return 0.0

        pair_scores: list[float] = []
        for s1, s2 in combinations(sessions, 2):
            pair_scores.append(self._pairwise_linkability(s1, s2))

        return sum(pair_scores) / len(pair_scores)

    # ------------------------------------------------------------------ M6
    def calculate_m6_agent_leakage(self, session: SessionData) -> AgentLeakageResult:
        """M6: Detect agent-specific identifier leakage.

        Checks for:
        - ``navigator.webdriver`` flag exposure in fingerprint API calls
        - Headless browser markers in user-agent strings
        - Automation signatures in fingerprint API calls and network headers
        """
        webdriver_exposed = self._check_webdriver_flag(session)
        headless_markers = self._detect_headless_markers(session)
        automation_sigs = self._detect_automation_signatures(session)

        total_checks = 1 + len(_HEADLESS_USER_AGENT_MARKERS) + len(_AUTOMATION_SIGNATURES) + len(_CDP_HEADER_MARKERS)
        detected_count = (
            (1 if webdriver_exposed else 0)
            + len(headless_markers)
            + len(automation_sigs)
        )
        leakage_score = min(detected_count / total_checks, 1.0) if total_checks > 0 else 0.0

        return AgentLeakageResult(
            webdriver_flag_exposed=webdriver_exposed,
            headless_markers_detected=headless_markers,
            automation_signatures=automation_sigs,
            leakage_score=leakage_score,
        )

    # ================================================================
    # Private helpers
    # ================================================================

    def _pairwise_linkability(self, s1: SessionData, s2: SessionData) -> float:
        """Jaccard similarity between two sessions based on cookies + fingerprints."""
        cookie_sim = self._jaccard(
            {c.name + "|" + c.domain for c in s1.cookies},
            {c.name + "|" + c.domain for c in s2.cookies},
        )
        fp_sim = self._jaccard(
            {
                call.return_value_hash
                for call in s1.fingerprint_api_calls
                if call.return_value_hash
            },
            {
                call.return_value_hash
                for call in s2.fingerprint_api_calls
                if call.return_value_hash
            },
        )
        # Equal weight to cookie and fingerprint similarity
        return (cookie_sim + fp_sim) / 2.0

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a and not b:
            return 0.0  # both empty → no tracking data → unlinkable
        union = a | b
        if not union:
            return 0.0
        return len(a & b) / len(union)

    # -- webdriver flag ------------------------------------------------
    def _check_webdriver_flag(self, session: SessionData) -> bool:
        """Return True if navigator.webdriver is exposed in fingerprint calls."""
        for call in session.fingerprint_api_calls:
            api = call.api_name.lower()
            if "webdriver" in api:
                return True
        return False

    # -- headless markers ----------------------------------------------
    def _detect_headless_markers(self, session: SessionData) -> list[str]:
        """Detect headless browser markers in user-agent strings."""
        markers_found: list[str] = []

        # Check network request user-agent headers
        for req in session.network_requests:
            ua = req.headers.get("User-Agent", "") or req.headers.get("user-agent", "")
            for marker in _HEADLESS_USER_AGENT_MARKERS:
                if marker.lower() in ua.lower() and marker not in markers_found:
                    markers_found.append(marker)

        # Check fingerprint API calls for navigator.userAgent
        for call in session.fingerprint_api_calls:
            if "useragent" in call.api_name.lower() and call.return_value_hash:
                # The return_value_hash might contain the raw value in test scenarios
                for marker in _HEADLESS_USER_AGENT_MARKERS:
                    if marker.lower() in call.return_value_hash.lower() and marker not in markers_found:
                        markers_found.append(marker)

        return markers_found

    # -- automation signatures -----------------------------------------
    def _detect_automation_signatures(self, session: SessionData) -> list[str]:
        """Detect automation signatures in fingerprint calls and network headers."""
        sigs_found: list[str] = []

        # Check fingerprint API calls
        for call in session.fingerprint_api_calls:
            for sig in _AUTOMATION_SIGNATURES:
                if sig.lower() in call.api_name.lower() and sig not in sigs_found:
                    sigs_found.append(sig)
                if call.arguments:
                    for arg in call.arguments:
                        if sig.lower() in arg.lower() and sig not in sigs_found:
                            sigs_found.append(sig)

        # Check network request headers for CDP markers
        for req in session.network_requests:
            for header_key in req.headers:
                for cdp_marker in _CDP_HEADER_MARKERS:
                    if cdp_marker.lower() in header_key.lower() and cdp_marker not in sigs_found:
                        sigs_found.append(cdp_marker)

        return sigs_found
