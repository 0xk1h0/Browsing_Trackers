"""Tracker family / super-category classifier.

Self-contained module — no project-internal dependencies. Pass a host string
to ``classify_tracker_family`` to get a fine-grained family label; pass that
to ``super_category`` to get the 7-class super-category used by RQ1 Table 4.

Coverage:
- 70+ fine-grained tracker families (Google, Meta, Amazon, all major SSPs/
  DSPs, identity-resolution vendors, ACR vendors, measurement vendors).
- Known false-positive domains (CDNs, fonts) returned as ``"false_positive"``
  so callers can exclude them from tracker counts.
- Domain not in any pattern → ``"other"``.

Plus the leakage-class keyword sets used by the per-session aggregator
(``aggregation/six_metrics.py``) to compute M_P, M_T, M_X, M_F:
- PSEUDONYM_KEYWORDS / PSEUDONYM_TOKENS
- CONTEXT_KEYWORDS, BEHAVIOR_KEYWORDS, DEVICE_NETWORK_KEYWORDS
- DIRECT_IDENTIFIER_TOKENS / *_COMPOUND_KEYS

Extracted from the project's `research/common.py` (RQ7 paired-experiment
shared utilities) and the super-category map in
`analysis/run_rq2_category.py`.
"""

from __future__ import annotations

import re
from typing import Iterable

# ---------------------------------------------------------------------------
# Fine-grained tracker families
# ---------------------------------------------------------------------------

TRACKER_FAMILY_PATTERNS: list[tuple[str, str]] = [
    # --- Google ecosystem ---
    ("google_tag_analytics", r"(googletagmanager\.com|google-analytics\.com|analytics\.google\.com"
                             r"|googletagservices\.com|fundingchoicesmessages\.google\.com"
                             r"|adtrafficquality\.google)"),
    ("google_ads_doubleclick", r"(doubleclick\.net|googlesyndication\.com|googleadservices\.com"
                               r"|2mdn\.net|pagead2\.googlesyndication\.com)"),
    # --- Major platforms ---
    ("meta_pixel", r"(facebook\.net|facebook\.com/tr|www\.facebook\.com)"),
    ("linkedin_ads", r"(licdn\.com|linkedin\.com)"),
    ("microsoft_ads_clarity", r"(clarity\.ms|bat\.bing\.com|c\.bing\.com|r\.bing\.com)"),
    ("amazon_ads", r"(amazon-adsystem\.com|fls-na\.amazon\.com|unagi.*\.amazon\.com"
                   r"|amazon\.dev|data\.amazon\.com)"),
    ("yahoo_ads_analytics", r"(analytics\.yahoo\.com|advertising\.yahoo\.com"
                            r"|advertising\.com|yahoo\.com)"),
    # --- Major SSPs / Exchanges ---
    ("xandr_adnxs", r"(adnxs\.com|appnexus\.com|xandr\.com)"),
    ("rubicon_magnite", r"(rubiconproject\.com|rlcdn\.com|magnite\.com)"),
    ("openx", r"(openx\.net|openx\.com|openxcdn\.net)"),
    ("pubmatic", r"(pubmatic\.com)"),
    ("index_exchange", r"(indexww\.com|casalemedia\.com)"),
    ("triplelift", r"(triplelift\.com|3lift\.com)"),
    ("sharethrough", r"(sharethrough\.com)"),
    ("sovrn", r"(lijit\.com|sovrn\.com)"),
    ("criteo", r"(criteo\.com|criteo\.net)"),
    ("thetradedesk", r"(adsrvr\.org|thetradedesk\.com)"),
    ("adform", r"(adform\.net|adform\.com)"),
    # --- Native / Content rec ---
    ("outbrain", r"(outbrain\.com|outbrainimg\.com|zemanta\.com)"),
    ("taboola", r"(taboola\.com)"),
    # --- DMPs / Identity ---
    ("lotame", r"(crwdcntrl\.net|lotame\.com)"),
    ("id5", r"(id5-sync\.com)"),
    ("liveintent", r"(liadm\.com|liveintent\.com)"),
    ("tapad", r"(tapad\.com)"),
    # --- Measurement / Verification ---
    ("comscore", r"(scorecardresearch\.com|comscore\.com)"),
    ("nielsen", r"(imrworldwide\.com|exelator\.com)"),
    ("doubleverify", r"(doubleverify\.com|dvtps\.com|dv\.tech)"),
    ("quantcast", r"(quantserve\.com|quantcast\.com)"),
    ("adobe_audience_manager", r"(demdex\.net|adobedc\.demdex\.net)"),
    ("ias_adsafe", r"(adsafeprotected\.com)"),
    ("chartbeat", r"(chartbeat\.com|chartbeat\.net)"),
    # --- Other DSPs / Ad networks ---
    ("mediamath", r"(mathtag\.com|mediamath\.com)"),
    ("bidswitch", r"(bidswitch\.net|iponweb\.net)"),
    ("smartadserver", r"(smartadserver\.com)"),
    ("yieldmo", r"(yieldmo\.com)"),
    ("turn_amobee", r"(turn\.com|amobee\.com)"),
    ("semasio", r"(semasio\.net)"),
    ("kargo", r"(kargo\.com)"),
    ("loopme", r"(loopme\.me|loopme\.com)"),
    ("samba_tv", r"(samba\.tv)"),
    ("dotomi", r"(dotomi\.com)"),
    ("adroll", r"(adroll\.com)"),
    ("tribalfusion", r"(tribalfusion\.com)"),
    ("media_net", r"(media\.net)"),
    ("simpli_fi", r"(simpli\.fi)"),
    ("rfihub_rocket_fuel", r"(rfihub\.com)"),
    ("connatix", r"(connatix\.com)"),
    ("jwplayer", r"(jwplayer\.com|jwplatform\.com|jwpsrv\.com|jwpltx\.com|jwpcdn\.com)"),
    ("opera_ads", r"(adx\.opera\.com|oa\.opera\.com)"),
    ("cloudflare_analytics", r"(cloudflareinsights\.com)"),
    # --- Consent / Privacy infra (tracked by filter lists) ---
    ("privacy_manager", r"(privacymanager\.io|presage\.io)"),
    # --- Additional sync / identity networks ---
    ("intentiq", r"(intentiq\.com)"),
    ("1rx", r"(1rx\.io)"),
    ("perflib_permutive", r"(perflib\.com)"),
    ("stackadapt", r"(stackadapt\.com)"),
    ("zeotap", r"(zeotap\.com)"),
    ("agkn_neustar", r"(agkn\.com)"),
    ("ispot_tv", r"(ispot\.tv)"),
    ("33across", r"(33across\.com|tynt\.com)"),
    ("gumgum", r"(gumgum\.com)"),
    ("unruly", r"(unrulymedia\.com)"),
    ("teads", r"(teads\.tv)"),
    ("deepintent", r"(deepintent\.com)"),
    ("inmobi", r"(inmobi\.com)"),
    ("rich_audience", r"(richaudience\.com)"),
    ("brandmetrics", r"(brandmetrics\.com)"),
    ("360yield_improve", r"(360yield\.com)"),
    ("freewheel", r"(fwmrm\.net)"),
    ("tremorhub", r"(tremorhub\.com)"),
    ("sportradar", r"(sportradarserving\.com)"),
    ("btloader", r"(btloader\.com)"),
]

# Filter-list false positives — CDNs, fonts, captcha challenges — returned as
# "false_positive" so callers can exclude them from tracker counts.
FALSE_POSITIVE_DOMAINS: set[str] = {
    "fonts.gstatic.com", "fonts.googleapis.com",
    "www.gstatic.com", "csi.gstatic.com",
    "play.google.com",
    "cloudflare.com", "challenges.cloudflare.com",
    "cdn.jsdelivr.net", "cdn.ampproject.org",
}

_FAMILY_REGEX: list[tuple[str, re.Pattern[str]]] = [
    (family, re.compile(pattern)) for family, pattern in TRACKER_FAMILY_PATTERNS
]


# ---------------------------------------------------------------------------
# Super-category map (used by RQ1 Table 4)
# ---------------------------------------------------------------------------

PLATFORM_FIRST_PARTY = {
    "google_tag_analytics", "google_ads_doubleclick",
    "meta_pixel", "linkedin_ads", "microsoft_ads_clarity",
    "amazon_ads", "yahoo_ads_analytics",
}
OPEN_RTB = {
    "xandr_adnxs", "rubicon_magnite", "openx", "pubmatic", "index_exchange",
    "triplelift", "sharethrough", "sovrn", "criteo", "thetradedesk",
    "adform", "mediamath", "bidswitch", "smartadserver", "yieldmo",
    "turn_amobee", "kargo", "loopme", "dotomi", "adroll", "tribalfusion",
    "media_net", "simpli_fi", "rfihub_rocket_fuel", "stackadapt",
    "33across", "gumgum", "unruly", "teads", "deepintent", "inmobi",
    "rich_audience", "360yield_improve", "freewheel", "tremorhub",
    "1rx", "opera_ads", "perflib_permutive",
}
IDENTITY_RESOLUTION = {
    "lotame", "id5", "liveintent", "tapad", "intentiq", "zeotap",
    "agkn_neustar",
}
CROSS_DEVICE_ACR = {
    "samba_tv", "ispot_tv",
}
AUDIENCE_MEASUREMENT = {
    "comscore", "nielsen", "doubleverify", "quantcast", "ias_adsafe",
    "chartbeat", "adobe_audience_manager", "brandmetrics", "btloader",
    "cloudflare_analytics",
}
NATIVE_REC = {"outbrain", "taboola"}

SUPER_CATEGORIES = [
    "platform_first_party_ad",
    "open_rtb_exchange",
    "identity_resolution",
    "cross_device_acr",
    "audience_measurement",
    "native_recommendation",
    "other",
    "false_positive",
]


# ---------------------------------------------------------------------------
# Leakage-class keyword sets (consumed by aggregation/six_metrics.py)
# ---------------------------------------------------------------------------

DIRECT_IDENTIFIER_TOKENS = {
    "email", "phone", "ssn", "passport", "resident",
}
DIRECT_IDENTIFIER_COMPOUND_KEYS = {
    "full_name", "fullname", "first_name", "last_name", "firstname", "lastname",
    "user_email", "user_phone", "user_name", "username",
    "phone_number", "phonenumber", "mobile_number",
    "email_address", "emailaddress",
    "hashed_email", "sha256_email", "md5_email",
}
GEO_CONTEXT_TOKENS = {
    "city_name", "country_name", "region_name", "dest_name",
    "city", "country", "region", "state", "zip", "postal",
}
PSEUDONYM_KEYWORDS = (
    "clientid", "client_id", "sessionid", "session_id", "uuid", "guid",
    "adid", "advertising_id", "device_id", "_ga", "_gid", "fbp", "fbc",
    "gclid", "dclid", "msclkid", "clickid",
)
PSEUDONYM_TOKENS = {"cid", "sid", "uid"}
CONTEXT_KEYWORDS = (
    "url", "uri", "path", "page", "title",
    "ref", "referer", "referrer",
    "query", "search", "term", "utm_",
)
BEHAVIOR_KEYWORDS = (
    "event", "evt", "click", "scroll", "dwell", "duration",
    "engagement", "interaction", "action",
    "conversion", "purchase", "view",
)
DEVICE_NETWORK_KEYWORDS = (
    "useragent", "user_agent", "device", "platform",
    "language", "locale", "timezone",
    "screen", "resolution",
    "country", "region", "city",
)
DEVICE_NETWORK_TOKENS = {"ua", "os", "lang", "tz", "ip", "geo"}

LEAKAGE_WEIGHTS: dict[str, float] = {
    "direct_identifier": 5.0,
    "pseudonymous_identifier": 3.0,
    "context_signal": 1.5,
    "behavior_signal": 2.0,
    "device_network_signal": 1.2,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_hostname(hostname: str) -> str:
    host = (hostname or "").strip().lower()
    if host.endswith("."):
        host = host[:-1]
    return host


def is_host_or_subdomain(hostname: str, target_domain: str) -> bool:
    host = normalize_hostname(hostname)
    target = normalize_hostname(target_domain)
    if not host or not target:
        return False
    return host == target or host.endswith(f".{target}")


def classify_tracker_family(domain: str) -> str:
    """Map a tracker domain to a fine-grained family label.

    Returns:
        "false_positive" — known non-tracker domain (CDN, font, captcha).
        "other"          — domain not matched by any family pattern.
        <family>         — one of the 70+ named families defined above.
    """
    d = normalize_hostname(domain)
    if d in FALSE_POSITIVE_DOMAINS:
        return "false_positive"
    for family, pattern in _FAMILY_REGEX:
        if pattern.search(d):
            return family
    return "other"


def super_category(family: str) -> str:
    """Map a fine-grained family to one of 8 super-categories."""
    if family == "false_positive":
        return "false_positive"
    if family in PLATFORM_FIRST_PARTY:
        return "platform_first_party_ad"
    if family in OPEN_RTB:
        return "open_rtb_exchange"
    if family in IDENTITY_RESOLUTION:
        return "identity_resolution"
    if family in CROSS_DEVICE_ACR:
        return "cross_device_acr"
    if family in AUDIENCE_MEASUREMENT:
        return "audience_measurement"
    if family in NATIVE_REC:
        return "native_recommendation"
    return "other"


def aggregate_tracker_family_counts(domains: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for domain in domains:
        family = classify_tracker_family(domain)
        counts[family] = counts.get(family, 0) + 1
    return counts


__all__ = [
    "TRACKER_FAMILY_PATTERNS", "FALSE_POSITIVE_DOMAINS", "SUPER_CATEGORIES",
    "PLATFORM_FIRST_PARTY", "OPEN_RTB", "IDENTITY_RESOLUTION",
    "CROSS_DEVICE_ACR", "AUDIENCE_MEASUREMENT", "NATIVE_REC",
    "DIRECT_IDENTIFIER_TOKENS", "DIRECT_IDENTIFIER_COMPOUND_KEYS",
    "GEO_CONTEXT_TOKENS", "PSEUDONYM_KEYWORDS", "PSEUDONYM_TOKENS",
    "CONTEXT_KEYWORDS", "BEHAVIOR_KEYWORDS",
    "DEVICE_NETWORK_KEYWORDS", "DEVICE_NETWORK_TOKENS",
    "LEAKAGE_WEIGHTS",
    "classify_tracker_family", "super_category",
    "aggregate_tracker_family_counts",
    "is_host_or_subdomain", "normalize_hostname",
]
