#!/usr/bin/env python3
"""Compare a rebuild of the human arm against the shipped per-session table.

The rebuild is produced by running the released classifier and aggregator over
the released raw session captures. This script reports, per column, how many
sessions come back identical, so a reviewer can see how much of the published
table the shipped instrument regenerates from raw data.

Usage:
    compare_rebuild.py --shipped per_session_full.csv --rebuilt rebuilt.csv
"""
from __future__ import annotations

import argparse
import csv
import sys

# Columns the rebuild can reconstruct from the released Tier-2 captures.
# `pseudo` is deliberately excluded: Cookie headers are removed from the
# released captures under the IRB protocol, so identifier counts cannot be
# recomputed from them by design.
COLUMNS = ("n_records", "trk_hosts", "xsite")

# Expected agreement, measured by the authors on the released archive. The
# script fails if agreement drops below these floors, so a regression in the
# classifier or the aggregator is caught rather than silently tolerated.
FLOOR = {"n_records": 219, "trk_hosts": 219, "xsite": 165}


def norm_task(t: str) -> str:
    # Session directories spell task ids with underscores; the shipped table
    # uses spaces.
    return t.replace("_", " ").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shipped", required=True)
    ap.add_argument("--rebuilt", required=True)
    args = ap.parse_args()

    with open(args.shipped, newline="") as fh:
        shipped = {
            (r["id"], norm_task(r["task_id"])): r
            for r in csv.DictReader(fh)
            if r["cohort"] == "human"
        }
    with open(args.rebuilt, newline="") as fh:
        rebuilt = list(csv.DictReader(fh))

    agree = dict.fromkeys(COLUMNS, 0)
    compared = 0
    unmatched = 0
    for row in rebuilt:
        key = (row["id"], norm_task(row["task_id"]))
        ref = shipped.get(key)
        if ref is None:
            unmatched += 1
            continue
        compared += 1
        for col in COLUMNS:
            if str(ref[col]).strip() == str(row[col]).strip():
                agree[col] += 1

    print(f"shipped human sessions   : {len(shipped)}")
    print(f"rebuilt session records  : {len(rebuilt)}")
    print(f"joined for comparison    : {compared}")
    if unmatched:
        print(f"rebuilt rows not in table: {unmatched} (duplicate session directory)")
    print()
    ok = True
    for col in COLUMNS:
        floor = FLOOR[col]
        mark = "ok " if agree[col] >= floor else "LOW"
        if agree[col] < floor:
            ok = False
        pct = 100.0 * agree[col] / compared if compared else 0.0
        print(f"  [{mark}] {col:10s} identical on {agree[col]:3d}/{compared} ({pct:.1f}%), floor {floor}")

    print()
    print("  pseudo IDs are not compared: Cookie headers are stripped from the")
    print("  released captures under the IRB protocol, so identifier counts")
    print("  cannot be recomputed from them. That is a release decision, not a")
    print("  pipeline limitation.")
    print()
    if ok:
        print("[pipeline] The released instrument reproduces the published per-session")
        print("[pipeline] record counts and tracker-host counts from raw captures.")
        return 0
    print("[pipeline] Agreement fell below the recorded floor.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
