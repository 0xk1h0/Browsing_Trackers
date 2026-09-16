#!/usr/bin/env python3
"""Rebuild data/mind2web_per_session.csv from raw Mind2Web paired_sessions.jsonl.

Task definitions live at `data/mind2web/Online_Mind2Web.json` (artifact root).
This script needs Tier-3 raw per-agent session data — NOT shipped.
See ../docs/ETHICS.md for data-release tiering.

The shipped CSV was produced by running this script against the project's
internal raw data archive.

Usage:
    python scripts/rebuild_mind2web_per_session.py --runs-root /path/to/runs/
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


AGENTS = [
    ("Browser-Use",  "browseruse_bu30b_mind2web_paired_v1"),
    ("Fara-7B",      "mind2web_fara7b_v1"),
    ("SoM-GLM",      "mind2web_somglm_v1"),
    ("SoM-GPT-5",    "mind2web_somgpt5_v1"),
    ("UI-TARS",      "mind2web_uitars_v1"),
    ("OpenCUA-7B",   "mind2web_opencua_v1"),
    ("GUI-Owl-8B",   "mind2web_guiowl_v1"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", type=Path, required=True,
                    help="path to raw paired-sessions root (Tier-3, not shipped)")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parents[1]
                    / "data" / "mind2web_per_session.csv")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with args.out.open("w", newline="") as fout:
        w = csv.writer(fout)
        w.writerow([
            "agent", "condition", "task_id", "domain", "session_id",
            "operational_success", "task_success",
            "duration_seconds", "total_requests", "third_party_requests",
            "tracker_requests", "unique_tracker_domains",
            "nav_xsite_transitions", "nav_tracker_host_count",
            "pseudo_id", "context_signal", "device_network_signal",
            "behavior_signal", "direct_identifier",
        ])
        for label, run in AGENTS:
            path = args.runs_root / run / "paired_sessions.jsonl"
            n = 0
            if not path.exists():
                print(f"  ! missing {path}")
                continue
            for line in path.open():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                leak = r.get("info_leakage_counts") or {}
                w.writerow([
                    label,
                    r.get("condition", ""),
                    r.get("task_id", ""),
                    r.get("domain", ""),
                    r.get("session_id", ""),
                    int(bool(r.get("operational_success"))),
                    int(bool(r.get("task_success"))),
                    r.get("duration_seconds", "") or "",
                    r.get("total_requests", 0) or 0,
                    r.get("third_party_requests", 0) or 0,
                    r.get("tracker_requests", 0) or 0,
                    r.get("unique_tracker_domains", 0) or 0,
                    r.get("nav_cross_site_transition_count", 0) or 0,
                    r.get("nav_tracker_host_count", 0) or 0,
                    int(leak.get("pseudonymous_identifier", 0) or 0),
                    int(leak.get("context_signal", 0) or 0),
                    int(leak.get("device_network_signal", 0) or 0),
                    int(leak.get("behavior_signal", 0) or 0),
                    int(leak.get("direct_identifier", 0) or 0),
                ])
                n += 1
            print(f"  {label:14s} {n:5d} sessions")
            total += n
    print(f"\n[rebuild] {total} sessions -> {args.out}")


if __name__ == "__main__":
    main()
