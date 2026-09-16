"""Traffic analyzer pipeline for tracker detection in captured network traffic.

Uses the existing FilterListEngine, TrackerMLClassifier, and TrackerInterceptor
to classify captured network requests as trackers and produce session summaries.

Extended to integrate CookieAnalyzer and JSFingerprintMonitor results, support
first-party/third-party request classification, and provide an ``analyze_extended``
entry point for the full measurement pipeline.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 4.1, 4.6, 4.7
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from agentcloak.core.config import TrackerConfig
from agentcloak.core.enums import TrackerType
from agentcloak.core.models import (
    CookieRecord,
    FingerprintAPICall,
    NetworkRequest,
    TrackerDetection,
)
from agentcloak.defense.tracker_interceptor import TrackerInterceptor
from agentcloak.measurement.cookie_analyzer import CookieAnalyzer, CookieAnalysisResult
from agentcloak.measurement.js_fingerprint_monitor import JSFingerprintMonitor

logger = logging.getLogger(__name__)
_TIMING_ENABLED = os.environ.get("AGENTCLOAK_TIMING", "0").strip() == "1"
_DISABLE_COOKIE_SYNC = os.environ.get("AGENTCLOAK_DISABLE_COOKIE_SYNC", "0").strip() == "1"

# Known fingerprinting domains and their associated API names
KNOWN_FINGERPRINTING_DOMAINS: dict[str, list[str]] = {
    "fingerprintjs.com": ["canvas.toDataURL", "webgl.getParameter", "AudioContext"],
    "cdn.fingerprintjs.com": ["canvas.toDataURL", "webgl.getParameter", "AudioContext"],
    "fp.cdn.fingerprintjs.com": ["canvas.toDataURL", "webgl.getParameter"],
    "api.fingerprintjs.com": ["canvas.toDataURL", "webgl.getParameter"],
    "fingerprint.com": ["canvas.toDataURL", "navigator.userAgent"],
    "openfpcdn.io": ["canvas.toDataURL", "webgl.getParameter"],
    "impervil.com": ["canvas.toDataURL", "navigator.plugins"],
    "arkoselabs.com": ["canvas.toDataURL", "AudioContext"],
    "perimeterx.net": ["canvas.toDataURL", "webgl.getParameter"],
    "datadome.co": ["canvas.toDataURL", "navigator.userAgent"],
}

# Heuristic domain-category mapping to reduce "other" tracker type bias
TRACKER_DOMAIN_CATEGORIES: dict[TrackerType, set[str]] = {
    TrackerType.AD_NETWORK: {
        "doubleclick.net",
        "googlesyndication.com",
        "googleadservices.com",
        "amazon-adsystem.com",
        "criteo.com",
        "criteo.net",
        "adnxs.com",
        "rubiconproject.com",
        "pubmatic.com",
        "openx.net",
        "adsrvr.org",
        "mathtag.com",
        "serving-sys.com",
        "bidswitch.net",
        "sharethrough.com",
        "indexexchange.com",
    },
    TrackerType.ANALYTICS: {
        "google-analytics.com",
        "googletagmanager.com",
        "scorecardresearch.com",
        "quantserve.com",
        "chartbeat.com",
        "parsely.com",
        "mixpanel.com",
        "amplitude.com",
        "segment.com",
        "segment.io",
        "newrelic.com",
        "nr-data.net",
    },
    TrackerType.SOCIAL_TRACKER: {
        "facebook.com",
        "facebook.net",
        "fbcdn.net",
        "linkedin.com",
        "t.co",
        "ads-twitter.com",
        "snapchat.com",
        "tiktok.com",
        "byteoversea.com",
    },
    TrackerType.SESSION_REPLAY: {
        "hotjar.com",
        "mouseflow.com",
        "fullstory.com",
    },
}


class FingerprintingSummary(BaseModel):
    """Summary of JS fingerprinting API calls detected during a session."""

    fingerprinting_api_call_count: int = 0
    calling_script_domains: list[str] = Field(default_factory=list)


class SessionSummary(BaseModel):
    """Per-session tracker analysis summary.

    New fields have defaults so existing code that constructs SessionSummary
    without them continues to work.
    """

    total_requests: int
    tracker_requests: int
    unique_tracker_domains: int
    tracker_type_distribution: dict[str, int]  # TrackerType.value → count

    # --- Extended fields (backward-compatible defaults) ---
    first_party_requests: int = 0
    third_party_requests: int = 0
    cookie_analysis: CookieAnalysisResult | None = None
    fingerprinting_summary: FingerprintingSummary | None = None
    # Timing (ms) for analysis stages
    detect_ms: float = 0.0
    cookie_fp_ms: float = 0.0
    analysis_total_ms: float = 0.0


class TrafficAnalyzer:
    """Network traffic tracker analysis pipeline.

    Uses existing FilterListEngine + TrackerMLClassifier + TrackerInterceptor
    to classify captured network requests and produce detection results with
    session summaries.
    """

    def __init__(
        self,
        filter_lists: list[str] | None = None,
        ml_model_path: str = "",
    ) -> None:
        """Initialize with existing detection components.

        Parameters
        ----------
        filter_lists:
            Filter list names for the FilterListEngine. If None, uses the
            default TrackerConfig.filter_lists (easylist, easyprivacy, disconnect).
            DO NOT change the default.
        ml_model_path:
            Path to the ML model for TrackerMLClassifier.
        """
        config = TrackerConfig()
        if filter_lists is not None:
            config = TrackerConfig(filter_lists=filter_lists, ml_model_path=ml_model_path)
        elif ml_model_path:
            config = TrackerConfig(ml_model_path=ml_model_path)

        self._interceptor = TrackerInterceptor(config=config)
        logger.info(
            "TrafficAnalyzer initialized with filter_lists=%s",
            config.filter_lists,
        )

    async def analyze(
        self,
        requests: list[NetworkRequest],
    ) -> tuple[list[TrackerDetection], SessionSummary]:
        """Analyze network requests for trackers.

        Calls TrackerInterceptor.detect() for each request to perform hybrid
        detection (filter list + ML classifier). Produces TrackerDetection
        objects and a SessionSummary.

        Parameters
        ----------
        requests:
            List of captured NetworkRequest objects.

        Returns
        -------
        tuple[list[TrackerDetection], SessionSummary]
            Detected trackers and session summary statistics.
        """
        detections: list[TrackerDetection] = []
        t_start = time.monotonic()

        for req in requests:
            try:
                req.is_tracker = False
                req.tracker_domain = None
                req.tracker_type = None
                script_content = req.response_body_snippet if req.response_body_snippet else None
                result = await self._interceptor.detect(
                    request_url=req.url,
                    script_content=script_content,
                )

                if not result.is_tracker:
                    continue

                tracker_type = result.tracker_type
                collected_fields: list = []

                # Check for known fingerprinting domains
                domain = self._extract_domain(req.url)
                fp_api_names = self._match_fingerprinting_domain(domain)
                if fp_api_names:
                    tracker_type = TrackerType.FINGERPRINTER
                    logger.debug(
                        "Fingerprinting domain detected: %s (APIs: %s)",
                        domain,
                        fp_api_names,
                    )

                # Heuristic fallback: classify by known tracker domains / URL patterns
                if tracker_type == TrackerType.OTHER:
                    inferred = self._infer_tracker_type_from_domain(domain, req.url)
                    if inferred != TrackerType.OTHER:
                        tracker_type = inferred

                req.is_tracker = True
                req.tracker_domain = result.domain
                req.tracker_type = tracker_type.value

                detection = TrackerDetection(
                    url=req.url,
                    domain=result.domain,
                    tracker_type=tracker_type,
                    detection_method=result.detection_method,
                    collected_fields=collected_fields,
                    timestamp=req.timestamp,
                    blocked=False,
                )
                detections.append(detection)

            except Exception:
                logger.warning(
                    "Failed to analyze request: %s — skipping",
                    req.url,
                    exc_info=True,
                )
                continue

        summary = self.compute_summary(detections, len(requests))
        summary.detect_ms = (time.monotonic() - t_start) * 1000.0
        summary.analysis_total_ms = summary.detect_ms
        if _TIMING_ENABLED:
            logger.info(
                "TrafficAnalyzer.analyze: requests=%d detections=%d elapsed_ms=%.1f",
                len(requests),
                len(detections),
                summary.detect_ms,
            )
        return detections, summary

    def compute_summary(
        self,
        detections: list[TrackerDetection],
        total_requests: int,
    ) -> SessionSummary:
        """Compute session summary statistics from detections.

        Parameters
        ----------
        detections:
            List of TrackerDetection objects.
        total_requests:
            Total number of requests in the session.

        Returns
        -------
        SessionSummary
            Summary with tracker counts, unique domains, and type distribution.
        """
        unique_domains: set[str] = set()
        type_distribution: dict[str, int] = {}

        for det in detections:
            unique_domains.add(det.domain)
            type_key = det.tracker_type.value
            type_distribution[type_key] = type_distribution.get(type_key, 0) + 1

        return SessionSummary(
            total_requests=total_requests,
            tracker_requests=len(detections),
            unique_tracker_domains=len(unique_domains),
            tracker_type_distribution=type_distribution,
        )

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract domain from a URL."""
        try:
            parsed = urlparse(url)
            return parsed.netloc or parsed.path.split("/")[0]
        except Exception:
            return url

    @staticmethod
    def _match_fingerprinting_domain(domain: str) -> list[str]:
        """Check if a domain matches a known fingerprinting domain.

        Returns the associated fingerprinting API names if matched,
        or an empty list otherwise.
        """
        # Direct match
        if domain in KNOWN_FINGERPRINTING_DOMAINS:
            return KNOWN_FINGERPRINTING_DOMAINS[domain]

        # Subdomain match (e.g., cdn.fingerprintjs.com matches fingerprintjs.com)
        for fp_domain, api_names in KNOWN_FINGERPRINTING_DOMAINS.items():
            if domain.endswith("." + fp_domain):
                return api_names

        return []

    @staticmethod
    def _infer_tracker_type_from_domain(domain: str, url: str) -> TrackerType:
        """Heuristic tracker type inference from domain or URL patterns."""
        domain = domain.lower().split(":", 1)[0]
        # Link decoration heuristics (utm/gclid/fbclid etc.)
        url_lower = url.lower()
        if any(
            token in url_lower
            for token in ("utm_", "gclid=", "fbclid=", "yclid=", "msclkid=")
        ):
            return TrackerType.LINK_DECORATOR

        for tracker_type, domains in TRACKER_DOMAIN_CATEGORIES.items():
            for known in domains:
                if domain == known or domain.endswith("." + known):
                    return tracker_type

        # Fingerprinter domains are already handled, but keep a guard
        if domain in KNOWN_FINGERPRINTING_DOMAINS:
            return TrackerType.FINGERPRINTER

        return TrackerType.OTHER

    # ------------------------------------------------------------------
    # Extended methods (Requirements 4.1, 4.6, 4.7)
    # ------------------------------------------------------------------

    @staticmethod
    def classify_request_party(request_url: str, page_domain: str) -> str:
        """Classify a request as first-party or third-party relative to *page_domain*.

        Compares the base domain (last two labels) of the request URL with
        the base domain of the page.

        Returns
        -------
        str
            ``"first_party"`` or ``"third_party"``.
        """
        req_domain = TrafficAnalyzer._extract_domain(request_url)
        req_base = _extract_base_domain(req_domain)
        page_base = _extract_base_domain(page_domain)
        if req_base == page_base:
            return "first_party"
        return "third_party"

    async def analyze_extended(
        self,
        requests: list[NetworkRequest],
        cookies: list[CookieRecord] | None = None,
        js_api_calls: list[FingerprintAPICall] | None = None,
        page_domain: str = "",
    ) -> tuple[list[TrackerDetection], SessionSummary]:
        """Extended analysis integrating cookie and JS fingerprinting results.

        Calls the base ``analyze()`` for tracker detection, then enriches the
        ``SessionSummary`` with:
        * first-party / third-party request counts
        * ``CookieAnalysisResult`` from ``CookieAnalyzer``
        * ``FingerprintingSummary`` from JS API call data

        Parameters
        ----------
        requests:
            Captured ``NetworkRequest`` objects.
        cookies:
            Captured ``CookieRecord`` objects (optional).
        js_api_calls:
            Captured ``FingerprintAPICall`` objects (optional).
        page_domain:
            The page's domain used for first-party/third-party classification.

        Returns
        -------
        tuple[list[TrackerDetection], SessionSummary]
            Detections and an enriched session summary.
        """
        t_start = time.monotonic()
        detections, summary = await self.analyze(requests)
        t_after_detect = time.monotonic()

        # --- First-party / third-party request classification ---
        first_party = 0
        third_party = 0
        if page_domain:
            for req in requests:
                party = self.classify_request_party(req.url, page_domain)
                if party == "first_party":
                    first_party += 1
                else:
                    third_party += 1
        summary.first_party_requests = first_party
        summary.third_party_requests = third_party

        # --- Cookie analysis integration ---
        if cookies is not None and page_domain:
            cookie_analyzer = CookieAnalyzer()
            base_result = cookie_analyzer.analyze(cookies, page_domain)
            # Add cookie-sync detection based on request URLs
            sync_events: list = []
            if not _DISABLE_COOKIE_SYNC:
                sync_events = cookie_analyzer.detect_cookie_syncing(requests, cookies)
            cookie_result = CookieAnalysisResult(
                total_cookies=base_result.total_cookies,
                first_party_cookies=base_result.first_party_cookies,
                third_party_cookies=base_result.third_party_cookies,
                tracking_cookies=base_result.tracking_cookies,
                cookie_sync_events=sync_events,
            )
            summary.cookie_analysis = cookie_result

        # --- JS fingerprinting summary ---
        if js_api_calls:
            script_domains: list[str] = []
            seen: set[str] = set()
            for call in js_api_calls:
                domain = self._extract_domain(call.caller_script)
                if domain and domain not in seen:
                    seen.add(domain)
                    script_domains.append(domain)
            summary.fingerprinting_summary = FingerprintingSummary(
                fingerprinting_api_call_count=len(js_api_calls),
                calling_script_domains=script_domains,
            )

        t_after_cookie = time.monotonic()
        summary.detect_ms = (t_after_detect - t_start) * 1000.0
        summary.cookie_fp_ms = (t_after_cookie - t_after_detect) * 1000.0
        summary.analysis_total_ms = (t_after_cookie - t_start) * 1000.0
        if _TIMING_ENABLED:
            logger.info(
                "TrafficAnalyzer.analyze_extended: requests=%d detections=%d "
                "detect_ms=%.1f cookie_fp_ms=%.1f total_ms=%.1f",
                len(requests),
                len(detections),
                summary.detect_ms,
                summary.cookie_fp_ms,
                summary.analysis_total_ms,
            )

        return detections, summary


# ---------------------------------------------------------------------------
# Module-level helper (shared with CookieAnalyzer pattern)
# ---------------------------------------------------------------------------


def _extract_base_domain(domain: str) -> str:
    """Extract the registrable base domain (last two labels).

    For example ``"ads.example.com"`` → ``"example.com"``.
    """
    domain = domain.lstrip(".").lower()
    parts = domain.split(".")
    if len(parts) <= 2:
        return domain
    return ".".join(parts[-2:])
