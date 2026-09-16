#!/usr/bin/env python3
"""In-place anonymizer for ``runtime/sessions/``.

Treats the live-study dataset under ``runtime/sessions/`` as input and rewrites
every file that could carry operator- or participant-identifying content. After
this script runs the directory is safe for public release alongside the code
artifact.

Per-PID actions (applied to every ``runtime/sessions/<PID>/``):

  ``survey_code.txt``          DELETED. Six-digit codes link the measurement
                                session to the participant's external survey
                                response — they are the most direct join key
                                to participant identity and have no value in
                                the public artifact.
  ``<task>/chrome_profile/``   DELETED. SQLite cookie / login / history /
                                autofill databases that the operator's
                                Chromium accumulated while serving the task.
                                Cookies for ``.amazon.com/session-token`` and
                                similar are operator-session credentials; the
                                only safe sanitization is removal.
  ``<task>/answer.json``       REWRITTEN.
                                * ``question`` replaced with the canonical
                                  English text from participant_assignments
                                  (by task_id).
                                * ``answer`` replaced with the structured
                                  ``answer_template`` (each ``a) Field:``
                                  prompt preserved) with the substantive
                                  value redacted to ``[REDACTED]``. This
                                  keeps the schema and the form structure
                                  visible to reviewers without leaking
                                  free-text participant prose.
                                * ``submitted_at`` normalized to UTC.
  ``<task>/chromium_launch.sh`` `/home/<user>/` → `<study-user-home>/`,
                                 the long operator filesystem path inside
                                 ``user_study/...`` → ``<study-root>/...``.
  ``<task>/stopped.json``      Same path rewrite + timezone normalization.
                                 PIDs and port numbers are not PII and are
                                 kept verbatim.
  ``<task>/logs/*.log``        Same path rewrite. Any incidental Korean is
                                 stripped to ``[REDACTED-non-ASCII]``.
  ``<task>/capture.jsonl``     Per line:
                                 * drop ``Cookie`` and ``Authorization``
                                   request headers (operator credentials);
                                 * drop ``Set-Cookie``/``Set-Cookie2`` from
                                   response_headers; clear ``set_cookies``;
                                 * normalize the ``accept-language``
                                   header from ``ko-KR,ko;q=0.9...`` to
                                   ``en-US,en;q=0.9`` so every request stops
                                   advertising operator geolocation;
                                 * keep url, method, hostname, status, size,
                                   timestamp, content_type, sha256 — these
                                   are the analytically necessary fields.
  ``<task>/js_telemetry.jsonl`` Preserved verbatim. Records are JS-side
                                 fingerprint events emitted by ``hook.js``
                                 (Canvas / WebGL / AudioContext / XHR pre-
                                 encryption bodies). Any non-English text
                                 inside is third-party site content captured
                                 from sites that localized by IP (Coursera,
                                 Booking). This is the measurement payload
                                 and is documented as such in README §8.

Usage:
    python scripts/sanitize_sessions.py [--check-only]

With ``--check-only`` no files are written; the script prints what would
change and returns non-zero if any drift exists.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


STUDY_DIR = Path(__file__).resolve().parent.parent
SESSIONS_DIR = STUDY_DIR / "runtime" / "sessions"
ASSIGNMENTS = STUDY_DIR / "participant_assignments.json"

HOST_PATH_FULL = re.compile(
    r"/home/[^/]+/project/agent/experiments/rq7_agent_tracker_measurement/user_study(?:_en)?"
)
HOST_PATH_HOME = re.compile(r"/home/[^/\s\"]+")
HOST_PATH_HOME_LITERAL = re.compile(r"/home/<user>")  # any leftover specific path
HANGUL = re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF]+")
# Headers we drop entirely from request side (lower-case match).
DROP_REQ_HEADERS = {"cookie", "authorization", "proxy-authorization"}
# Headers we drop from response side (lower-case match).
DROP_RESP_HEADERS = {"set-cookie", "set-cookie2"}
NEUTRAL_ACCEPT_LANG = "en-US,en;q=0.9"


# --- helpers --------------------------------------------------------------

def load_master_questions() -> tuple[dict[str, str], dict[str, str]]:
    """Return (task_id -> canonical English question,
                task_id -> canonical answer template)."""
    data = json.loads(ASSIGNMENTS.read_text())
    qs, tpl = {}, {}
    for t in data["tasks_master"]:
        qs[t["task_id"]] = t["question"]
        tpl[t["task_id"]] = t.get("answer_template", "")
    return qs, tpl


def redact_answer(template: str) -> str:
    """Convert ``a) Field:`` prompt lines into ``a) Field: [REDACTED]``.

    Lines without a colon are left as-is. Empty template falls back to a
    single-line redaction marker so the schema still has the field."""
    if not template.strip():
        return "[REDACTED]"
    out_lines = []
    for line in template.splitlines():
        if ":" in line:
            head = line.split(":", 1)[0]
            out_lines.append(f"{head}: [REDACTED]")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def normalize_ts(ts: str) -> str:
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return ts
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def rewrite_paths(text: str) -> str:
    text = HOST_PATH_FULL.sub("<study-root>/user_study_en", text)
    text = HOST_PATH_HOME.sub("<study-user-home>", text)
    return text


def strip_hangul(text: str) -> str:
    return HANGUL.sub("[REDACTED-non-ASCII]", text)


# --- per-file actions -----------------------------------------------------

def sanitize_answer_json(path: Path, eng_q: dict[str, str], eng_tpl: dict[str, str]) -> dict:
    src = json.loads(path.read_text(encoding="utf-8"))
    tid = src.get("task_id")
    return {
        "participant_id": src.get("participant_id"),
        "task_id": tid,
        "order": src.get("order"),
        "question": eng_q.get(tid, src.get("question", "")),
        "answer": redact_answer(eng_tpl.get(tid, "")),
        "submitted_at": normalize_ts(src.get("submitted_at", "")),
    }


def sanitize_stopped_json(path: Path) -> dict:
    src = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for k, v in src.items():
        if isinstance(v, str):
            v2 = rewrite_paths(v)
            if k in ("started_at", "stopped_at"):
                v2 = normalize_ts(v2)
            out[k] = v2
        else:
            out[k] = v
    return out


ACCEPT_LANG_LITERAL = re.compile(
    r"--accept-lang='[^']*ko-KR[^']*'"
)


def sanitize_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = rewrite_paths(text)
    # Old generated chromium_launch.sh wrappers were emitted with
    # --accept-lang='ko-KR,...' before the source template was fixed. Rewrite
    # the whole flag value so the launcher reflects the artifact's English
    # default (and so the literal ko-KR string stops geolocating the operator).
    text = ACCEPT_LANG_LITERAL.sub("--accept-lang='en-US,en;q=0.9'", text)
    return strip_hangul(text)


def sanitize_log_bytes(path: Path) -> str:
    # Logs may have noisy non-utf8 bytes. Read tolerantly.
    text = path.read_text(encoding="utf-8", errors="replace")
    return strip_hangul(rewrite_paths(text))


def sanitize_capture_record(d: dict) -> dict:
    # Request headers
    h = d.get("headers")
    if isinstance(h, dict):
        clean = {}
        for k, v in h.items():
            kl = k.lower()
            if kl in DROP_REQ_HEADERS:
                continue
            if kl == "accept-language":
                clean[k] = NEUTRAL_ACCEPT_LANG
            else:
                clean[k] = v
        d["headers"] = clean
    # Response headers
    rh = d.get("response_headers")
    if isinstance(rh, dict):
        d["response_headers"] = {
            k: v for k, v in rh.items() if k.lower() not in DROP_RESP_HEADERS
        }
    # set_cookies field (operator session tokens served by site)
    if "set_cookies" in d:
        d["set_cookies"] = []
    # Body snippet — strip if it contains Korean (site-localized content
    # that geolocates the operator). The sha256 is already an opaque digest
    # so analysis pipelines that need body identity still work.
    bs = d.get("response_body_snippet")
    if isinstance(bs, str) and HANGUL.search(bs):
        d["response_body_snippet"] = None
    return d


def sanitize_capture_jsonl(path: Path) -> str:
    out_lines = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            d = sanitize_capture_record(d)
            out_lines.append(json.dumps(d, ensure_ascii=False))
    return "\n".join(out_lines) + ("\n" if out_lines else "")


# --- runner ---------------------------------------------------------------

def run(check_only: bool) -> int:
    if not SESSIONS_DIR.is_dir():
        print(f"[skip] {SESSIONS_DIR} not present")
        return 0
    eng_q, eng_tpl = load_master_questions()

    n_changed = 0
    n_files = 0

    for pid_dir in sorted(SESSIONS_DIR.iterdir()):
        if not pid_dir.is_dir():
            continue

        # 1) survey_code.txt — DELETE
        sc = pid_dir / "survey_code.txt"
        if sc.is_file():
            n_files += 1
            n_changed += 1
            if check_only:
                print(f"[delete] {sc.relative_to(STUDY_DIR)}")
            else:
                sc.unlink()

        for task_dir in sorted(pid_dir.iterdir()):
            if not task_dir.is_dir():
                continue

            # 2) chrome_profile/ — DELETE TREE
            cp = task_dir / "chrome_profile"
            if cp.is_dir():
                n_files += 1
                n_changed += 1
                if check_only:
                    print(f"[delete-tree] {cp.relative_to(STUDY_DIR)}")
                else:
                    shutil.rmtree(cp)

            # 3) answer.json — REWRITE
            aj = task_dir / "answer.json"
            if aj.is_file():
                n_files += 1
                new = sanitize_answer_json(aj, eng_q, eng_tpl)
                old = json.loads(aj.read_text(encoding="utf-8"))
                if new != old:
                    n_changed += 1
                    if check_only:
                        print(f"[rewrite] {aj.relative_to(STUDY_DIR)}")
                    else:
                        aj.write_text(json.dumps(new, indent=2, ensure_ascii=True) + "\n")

            # 4) chromium_launch.sh — REWRITE TEXT
            cl = task_dir / "chromium_launch.sh"
            if cl.is_file():
                n_files += 1
                new = sanitize_text(cl)
                old = cl.read_text(encoding="utf-8")
                if new != old:
                    n_changed += 1
                    if check_only:
                        print(f"[rewrite] {cl.relative_to(STUDY_DIR)}")
                    else:
                        cl.write_text(new)

            # 5) stopped.json — REWRITE
            sj = task_dir / "stopped.json"
            if sj.is_file():
                n_files += 1
                new = sanitize_stopped_json(sj)
                old = json.loads(sj.read_text(encoding="utf-8"))
                if new != old:
                    n_changed += 1
                    if check_only:
                        print(f"[rewrite] {sj.relative_to(STUDY_DIR)}")
                    else:
                        sj.write_text(json.dumps(new, indent=2) + "\n")

            # 6) logs/*.log — REWRITE TEXT
            logs = task_dir / "logs"
            if logs.is_dir():
                for log in logs.glob("*.log"):
                    n_files += 1
                    new = sanitize_log_bytes(log)
                    old = log.read_text(encoding="utf-8", errors="replace")
                    if new != old:
                        n_changed += 1
                        if check_only:
                            print(f"[rewrite] {log.relative_to(STUDY_DIR)}")
                        else:
                            log.write_text(new, encoding="utf-8")

            # 7) capture.jsonl — REWRITE STREAM
            cj = task_dir / "capture.jsonl"
            if cj.is_file():
                n_files += 1
                new = sanitize_capture_jsonl(cj)
                # cheap drift test: compare hash of new vs old
                import hashlib
                old_h = hashlib.sha1(cj.read_bytes()).hexdigest()
                new_h = hashlib.sha1(new.encode("utf-8")).hexdigest()
                if old_h != new_h:
                    n_changed += 1
                    if check_only:
                        print(f"[rewrite] {cj.relative_to(STUDY_DIR)}")
                    else:
                        cj.write_text(new, encoding="utf-8")

    print()
    print(f"{n_changed}/{n_files} file(s) {'would change' if check_only else 'updated'}")
    return 1 if (check_only and n_changed) else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    sys.exit(run(args.check_only))


if __name__ == "__main__":
    main()
