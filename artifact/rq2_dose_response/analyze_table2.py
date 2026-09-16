#!/usr/bin/env python3
"""Table 2 — per-agent empirical exposure on the full 643-task WebVoyager
benchmark (n=1,286 intent-to-treat sessions per agent, 7 agents = 9,002).

Every cell is derived from the shipped per-session records; nothing is read
from a pre-aggregated summary. Only records with condition == "agent" are
counted.

Columns (one row per agent, sorted by mean pseudo IDs descending):
  Duration (s)          mean / median of duration_seconds
  X-site transitions    mean / median of nav_cross_site_transition_count
  Off-site %            share of sessions with >= 1 cross-site transition
  Tracking hosts        mean / median of unique_tracker_domains
  Pseudo IDs            mean / median of
                        info_leakage_counts["pseudonymous_identifier"]
  FP triggered %        share of sessions with fingerprinting_api_call_count >= 1
  Op. succ. %           share of sessions with operational_success true
                        (>= 3 navigation requests without crash or timeout;
                        not benchmark answer accuracy)

Inputs:
  data/paired_sessions/*.jsonl.gz  # one JSON session record per line

Outputs:
  expected_outputs/table2.tsv      # rebuilt at every run
  stdout                           # human-readable table
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "paired_sessions"
OUT = HERE / "expected_outputs" / "table2.tsv"

# file stem -> paper label. All seven must be present.
AGENT_FILES = {
    "e1_opencua_paired_v1_paired_sessions.jsonl.gz": "OpenCUA-7B",
    "e1_fara7b_paired_v1_paired_sessions.jsonl.gz": "Fara-7B",
    "browseruse_bu30b_paired_v1_paired_sessions.jsonl.gz": "Browser-Use",
    "e1_somgpt5_paired_v1_paired_sessions.jsonl.gz": "SoM-GPT-5",
    "e1_uitars_paired_v1_paired_sessions.jsonl.gz": "UI-TARS",
    "e1_guiowl_paired_v1_paired_sessions.jsonl.gz": "GUI-Owl-8B",
    "e1_somglm_paired_v1_paired_sessions.jsonl.gz": "SoM-GLM",
}

COLUMNS = ["agent", "n_sessions", "duration_mean", "duration_med",
           "xsite_mean", "xsite_med", "offsite_pct", "trk_hosts_mean",
           "trk_hosts_med", "pseudo_mean", "pseudo_med", "fp_pct", "op_succ_pct"]


def load_agent(path: Path) -> list[dict]:
    with gzip.open(path, "rt") as f:
        return [r for r in map(json.loads, f) if r.get("condition") == "agent"]


def row_for(label: str, sessions: list[dict]) -> dict:
    dur = np.array([s["duration_seconds"] for s in sessions], float)
    xsite = np.array([s["nav_cross_site_transition_count"] for s in sessions], float)
    trk = np.array([s["unique_tracker_domains"] for s in sessions], float)
    pseudo = np.array([s["info_leakage_counts"]["pseudonymous_identifier"]
                       for s in sessions], float)
    fp = np.array([s["fingerprinting_api_call_count"] for s in sessions], float)
    op = np.array([bool(s["operational_success"]) for s in sessions])
    return {
        "agent": label,
        "n_sessions": len(sessions),
        "duration_mean": round(dur.mean(), 1),
        "duration_med": round(float(np.median(dur)), 1),
        "xsite_mean": round(xsite.mean(), 1),
        "xsite_med": round(float(np.median(xsite)), 1),
        "offsite_pct": round((xsite >= 1).mean() * 100, 1),
        "trk_hosts_mean": round(trk.mean(), 1),
        "trk_hosts_med": round(float(np.median(trk)), 1),
        "pseudo_mean": round(pseudo.mean(), 1),
        "pseudo_med": round(float(np.median(pseudo)), 1),
        "fp_pct": round((fp >= 1).mean() * 100, 1),
        "op_succ_pct": round(op.mean() * 100, 1),
    }


def main() -> None:
    missing = [n for n in AGENT_FILES if not (DATA / n).is_file()]
    if missing:
        sys.exit(f"[T2] FATAL: missing session files in {DATA}: {sorted(missing)}")

    rows = [row_for(label, load_agent(DATA / name))
            for name, label in AGENT_FILES.items()]
    rows.sort(key=lambda r: r["pseudo_mean"], reverse=True)

    total = sum(r["n_sessions"] for r in rows)
    print(f"Table 2 — per-agent exposure, {len(rows)} agents, {total} agent sessions\n")
    head = ("{:<13s} {:>7s} {:>7s} {:>6s} {:>6s} {:>8s} {:>7s} {:>6s} "
            "{:>8s} {:>8s} {:>8s} {:>9s}")
    body = ("{agent:<13s} {duration_mean:7.1f} {duration_med:7.1f} "
            "{xsite_mean:6.1f} {xsite_med:6.1f} {offsite_pct:8.1f} "
            "{trk_hosts_mean:7.1f} {trk_hosts_med:6.0f} {pseudo_mean:8.1f} "
            "{pseudo_med:8.1f} {fp_pct:8.1f} {op_succ_pct:9.1f}")
    print(head.format("Agent", "Dur.mean", "med", "X.mean", "med", "Off-site%",
                      "Trk.mean", "med", "Pseu.mean", "med", "FP%", "Op.succ%"))
    print("-" * 104)
    for r in rows:
        print(body.format(**r))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        f.write("\t".join(COLUMNS) + "\n")
        for r in rows:
            f.write("\t".join(str(r[c]) for c in COLUMNS) + "\n")
    print(f"\n[T2] -> {OUT.relative_to(HERE)}")


if __name__ == "__main__":
    main()
