# Browser User Study — Operator README

End-to-end documentation for the human-side user study that complements the
multi-agent privacy measurement (RQ1). Participants drive a fully instrumented
Chromium running on the study server via an HTML5 remote-desktop client; every
HTTP request, JS fingerprinting call, and participant answer is captured per
session and per task.

This document describes the system as deployed; the actual code lives under
[proto/](proto/) and runtime artifacts under [runtime/](runtime/).

This directory is the **anonymized, English-only reproducibility artifact**.
Operator-identifying values (public domain, Cloudflare tunnel UUID, absolute
host paths) are replaced with placeholders or environment-variable hooks.

---

## 1. Study design

| Element                | Value |
|------------------------|-------|
| Total participants     | 20 (P001 – P020) |
| Tasks per participant  | 10 (the same 10 across everyone) |
| Total session-tasks    | 200 |
| Per-task time          | 2-5 minutes (full study approx. 30 min / participant) |
| Task assignment        | **Cyclic Latin square**: each block of 10 participants sees each task in each ordinal position exactly once -> first-order position effects (fatigue, learning, attention drift) are counterbalanced. |

### 1.1 Master task list

Defined in [proto/build_fixed_tasks.py](proto/build_fixed_tasks.py); compiled
into [participant_assignments.json](participant_assignments.json).

| # | Site            | Start URL                                | Question (summary) |
|---|-----------------|------------------------------------------|--------------------|
| 1 | Google Search   | `https://www.google.com/`                | Inception IMDb + Metacritic scores |
| 2 | Google Maps     | `https://www.google.com/maps/`           | Big Bend National Park basic info |
| 3 | Amazon          | `https://www.amazon.com/`                | 1.5 L stainless electric kettle (no purchase) |
| 4 | Apple           | `https://www.apple.com/`                 | Latest MacBook Air wireless-web battery life |
| 5 | Google Flights  | `https://www.google.com/travel/flights/` | LIS -> SIN flight + booking sites |
| 6 | Booking.com     | `https://www.booking.com/`               | LA hotel, Dec 7-11, 4-star or higher |
| 7 | Coursera        | `https://www.coursera.org/`              | Digital Marketing beginner course |
| 8 | GitHub          | `https://github.com/`                    | React latest release version + date |
| 9 | Hugging Face    | `https://huggingface.co/`                | Pro account monthly price + 2 features |
| 10 | ArXiv          | `https://arxiv.org/`                     | GPT-4 Technical Report v3 submission date |

To regenerate: `python proto/build_fixed_tasks.py --n-participants 20`.

---

## 2. System architecture

```
participant browser (anywhere)
   |  HTTPS -- https://<study-host>/         (operator's public hostname)
   v
Cloudflare edge
   |  Named tunnel <tunnel-name> / <tunnel-uuid>
   v
cloudflared (server-side, foreground or systemd)
   |  http://localhost:8888
   v
proto/web_app.py            (aiohttp; multi-participant router + HTTP/WS reverse proxy)
   |  /, /briefing, /login, /dashboard, /task/<n>/iframe, /task/<n>/complete
   |  /p/<pid>/...           <- reverse proxies to backend xpra
   v
proto/spawn_session.py      (per-PID session orchestrator with file lock)
   |
   |-- mitmdump              (per-session; runs the capture addon + stealth addon)
   |     -> AGENTCLOAK_CAPTURE_FILE      = capture.jsonl
   |     -> AGENTCLOAK_FP_CAPTURE_FILE   = js_telemetry.jsonl
   |     -> AGENTCLOAK_STEALTH           = 1
   |
   |-- xpra :NNN             (HTML5 client on 127.0.0.1:14600+N)
   |     -> JPEG / PNG only encoding (no stateful VP8/h264)
   |     -> Xvfb framebuffer resized to participant viewport
   |     -> ws-auth = password from $USER_STUDY_XPRA_PASSWORD_FILE (default: ~/.config/study/xpra_password)
   |
   |-- chromium_launch.sh    (per-session bash, generated)
         -> clears stale ~/.config/chromium/Singleton*
         -> exec chromium with --proxy-server, --ozone-platform=x11, start URL
```

### 2.1 Per-PID isolation

Every active participant gets:

- a unique X display (:110, :111, :112, …)
- a unique xpra TCP port (14600, 14601, 14602, …)
- a unique mitmdump port (random free port)
- a fresh Chromium profile (`runtime/sessions/<PID>/<task>_<ts>/chrome_profile/`)
- a fresh capture file (`capture.jsonl`)
- a fresh JS telemetry file (`js_telemetry.jsonl`)

Allocation is serialized through a file lock (`runtime/spawn.lock`) to prevent
two concurrent spawns from racing on the same display/port.

---

## 3. Key files

```
user_study_en/
|-- README.md                            (this file)
|-- participant_assignments.json         Master N x 10 task assignment + Latin square
|-- verify_reproducibility.py            Reproducibility validation harness
|-- proto/
|   |-- web_app.py                       aiohttp app: routing, briefing, dashboard, proxy
|   |-- spawn_session.py                 spawn_session start|stop|list  (per-PID isolation)
|   |-- stealth_addon.py                 puppeteer-stealth-equivalent JS injection
|   |-- build_fixed_tasks.py             Regenerate participant_assignments.json
|   |-- inspect_capture.py               Quick summary of a capture.jsonl
|   |-- smoke_traffic_check.sh           End-to-end single-task smoke
|   |-- multi_task_smoke.sh              Same PID, 5 different tasks
|   `-- multi_pid_stress.sh              3 concurrent PIDs at once (isolation check)
|-- client/                              Standalone (no-server) participant client
|   |-- study_client.py
|   |-- start_linux.sh / start_mac.command / start_windows.bat
|   `-- README.txt
|-- xpra-html5/                          xpra HTML5 client  (clone separately - see below)
`-- runtime/
    |-- sessions/<PID>/                  one dir per active or completed participant
    |   |-- state.json                    active session metadata (rm on stop)
    |   `-- <task_safe>_<ts>/
    |       |-- capture.jsonl             <- agent-comparable HTTP trace
    |       |-- js_telemetry.jsonl        <- Canvas/WebGL/Audio + pre-encryption XHR
    |       |-- answer.json               <- participant answer + timestamp
    |       |-- stopped.json              <- end-of-task metadata
    |       |-- chromium_launch.sh        per-session generated launcher
    |       |-- chrome_profile/           per-session Chromium profile
    |       `-- logs/{xpra,mitmdump}.log  diagnostic logs
    |-- backup/                          Sanitized sample sessions for reviewers
    `-- spawn.lock                        file lock for race-free spawn
```

### 3.1 Shared dependency

The mitmproxy capture addon is the same file the agent measurement pipeline
uses, so schema parity is enforced by reuse. This artifact ships it at
`pipeline/capture/mitm_addon.py` (relative to `artifact/`).

`proto/spawn_session.py` resolves the addon in this order:

1. `USER_STUDY_ADDON_PATH` environment variable (explicit override).
2. `../../pipeline/capture/mitm_addon.py` relative to the user-study folder,
   which is where this artifact ships it.
3. `$AGENTCLOAK_ROOT/measurement/mitm_capture_addon.py`, the development-tree
   location, if that variable is set.

Likewise `XPRA_BIN`, `MITMDUMP_BIN`, `CHROMIUM_CANDIDATES`, and
`PASSWORD_FILE` are all discovered via `$PATH` and a small set of
`USER_STUDY_*_BIN` overrides so the artifact is host-independent.

### 3.2 External dependency: xpra-html5

The xpra HTML5 client is not vendored. Operators clone it once into the
study root before starting `web_app.py`:

```bash
git clone https://github.com/Xpra-org/xpra-html5.git
```

`proto/spawn_session.py` expects `xpra-html5/html5/index.html` to exist
and prints a clear startup error otherwise.

---

## 4. Public deployment (operator-side)

The user study is exposed publicly via Cloudflare Tunnel. Operator-specific
values are placeholders here — replace with your own before deploying.

| Item                      | Value |
|---------------------------|-------|
| Public URL                | `https://<study-host>/` |
| Named tunnel              | `<tunnel-name>` (UUID `<tunnel-uuid>`) |
| Credentials file          | `~/.cloudflared/<tunnel-uuid>.json` |
| Ingress config            | `~/.cloudflared/config.yml`  ( `<study-host>` -> `http://localhost:8888` ) |
| Tunnel runner             | `cloudflared tunnel run <tunnel-name>` |
| Persistent xpra password  | `$USER_STUDY_XPRA_PASSWORD_FILE`  (default `~/.config/study/xpra_password`, mode 600, generated with `openssl rand -hex 12`) |

### 4.1 Start / stop the operator side

```bash
# 1) web_app (port 8888)
cd user_study_en/proto
python web_app.py --port 8888

# 2) cloudflared tunnel (already mapped to <study-host>)
cloudflared tunnel run <tunnel-name>

# (recommended) make cloudflared a service so it survives terminal close
sudo cloudflared service install
```

To stop everything:

```bash
pkill -f "web_app.py"
pkill -f "cloudflared.*tunnel run"
# any orphan sessions:
python proto/spawn_session.py list      # see what is still up
for p in $(ls runtime/sessions); do
    python proto/spawn_session.py stop --pid "$p"
done
```

---

## 5. Participant flow

1. Participant receives **PID** (P001-P022) from the operator (or one is
   auto-assigned at briefing-ack — see `proto/web_app.py:assign_new_participant`).
2. Visits the study URL.
3. **Briefing page** — overview, the 10 tasks, copy/paste note. Checks "I
   have read and understood the above" -> **Next**.
4. **Login / auto-assign** — a participant ID and 6-digit survey code are
   minted automatically; participants never type a PID.
5. **Dashboard** — sees the 10 tasks in their Latin-square order. Clicks
   **Start** on the next pending task.
6. **Task page** — left side is a full-bleed iframe with the live Chromium;
   right side has the question and an answer textarea. Cold start
   approx. 5-10 s with a spinner.
7. Performs the task naturally, types the answer, clicks
   "Done -- next task". Session is torn down, capture is finalized,
   dashboard returns showing the task as completed.
8. Repeat until all 10 are done.

Copy/paste inside the iframe is preserved: the page hooks xpra-html5's
internal clipboard buffer so a fresh `Ctrl+C` inside the remote browser
beats the stale OS clipboard.

---

## 6. Monitoring during the study

| Command                                                                | What it shows |
|------------------------------------------------------------------------|---------------|
| `python proto/spawn_session.py list`                                   | All active sessions: PID, display, ports |
| `curl http://127.0.0.1:8888/admin/status \| jq .`                      | JSON view of active sessions |
| `ls runtime/sessions/`                                                 | Every PID that has ever spawned a session |
| `find runtime/sessions -name answer.json \| wc -l`                     | Total answers collected so far |
| `find runtime/sessions -name capture.jsonl -exec wc -l {} +`           | Lines (approx. HTTP requests) per task |
| `tail -f runtime/sessions/<PID>/<task>/logs/xpra.log`                  | Live xpra log of a session |
| `tail -f runtime/sessions/<PID>/<task>/logs/mitmdump.log`              | Live mitmproxy log |

To clear everything for a fresh start (e.g., between pilot and real study):

```bash
for p in $(ls runtime/sessions); do
    python proto/spawn_session.py stop --pid "$p" || true
done
rm -rf runtime/sessions/* runtime/spawn.lock
rm -f ~/.config/chromium/Singleton*
```

---

## 7. Data schema

### 7.1 `capture.jsonl` — one JSON object per HTTP/S flow

Same schema as the multi-agent measurement so analysis pipelines can be reused.

```json
{
  "url": "https://www.amazon.com/",
  "method": "GET",
  "hostname": "www.amazon.com",
  "timestamp": "2026-05-18T06:13:55.906516+00:00",
  "response_status": 202,
  "response_size": 2012,
  "response_headers": "{...}",
  "headers": "{...}",
  "set_cookies": "[...]",
  "content_type": "text/html; charset=UTF-8",
  "response_body_sha256": "fe6ab38c...",
  "response_body_snippet": null
}
```

### 7.2 `js_telemetry.jsonl` — JS fingerprinting beacons

Each line is one event emitted by `hook.js` (Canvas / WebGL / AudioContext API
calls, or pre-encryption XHR/Fetch bodies). Independent of `capture.jsonl`.

### 7.3 `answer.json` — participant's text answer

```json
{
  "participant_id": "P001",
  "task_id": "Google Search--28",
  "order": 1,
  "question": "On Google Search, find the IMDb score and the Metacritic score of the movie \"Inception\".",
  "answer": "a) IMDb score: 8.8\nb) Metacritic score: 74",
  "submitted_at": "2026-05-18T13:25:12+0000"
}
```

### 7.4 `stopped.json` — end-of-task metadata

```json
{
  "pid": "P001",
  "task_id": "Google Search--28",
  "n_requests_captured": 234,
  "stopped_at": "2026-05-18T13:26:00+0000"
}
```

### 7.5 Latin square metadata (in participant_assignments.json)

Each assignment carries:
- `latin_shift` (0-9)
- `task_order_master_indices` (1-indexed) — which master tasks appear at orders 1..10

These enable position-controlled analyses.

---

## 8. Sanitization notes (reproducibility artifact)

**Operator identity.** The released session bundles are scrubbed of the study
operator's account and of the study network's public address. The path sanitizer rewrites home directories to
`<study-user-home>` and `<study-root>`, and
`../../scripts/scrub_user_study_archive.py` rewrites the remote-desktop
server's own `uid=... (name), gid=... (name)` line in each `xpra.log` to
`uid=<uid> (<operator>), gid=<gid> (<operator>)`. The same script replaces the
study network's public address with `<study-network-ip>` wherever trackers
echoed it back into the captures, which happens in `cip=` request parameters
and in `x-forwarded-for`, `x-proxy-origin`, `x-q-stat` and `location` response
headers across 127 files. Third-party and loopback addresses are left alone,
because they are part of what was measured. Record counts are unchanged by the
rewrite: 164,821 capture lines before and after, 221 session directories, 220
answers.

**Participant identity.** Participants appear only as `P001` through `P022`.
No name, email, survey identifier, or demographic field is released, and the
study network's own public address is replaced with `<study-network-ip>`.

Be precise about what the captures do still contain, because they are the
measurement itself rather than incidental metadata:

- **No cookie headers.** Request `Cookie` and `Authorization` headers and
  response `Set-Cookie` headers are dropped by `scripts/sanitize_sessions.py`
  before packing. Verified on the released archive: 0 of 164,821 captured
  requests carry any of them.
- **Identifiers in URLs are retained by design.** Tracking identifiers that
  travel as query parameters, such as `_ga` or `gclid` values, remain in the
  captured URLs. Removing them would remove the phenomenon the paper measures.
  In the Tier-1 tables these are reduced to per-campaign salted hashes, and the
  salt is not released.
- **Task-derived search text is retained.** URLs contain the terms typed during
  a task, for example `react` or `inception imdb rating`. The ten tasks were
  assigned and identical across participants, so this text is task content
  rather than personal expression. No free-form survey or comment field is
  released.

**How this is enforced.** `../../scripts/anonymize_check.sh` is the release
gate. It scans the tree and, since it previously skipped compressed files, now
also scans inside both the `.jsonl.gz` per-session archives and this `.tar.xz`
archive. It exits non-zero on any hit, so a regression is caught rather than
shipped.

This directory is intentionally redacted for public release:

| Item                                | Treatment |
|-------------------------------------|-----------|
| Real participant identities         | Only the synthetic `P0NN` PIDs are kept. No emails, names, IPs, or device fingerprints. |
| Operator's public hostname / domain | Replaced with `<study-host>` in this README. The code itself is host-agnostic (reads `Host` header). |
| Cloudflare tunnel UUID / name       | Replaced with `<tunnel-uuid>` / `<tunnel-name>` placeholders. |
| Operator home paths                 | All absolute home-directory constants in `proto/spawn_session.py` and the `proto/*.sh` smoke scripts have been replaced with `$PATH` discovery and `USER_STUDY_*` env-var overrides. |
| `runtime/backup/` answers           | Original participant text translated to representative English equivalents. The `capture.jsonl` and `js_telemetry.jsonl` files are preserved verbatim because they are exactly the measurement data the artifact is meant to demonstrate. |
| `runtime/backup/` telemetry         | `js_telemetry.jsonl` and `capture.jsonl` contain raw HTTP / JS-fingerprint events recorded from real third-party sites (Amazon, Coursera, Booking, ...). Some payloads contain non-English text returned by those sites' own localization — Korean course titles on Coursera, Korean product names on Amazon, etc. This is **third-party site content**, not operator or participant prose, and is left as captured so the dataset accurately reflects what the measurement pipeline observes. |
| Stealth addon                       | Unchanged — it injects no participant-identifying values; only generic Chrome-like overrides. |

---

## 9. Reproducibility

Install the runtime dependencies, then run the harness:

```bash
pip install -r requirements.txt
python verify_reproducibility.py
```

It verifies:

1. `proto/build_fixed_tasks.py` regenerates `participant_assignments.json`
   bit-for-bit.
2. The Latin-square assignment is bijective (every task appears in every
   ordinal position exactly once per block of 10 participants).
3. Every required source file is syntactically valid Python / JSON.
4. No Korean (Hangul) text remains in any tracked source file.
5. No operator-identifying paths or domains leak into the artifact.

Exit code 0 means all checks pass.

---

## 10. Known limitations / operational notes

| Item | Status |
|------|--------|
| **AWS WAF on Amazon** | The Xvfb fingerprint occasionally triggers AWS WAF challenge interstitials. Participants wait or click through; the full page subsequently loads normally. |
| **Cold start latency** | 5-13 s on the first task per session (xpra + Chromium boot). The task page shows a loading spinner with a "First load may take 5-10 seconds" note. |
| **`--no-sandbox`** | Required because Ubuntu 24.04 disables unprivileged user namespaces via AppArmor. Acceptable here because (i) all TLS goes through mitmproxy, (ii) Chromium is ephemeral per session. For a long-running participant-facing deployment, replace with a proper AppArmor profile. |
| **Stealth JS asymmetry** | Stealth is enabled here but was *off* in the published agent measurements. Pilot human results are conservative-lower-bound vs agents; for the final RQ1 comparison, agents should be re-run with stealth on, or this is documented as a methodology caveat. |

---

## 11. Quick troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `502 Bad Gateway` on `/task/<n>/iframe` | Backend xpra for this PID died but `state.json` still points at the dead port | `is_session_alive` in `web_app.py` re-spawns on next visit; if stuck, `spawn_session.py stop --pid <P>` then refresh |
| Chromium opens NTP instead of start URL | Earlier bug — bash interpreted `;` in `--accept-lang` as command separator -> URL dropped. Fixed by quoting the value. |
| "Opening in existing browser session." then Chromium exits | Stale `~/.config/chromium/SingletonLock`. Launcher `rm -f`s these on each spawn; if it persists, run `rm -f ~/.config/chromium/Singleton*` manually. |
| Concurrent spawns collide on display | `runtime/spawn.lock` flock serializes spawn. If a stale lock survives a crash, `rm -f runtime/spawn.lock`. |
| `Chrome for Testing v145 is only for automated testing` banner | The artifact deliberately uses Playwright's older bundle (chromium-1161, Chromium 134 stable) instead of the CfT 145 bundle. If `spawn_session.CHROMIUM_CANDIDATES` is reordered, the banner returns. |

---

## 12. Pilot -> real study handoff

When pilot dry-run is done and the real study begins:

1. **Clear pilot data** (see section 6).
2. **Verify Latin square** mapping still matches the participant assignment
   sheet given out to participants — if anyone is shifted (e.g. P010 <-> P011),
   regenerate with `build_fixed_tasks.py` and redistribute.
3. **Pin cloudflared as a service** so the tunnel survives terminal disconnects.
4. **Pre-warm one session** before the first real participant arrives:
   `python proto/spawn_session.py start --pid P000_WARMUP --task-id warmup --start-url https://www.example.com/` then stop. This loads the Chromium binaries into the OS file cache so the first real spawn is faster.
5. **Snapshot Cloudflare zone DNS** so the named tunnel CNAME isn't accidentally
   edited mid-study.
