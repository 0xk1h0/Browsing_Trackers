#!/usr/bin/env python3
"""Scrub operator identity out of the released user-study session archive.

Two operator identifiers reach the released bundles.

The remote-desktop server's own log (``xpra.log``) records the account the
study server ran as:

    uid=1001 (someone), gid=1001 (someone)

That is the operator's account, not a participant's, but it is exactly the kind
of identifier ACSAC asks artifacts not to ship, and the path sanitizer that
produced the archive rewrote home directories without touching this line.

Separately, trackers echo the client IP back into the traffic we captured, as a
``cip=`` request parameter and in ``x-forwarded-for``, ``x-proxy-origin``,
``x-q-stat`` and ``location`` response headers. That address identifies the
study institution's network, so it is replaced with ``<study-network-ip>``.
Third-party and loopback addresses are left untouched, because they are part of
what the study measured.

This script rewrites both identifiers to neutral placeholders inside the archive
and repacks it. No metric this artifact publishes is derived from either field.

Usage:
    python3 scripts/scrub_user_study_archive.py --check   # report only, exit 1 if dirty
    python3 scripts/scrub_user_study_archive.py           # rewrite the archive in place

Rewriting is idempotent.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ARCHIVE = (
    ROOT
    / "rq1_human_vs_agent"
    / "user_study"
    / "runtime"
    / "participants_sessions.tar.xz"
)

# uid=1001 (name), gid=1001 (name)  ->  uid=<uid> (<operator>), gid=<gid> (<operator>)
UID_RE = re.compile(rb"uid=\d+ \([A-Za-z0-9._-]+\), gid=\d+ \([A-Za-z0-9._-]+\)")
UID_SUB = b"uid=<uid> (<operator>), gid=<gid> (<operator>)"
UID_TARGET = "xpra.log"

# The study network's own public address. Trackers echo the client IP back in
# request parameters and response headers (cip=, x-forwarded-for, x-proxy-origin,
# x-q-stat, location), so it lands inside the captured traffic. It identifies the
# institution's network, so it is replaced with a placeholder. Third-party and
# loopback addresses are left alone: they are part of what was measured.
STUDY_PUBLIC_IP = rb"129\.254\.184\.3"
IP_RE = re.compile(STUDY_PUBLIC_IP)
IP_SUB = b"<study-network-ip>"
IP_TARGETS = ("capture.jsonl", "js_telemetry.jsonl")


def rules_for(name: str):
    """Return the (regex, replacement) rules that apply to this filename."""
    rules = []
    if name == UID_TARGET:
        rules.append((UID_RE, UID_SUB))
    if name in IP_TARGETS:
        rules.append((IP_RE, IP_SUB))
    return rules


def scan_member(data: bytes, rules) -> int:
    return sum(len(rx.findall(data)) for rx, _ in rules)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--check",
        action="store_true",
        help="report without rewriting; exit 1 if the archive is dirty",
    )
    args = ap.parse_args()

    if not ARCHIVE.is_file():
        print(f"ERROR: archive not found at {ARCHIVE}", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="scrub_us_"))
    try:
        extracted = work / "tree"
        extracted.mkdir()
        with tarfile.open(ARCHIVE, "r:xz") as tf:
            members = tf.getmembers()
            tf.extractall(extracted, filter="data")

        dirty_files = 0
        dirty_hits = 0
        for path in sorted(extracted.rglob("*")):
            if not path.is_file():
                continue
            rules = rules_for(path.name)
            if not rules:
                continue
            data = path.read_bytes()
            hits = scan_member(data, rules)
            if not hits:
                continue
            dirty_files += 1
            dirty_hits += hits
            if not args.check:
                for rx, sub in rules:
                    data = rx.sub(sub, data)
                path.write_bytes(data)

        if args.check:
            if dirty_files:
                print(
                    f"[scrub-us] {dirty_hits} operator identifier(s) across "
                    f"{dirty_files} file(s) of {len(members)} members."
                )
                return 1
            print(f"[scrub-us] clean: {len(members)} members, no operator identifiers.")
            return 0

        if not dirty_files:
            print("[scrub-us] already clean, archive untouched.")
            return 0

        # Repack deterministically: sorted names, no uid/gid/uname/gname carried over.
        tmp_archive = Path(
            tempfile.mkstemp(suffix=".tar.xz", dir=str(ARCHIVE.parent))[1]
        )
        with tarfile.open(tmp_archive, "w:xz", preset=9 | __import__("lzma").PRESET_EXTREME) as out:
            for path in sorted(extracted.rglob("*")):
                arc = path.relative_to(extracted).as_posix()
                info = out.gettarinfo(str(path), arcname=arc)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                if path.is_file():
                    with path.open("rb") as fh:
                        out.addfile(info, fh)
                else:
                    out.addfile(info)
        shutil.move(str(tmp_archive), str(ARCHIVE))
        print(
            f"[scrub-us] rewrote {dirty_hits} identifier(s) across {dirty_files} "
            f"file(s) and repacked {ARCHIVE.name}."
        )
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
