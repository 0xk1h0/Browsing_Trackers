#!/usr/bin/env python3
"""Recompute the per-agent exposure summary directly from shipped per-session
records, without going through any pre-aggregated summary file.

This exists because the reviewer should be able to check the Table 2 quantities
against the session-level data rather than against a summary that was produced
before release. It reads only files shipped in this artifact, uses only the
Python standard library, and finishes in a few seconds.

Usage:
    python3 claims/04_architecture/expected/recompute_table2.py
"""
from __future__ import annotations

import gzip
import json
import statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
# claims/04_architecture/expected -> claims/04_architecture -> claims -> root
ROOT = HERE.parent.parent.parent
ART = ROOT / "artifact"
if not ART.is_dir():
    ART = ROOT
DATA = ART / "rq2_dose_response" / "data" / "paired_sessions"

# The seven agents evaluated in the paper. The OpenAI CUA run shipped alongside
# them is a separate pilot and is not part of the 643-task, seven-agent pool.
LABELS = {
    "browseruse_bu30b": "Browser-Use",
    "e1_fara7b": "Fara-7B",
    "e1_opencua": "OpenCUA-7B",
    "e1_somglm": "SoM-GLM",
    "e1_somgpt5": "SoM-GPT-5",
    "e1_uitars": "UI-TARS",
    "e1_guiowl": "GUI-Owl-8B",
}


def sessions(path: Path):
    with gzip.open(path, "rt", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("condition") != "agent":
                continue
            yield rec


def main() -> int:
    if not DATA.is_dir():
        print(f"ERROR: per-session data not found at {DATA}")
        return 2

    rows = []
    for path in sorted(DATA.glob("*_paired_sessions.jsonl.gz")):
        key = path.name.replace("_paired_v1_paired_sessions.jsonl.gz", "")
        if key not in LABELS:
            continue
        xsite, pseudo, hosts = [], [], []
        for rec in sessions(path):
            xsite.append(int(rec.get("nav_cross_site_transition_count", 0) or 0))
            leak = rec.get("info_leakage_counts") or {}
            pseudo.append(int(leak.get("pseudonymous_identifier", 0) or 0))
            hosts.append(int(rec.get("nav_tracker_host_count", 0) or 0))
        if not xsite:
            print(f"ERROR: no agent sessions parsed from {path.name}")
            return 2
        rows.append(
            {
                "agent": LABELS[key],
                "n": len(xsite),
                "xsite_mean": st.mean(xsite),
                "xsite_median": st.median(xsite),
                "pseudo_mean": st.mean(pseudo),
                "pseudo_median": st.median(pseudo),
                "hosts_mean": st.mean(hosts),
            }
        )

    missing = set(LABELS.values()) - {r["agent"] for r in rows}
    if missing:
        print(f"ERROR: missing agents in shipped data: {sorted(missing)}")
        return 2

    rows.sort(key=lambda r: -r["pseudo_mean"])
    head = (
        f"{'agent':14s} {'n':>6s} {'xsite mean':>11s} {'xsite med':>10s} "
        f"{'pseudo mean':>12s} {'pseudo med':>11s} {'hosts mean':>11s}"
    )
    print(head)
    print("-" * len(head))
    for r in rows:
        print(
            f"{r['agent']:14s} {r['n']:6d} {r['xsite_mean']:11.2f} "
            f"{r['xsite_median']:10.1f} {r['pseudo_mean']:12.1f} "
            f"{r['pseudo_median']:11.1f} {r['hosts_mean']:11.1f}"
        )
    print()
    print(f"{len(rows)} agents, {sum(r['n'] for r in rows)} agent sessions total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
