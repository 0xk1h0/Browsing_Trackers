# Ethics

Full IRB language is held out until camera-ready.

## Human study (RQ1)

- 22 adult participants, informed written consent, withdrawable.
- Compensation at prevailing local hourly rate.
- Fresh Chromium profile per task; controlled lab environment.
- Cookie *values* are stored only as per-campaign salted SHA-256 hashes; the
  salt is never released.
- Tasks are search / information-retrieval only. No accounts, no transactions.

## Browser-agent runs (RQ2, RQ3)

- No agent credentials. Sessions that failed a bot challenge are recorded
  with `success: false` and counted in the denominator.
- Respected `robots.txt` for non-search-engine destinations; one concurrent
  session per agent.

## Tracker filter lists

March 2026 snapshot of seven public lists; each shipped under its upstream
license (`pipeline/classifier/tracker_lists/LICENSES.md`).

## Released data

| Class | Released? |
|---|---|
| Per-session aggregates (cookie hashes only) | yes |
| Per-request log (cookie *names*, no values) | on request |
| Raw mitmproxy flows, participant artefacts, cookie values | no (IRB) |
