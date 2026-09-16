"""Cookie analysis module for AgentCloak measurement framework.

Provides first-party/third-party cookie classification, tracking cookie detection
via known tracker domain matching, and cookie syncing detection across tracker domains.

All models use Pydantic v2 BaseModel for JSON round-trip compatibility.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field

from agentcloak.core.models import CookieRecord, NetworkRequest

import hashlib
try:
    import xxhash as _xxhash
except Exception:  # pragma: no cover - fallback when xxhash isn't installed
    _xxhash = None

# ---------------------------------------------------------------------------
# Default known tracker domains (common advertising / analytics trackers)
# ---------------------------------------------------------------------------

DEFAULT_TRACKER_DOMAINS: set[str] = {
    "doubleclick.net",
    "googlesyndication.com",
    "google-analytics.com",
    "googleadservices.com",
    "googletagmanager.com",
    "facebook.net",
    "facebook.com",
    "fbcdn.net",
    "amazon-adsystem.com",
    "criteo.com",
    "criteo.net",
    "outbrain.com",
    "taboola.com",
    "adnxs.com",
    "rubiconproject.com",
    "pubmatic.com",
    "casalemedia.com",
    "openx.net",
    "adsrvr.org",
    "demdex.net",
    "bluekai.com",
    "scorecardresearch.com",
    "quantserve.com",
    "hotjar.com",
    "mouseflow.com",
    "fullstory.com",
    "newrelic.com",
    "nr-data.net",
    "mixpanel.com",
    "segment.io",
    "segment.com",
    "amplitude.com",
    "chartbeat.com",
    "parsely.com",
    "linkedin.com",
    "ads-twitter.com",
    "t.co",
    "snapchat.com",
    "tiktok.com",
    "byteoversea.com",
    "yahoo.com",
    "bing.com",
    "mathtag.com",
    "serving-sys.com",
    "eyeota.net",
    "exelator.com",
    "bidswitch.net",
    "sharethrough.com",
    "indexexchange.com",
}

# Pattern to detect long alphanumeric identifiers (UUID-like, 16+ chars)
_IDENTIFIER_PATTERN = re.compile(r"[a-fA-F0-9\-]{16,}")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CookieSyncEvent(BaseModel):
    """A detected cookie syncing event between two tracker domains."""

    domain_pair: tuple[str, str]
    shared_identifier_hash: str
    sync_method: str  # "url_parameter" | "redirect" | "pixel"
    timestamp: datetime


class CookieAnalysisResult(BaseModel):
    """Aggregated result of cookie analysis for a page session."""

    total_cookies: int
    first_party_cookies: int
    third_party_cookies: int
    tracking_cookies: int
    cookie_sync_events: list[CookieSyncEvent] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _extract_base_domain(domain: str) -> str:
    """Extract the registrable base domain from a cookie/page domain.

    Simple heuristic: strip leading dots, take last two labels.
    For example ``".ads.example.com"`` → ``"example.com"``.
    """
    domain = domain.lstrip(".")
    parts = domain.split(".")
    if len(parts) <= 2:
        return domain.lower()
    return ".".join(parts[-2:]).lower()


def _is_tracker_domain(domain: str, known_trackers: set[str]) -> bool:
    """Check whether *domain* (or any parent) is in the known tracker set."""
    domain = domain.lstrip(".").lower()
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in known_trackers:
            return True
    return False


def _extract_identifiers_from_url(url: str) -> list[str]:
    """Return long alphanumeric strings found in URL query parameters."""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=False)
    except Exception:
        return []

    identifiers: list[str] = []
    for values in params.values():
        for v in values:
            identifiers.extend(_IDENTIFIER_PATTERN.findall(v))
    return identifiers


def _hash_identifier(identifier: str) -> str:
    """Return an xxhash hex digest of *identifier* (fallback to SHA-256)."""
    data = identifier.encode("utf-8")
    if _xxhash is not None:
        return _xxhash.xxh3_128_hexdigest(data)
    return hashlib.sha256(data).hexdigest()


def _infer_sync_method(request: NetworkRequest) -> str:
    """Heuristic to classify the cookie-sync transport method."""
    url_lower = request.url.lower()
    # Pixel-based syncing: image requests or 1×1 pixel endpoints
    if request.request_type in ("image", "img") or any(
        ext in url_lower for ext in (".gif", ".png", ".jpg", "/pixel", "/track")
    ):
        return "pixel"
    # Redirect-based syncing: 3xx responses
    if 300 <= request.response_status < 400:
        return "redirect"
    # Default: URL parameter syncing
    return "url_parameter"


# ---------------------------------------------------------------------------
# CookieAnalyzer
# ---------------------------------------------------------------------------


class CookieAnalyzer:
    """Analyse cookies captured during a browsing session.

    Provides:
    * First-party / third-party classification
    * Tracking cookie detection (known tracker domain matching)
    * Cookie syncing detection (shared identifiers across tracker domains)
    """

    def __init__(
        self,
        known_tracker_domains: set[str] | None = None,
    ) -> None:
        self.known_tracker_domains: set[str] = (
            known_tracker_domains
            if known_tracker_domains is not None
            else DEFAULT_TRACKER_DOMAINS
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        cookies: list[CookieRecord],
        page_domain: str,
    ) -> CookieAnalysisResult:
        """Classify all *cookies* relative to *page_domain* and return a summary.

        Every cookie is classified as exactly first-party or third-party so that
        ``first_party_cookies + third_party_cookies == total_cookies``.
        """
        total = len(cookies)
        third_party = 0
        tracking = 0

        for cookie in cookies:
            is_tp = self.classify_cookie(cookie, page_domain)
            if is_tp:
                third_party += 1
            if _is_tracker_domain(cookie.domain, self.known_tracker_domains):
                tracking += 1

        first_party = total - third_party

        return CookieAnalysisResult(
            total_cookies=total,
            first_party_cookies=first_party,
            third_party_cookies=third_party,
            tracking_cookies=tracking,
            cookie_sync_events=[],
        )

    def detect_cookie_syncing(
        self,
        requests: list[NetworkRequest],
        cookies: list[CookieRecord],
    ) -> list[CookieSyncEvent]:
        """Detect cookie syncing across tracker domains.

        Looks for long alphanumeric strings (UUID-like, 16+ chars) in URL
        query parameters that appear across multiple tracker domains.
        """
        # Map: identifier → list of (tracker_domain, request) pairs
        id_to_domains: dict[str, list[tuple[str, NetworkRequest]]] = {}

        for req in requests:
            req_domain = _extract_base_domain(urlparse(req.url).netloc)
            if not _is_tracker_domain(req_domain, self.known_tracker_domains):
                continue

            identifiers = _extract_identifiers_from_url(req.url)
            for ident in identifiers:
                id_to_domains.setdefault(ident, []).append((req_domain, req))

        events: list[CookieSyncEvent] = []
        seen_pairs: set[tuple[str, str, str]] = set()

        for ident, domain_req_pairs in id_to_domains.items():
            unique_domains = {d for d, _ in domain_req_pairs}
            if len(unique_domains) < 2:
                continue

            sorted_domains = sorted(unique_domains)
            ident_hash = _hash_identifier(ident)

            for i in range(len(sorted_domains)):
                for j in range(i + 1, len(sorted_domains)):
                    pair = (sorted_domains[i], sorted_domains[j])
                    key = (pair[0], pair[1], ident_hash)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)

                    # Pick the earliest request for the timestamp
                    earliest_req = min(
                        (r for d, r in domain_req_pairs if d in pair),
                        key=lambda r: r.timestamp,
                    )

                    events.append(
                        CookieSyncEvent(
                            domain_pair=pair,
                            shared_identifier_hash=ident_hash,
                            sync_method=_infer_sync_method(earliest_req),
                            timestamp=earliest_req.timestamp,
                        )
                    )

        return events

    def classify_cookie(
        self,
        cookie: CookieRecord,
        page_domain: str,
    ) -> bool:
        """Return ``True`` if *cookie* is third-party relative to *page_domain*."""
        cookie_base = _extract_base_domain(cookie.domain)
        page_base = _extract_base_domain(page_domain)
        return cookie_base != page_base
