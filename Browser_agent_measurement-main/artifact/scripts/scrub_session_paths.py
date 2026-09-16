#!/usr/bin/env python3
"""Scrub operator-local absolute paths out of the shipped per-session archives.

The per-session records carry a few provenance and debugging fields that were
captured verbatim during collection and can contain the collecting machine's
home directory, including the operator's username:

    agent_action_log_path, agent_stdout_tail, agent_stderr_tail, error

None of these fields is read by any analysis script, so rewriting them cannot
change a published number. This script replaces any absolute home-directory
prefix with a neutral placeholder and leaves every other byte alone.

Usage:
    python3 scripts/scrub_session_paths.py --check     # report only, exit 1 if dirty
    python3 scripts/scrub_session_paths.py             # rewrite in place

Rewriting is idempotent: running it twice is a no-op.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

SEARCH_DIRS = [
    ROOT / "rq2_dose_response" / "data" / "paired_sessions",
    ROOT / "cross_benchmark" / "data" / "paired_sessions",
]

# Fields that may carry operator-local paths. Verified not to be read by any
# analyzer: grep for these names across rq*/ and cross_benchmark/ returns
# nothing outside this script.
SCRUB_FIELDS = (
    "agent_action_log_path",
    "agent_stdout_tail",
    "agent_stderr_tail",
    "error",
)

# /home/<name>/ and /Users/<name>/ at any depth. The trailing slash keeps this
# from matching a bare "/home" that appears inside a URL path such as
# https://www.traderjoes.com/home/stores.
HOME_RE = re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+/")
PLACEHOLDER = "<HOME>/"


def scrub_value(value):
    if isinstance(value, str):
        return HOME_RE.sub(PLACEHOLDER, value)
    if isinstance(value, list):
        return [scrub_value(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items()}
    return value


def process(path: Path, check_only: bool) -> tuple[int, int]:
    """Return (records_with_hits, total_records)."""
    hits = 0
    total = 0
    out_lines = []
    with gzip.open(path, "rt", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            total += 1
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                out_lines.append(stripped)
                continue
            dirty = False
            for field in SCRUB_FIELDS:
                if field not in rec:
                    continue
                cleaned = scrub_value(rec[field])
                if cleaned != rec[field]:
                    rec[field] = cleaned
                    dirty = True
            if dirty:
                hits += 1
            out_lines.append(json.dumps(rec, ensure_ascii=False))

    if hits and not check_only:
        tmp = Path(tempfile.mkstemp(suffix=".jsonl.gz", dir=str(path.parent))[1])
        with gzip.open(tmp, "wt", encoding="utf-8") as out:
            for line in out_lines:
                out.write(line + "\n")
        shutil.move(str(tmp), str(path))

    return hits, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--check",
        action="store_true",
        help="report without rewriting; exit 1 if any archive is dirty",
    )
    args = ap.parse_args()

    archives = []
    for d in SEARCH_DIRS:
        if d.is_dir():
            archives.extend(sorted(d.glob("*.jsonl.gz")))
    if not archives:
        print("no archives found", file=sys.stderr)
        return 2

    dirty_files = 0
    dirty_records = 0
    for path in archives:
        hits, total = process(path, args.check)
        rel = path.relative_to(ROOT)
        if hits:
            dirty_files += 1
            dirty_records += hits
            verb = "would scrub" if args.check else "scrubbed"
            print(f"[scrub] {verb} {hits:5d}/{total:5d} records  {rel}")
        else:
            print(f"[scrub] clean    {total:5d} records  {rel}")

    print()
    if dirty_files:
        if args.check:
            print(
                f"[scrub] {dirty_records} records in {dirty_files} archive(s) "
                f"still contain operator-local paths."
            )
            return 1
        print(
            f"[scrub] rewrote {dirty_records} records across {dirty_files} archive(s)."
        )
    else:
        print("[scrub] no operator-local paths found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
