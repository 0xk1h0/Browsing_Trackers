#!/usr/bin/env python3
"""Rebuild data/dose_response_643.csv from raw paired_sessions.jsonl files.

REQUIRES Tier-3 raw per-agent session data — NOT shipped with this artifact.
See ../docs/ETHICS.md for the data-release tiering.

The shipped dose_response_643.csv was produced by running this script against
the project's internal raw data archive.

Usage:
    python scripts/rebuild_dose_response_643.py \\
        --runs-root /path/to/baseline_paper/.../runs \\
        --out data/dose_response_643.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

AGENT_FILES_DEFAULT = {
    "Fara-7B":     "e1_fara7b_paired_v1/paired_sessions.jsonl",
    "Browser-Use": "browseruse_bu30b_paired_v1/paired_sessions.jsonl",
    "UI-TARS":     "e1_uitars_paired_v2/paired_sessions.jsonl",
    "SoM-GLM":     "e1_somglm_paired_v2/paired_sessions.jsonl",
    "SoM-GPT-5":   "e1_somgpt5_paired_v1/paired_sessions.jsonl",
    "OpenCUA-7B":  "e1_opencua_paired_v1/paired_sessions.jsonl",
    "GUI-Owl-8B":  "e1_guiowl_paired_v1/paired_sessions.jsonl",
}


def bin_label(x: int) -> str:
    if x == 0:
        return "0"
    if x == 1:
        return "1"
    if x == 2:
        return "2"
    if x <= 5:
        return "3-5"
    if x <= 10:
        return "6-10"
    return "11+"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", type=Path, required=True,
                    help="path to .../runs/ directory (Tier-3, not shipped)")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1]
                    / "data" / "dose_response_643.csv")
    args = ap.parse_args()

    by_bin = defaultdict(lambda: defaultdict(list))
    total = 0
    for agent, rel in AGENT_FILES_DEFAULT.items():
        path = args.runs_root / rel
        if not path.exists():
            print(f"  ! missing: {path}")
            continue
        with path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                s = json.loads(line)
                if s.get("condition") != "agent":
                    continue
                x = int(s.get("xsite_transitions", 0))
                leak = s.get("info_leakage_counts", {})
                fp = s.get("fp_apis", 0) or s.get("fingerprint_api_calls", 0)
                bk = bin_label(x)
                by_bin[bk]["trk"].append(s.get("unique_tracker_hosts", 0))
                by_bin[bk]["pseudo"].append(leak.get("pseudonymous_identifier", 0))
                by_bin[bk]["ctx"].append(leak.get("context_signal", 0))
                by_bin[bk]["dev"].append(leak.get("device_network_signal", 0))
                by_bin[bk]["fp"].append(1 if fp > 0 else 0)
                total += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bin", "n", "pop_pct", "med_trk",
                    "med_pseudo", "med_ctx", "med_dev", "fp_pct"])
        for bk in ["0", "1", "2", "3-5", "6-10", "11+"]:
            data = by_bin.get(bk)
            if not data:
                continue
            n = len(data["pseudo"])
            w.writerow([
                bk, n, round(n / total * 100, 1),
                int(np.median(data["trk"])),
                int(np.median(data["pseudo"])),
                int(np.median(data["ctx"])),
                int(np.median(data["dev"])),
                round(sum(data["fp"]) / n * 100, 1),
            ])

    print(f"[rebuild] {total} agent sessions -> {args.out}")


if __name__ == "__main__":
    main()
