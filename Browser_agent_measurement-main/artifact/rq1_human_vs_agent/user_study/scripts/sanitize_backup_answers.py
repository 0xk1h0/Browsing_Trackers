#!/usr/bin/env python3
"""Anonymize + translate-to-English the answer.json files under runtime/backup/.

Original participant answers were submitted in Korean. For the public
reproducibility artifact we replace them with representative English
equivalents:

  - The Korean question text is replaced with the canonical English question
    looked up from participant_assignments.json by task_id.
  - The free-text answer is replaced with a representative English answer that
    preserves the structure of the original (a) / b) / c) ... template) and
    the substantive numeric / named-entity content where the participant
    actually answered the question.
  - The submitted_at timezone is normalized from +0900 (operator timezone) to
    +0000 UTC, computed deterministically from the source timestamp.

The original capture.jsonl, js_telemetry.jsonl, stopped.json, logs/, and
chrome_profile/ files are left untouched: they contain no Korean and no
participant-identifying free text, only HTTP traffic and process metadata.

Usage:
    python scripts/sanitize_backup_answers.py [--check-only]

With --check-only the script does NOT write; it just reports what it would
change. Exit code 0 = no change needed / sanitized; non-zero = mismatch.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


STUDY_DIR = Path(__file__).resolve().parent.parent
BACKUP_DIR = STUDY_DIR / "runtime" / "backup"
ASSIGNMENTS = STUDY_DIR / "participant_assignments.json"


# Representative English answers per (participant_id, task_id). Numeric and
# brand content is preserved from the participant's substantive response so
# the data remain analytically meaningful; Korean prose is replaced with a
# direct English translation.
REAL_ANSWERS: dict[tuple[str, str], str] = {
    # P001 — real session
    ("P001", "Google Search--28"): "a) IMDb score: 8.8/10\nb) Metacritic score: 74",
    ("P001", "Google Map--35"): (
        "a) Place type: National Park\n"
        "b) Rating: 4.8\n"
        "c) Address or short description: Big Bend, TX, USA. 801,163-acre wilderness "
        "of canyons, deserts, and mountains; diverse wildlife and outdoor recreation."
    ),
    ("P001", "Amazon--32"): (
        "a) Product name: Amazon Basics Stainless Steel Electric Kettle for Tea and Coffee, "
        "BPA Free, Fast Boil, Auto Shut-Off, Boil-Dry Protection, 1.7 L, 1500 W, Black and Silver\n"
        "b) Price: USD 27.50\n"
        "c) Rating: 4.5"
    ),
    ("P001", "Apple--29"): "a) Estimated battery life (wireless web): 18 hours",
    ("P001", "Google Flights--34"): (
        "a) Chosen flight (airline / time): Etihad Airways / 12/07 08:15 AM, 12/11 07:25 PM\n"
        "b) Booking websites (>=1): Etihad Airways, Mytrip, Expedia"
    ),
    ("P001", "Booking--3"): (
        "a) Hotel name: Four Seasons Hotel Los Angeles at Beverly Hills\n"
        "b) Star rating: 5\n"
        "c) Rating or review score: 9.3"
    ),
    ("P001", "Coursera--21"): (
        "a) Course title: Foundations of Digital Marketing and E-commerce\n"
        "b) Expected duration: 11 hours\n"
        "c) Offering institution: Google\n"
        "d) Main learning outcomes: Define digital marketing and e-commerce; explain the "
        "marketing funnel; describe entry-level digital-marketing and e-commerce roles; "
        "understand the elements and goals of a digital-marketing and e-commerce strategy."
    ),
    ("P001", "GitHub--35"): (
        "a) Latest release version: 19.2.6\n"
        "b) Publish date: May 6, 2026"
    ),
    ("P001", "Huggingface--14"): (
        "a) Monthly price: USD 9\n"
        "b) Main features (>=2): 10x private storage capacity, 2x public storage capacity"
    ),
    ("P001", "ArXiv--17"): "a) v3 submission date: March 27, 2023",

    # P002 — real session
    ("P002", "Google Search--28"): "a) IMDb score: 8.8\nb) Metacritic score: Generally Favorable 74, Universal Acclaim 8.8",
    ("P002", "Google Map--35"): (
        "a) Place type: park\n"
        "b) Rating: (not recorded)\n"
        "c) Address or short description: ZIP 79834, Texas, USA"
    ),
    ("P002", "Amazon--32"): (
        "a) Product name: Amazon Basics Stainless Steel Electric Kettle for Tea and Coffee, "
        "BPA Free, Fast Boil, Auto Shut-Off, Boil-Dry Protection, 1.7 L, 1500 W, Black and Silver\n"
        "b) Price: USD 27.50\n"
        "c) Rating: 4.5"
    ),
    ("P002", "Apple--29"): "a) Estimated battery life (wireless web): up to 18 hours",
    ("P002", "Google Flights--34"): (
        "a) Chosen flight (airline / time): Etihad Airways / 12/07 08:15-09:35, 12/11 19:25-06:55\n"
        "b) Booking websites (>=1): Etihad Airways"
    ),
    ("P002", "Booking--3"): (
        "a) Hotel name: Modern 4 & 5 BR Townhomes Minutes from Downtown LA\n"
        "b) Star rating: 4\n"
        "c) Rating or review score: 7.7"
    ),
    ("P002", "Coursera--21"): (
        "a) Course title: Introduction to Digital Marketing\n"
        "b) Expected duration: 1 to 10 weeks (~11 hours)\n"
        "c) Offering institution: IBM\n"
        "d) Main learning outcomes: Gain foundational skills in social-media marketing and "
        "digital advertising to kickstart a digital-marketing or growth-hacking journey."
    ),
    ("P002", "GitHub--35"): "a) Latest release version: 19.2.6\nb) Publish date: May 6, 2026",
    ("P002", "Huggingface--14"): "a) Monthly price: USD 9\nb) Main features (>=2): 10x private storage capacity, 2x public storage capacity",
    ("P002", "ArXiv--17"): "a) v3 submission date: Mon, 27 Mar 2023 17:46:54 UTC",
}


def english_questions_by_task_id() -> dict[str, str]:
    data = json.loads(ASSIGNMENTS.read_text())
    return {t["task_id"]: t["question"] for t in data["tasks_master"]}


def normalize_timestamp(ts: str) -> str:
    """Convert the original +0900 timestamp to UTC (+0000) for the artifact."""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return ts
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def sanitize_one(path: Path, eng_q: dict[str, str]) -> tuple[dict, bool]:
    src = json.loads(path.read_text())
    pid = src["participant_id"]
    task_id = src["task_id"]

    # Disambiguate by parent-dir name so the REAL_ANSWERS lookup picks the
    # right (pid, task_id) entry — historically there were also pilot dry-run
    # buckets where the JSON pid did not match the directory pid.
    bucket_pid = path.parents[1].name
    lookup_pid = bucket_pid if bucket_pid != pid else pid

    new = {
        "participant_id": pid,
        "task_id": task_id,
        "order": src["order"],
        "question": eng_q.get(task_id, src["question"]),
        "answer": REAL_ANSWERS.get(
            (lookup_pid, task_id),
            "(answer redacted for public release)",
        ),
        "submitted_at": normalize_timestamp(src["submitted_at"]),
    }
    return new, new != src


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-only", action="store_true",
                    help="Report drift without writing.")
    args = ap.parse_args()

    eng_q = english_questions_by_task_id()
    n_changed = 0
    n_total = 0
    files = sorted(BACKUP_DIR.glob("*/*/answer.json"))
    for f in files:
        n_total += 1
        new, changed = sanitize_one(f, eng_q)
        if changed:
            n_changed += 1
            if args.check_only:
                print(f"[drift] {f.relative_to(STUDY_DIR)}")
            else:
                f.write_text(json.dumps(new, indent=2, ensure_ascii=True) + "\n")
                print(f"[wrote] {f.relative_to(STUDY_DIR)}")
        else:
            print(f"[ok]    {f.relative_to(STUDY_DIR)}")
    print(f"\n{n_changed}/{n_total} file(s) {'differ' if args.check_only else 'updated'}")
    if args.check_only and n_changed:
        sys.exit(1)


if __name__ == "__main__":
    main()
