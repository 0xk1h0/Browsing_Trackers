#!/usr/bin/env python3
"""RQ3 Appendix F — schema vs proxy layer-isolation (Table 12 / Layer-iso).

Reports a 2x2 schema-vs-proxy decomposition for the search and navigate
primitives on the 10-task user-study pool. Demonstrates that schema-only
removal of navigate raises Fara-7B's pseudo IDs by +26% through multi-hop
substitution, while proxy-only delivers -42% — the two layers are not
interchangeable.

Inputs:
  data/rq3_layer_isolation/per_session.csv      # Tier-1 (shipped)
  data/rq3_layer_isolation/decomposition.csv    # pre-computed cells (shipped)

Outputs:
  stdout summary table
  data/rq3_layer_isolation/decomposition.csv    # re-derived from per_session

To rebuild per_session.csv from raw mitmproxy flows, see
`scripts/rebuild_layer_isolation.py` (requires Tier-3 capture.jsonl, not shipped).
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data" / "rq3_layer_isolation"
PER_SESSION = DATA_DIR / "per_session.csv"
OUT_DECOMP = DATA_DIR / "decomposition.csv"

USER_STUDY_TASKS = {
    "Google Search--28", "Google Map--35", "Amazon--32", "Apple--29",
    "Google Flights--34", "Booking--3", "Coursera--21", "GitHub--35",
    "Huggingface--14", "ArXiv--17",
}


def load_per_session():
    rows = []
    with PER_SESSION.open() as f:
        for r in csv.DictReader(f):
            rows.append({
                "agent": r["agent"],
                "condition": r["condition"],
                "task": r["task"],
                "rep": r["rep"],
                "pseudo_id_count": int(r["pseudo_id_count"]),
            })
    return rows


def main():
    rows = load_per_session()
    print(f"[T12] {len(rows)} sessions loaded from {PER_SESSION.name}")

    by_ac = defaultdict(list)
    for r in rows:
        if r["task"] in USER_STUDY_TASKS:
            by_ac[(r["agent"], r["condition"])].append(r["pseudo_id_count"])

    print("\n=== Per-(agent, condition) median pseudo-IDs (user-study 10 tasks) ===")
    print(f"{'Agent':14s} {'Cond':>5s} {'n':>4s} {'median':>8s} {'mean':>8s} {'std':>8s}")
    print("-" * 60)
    summary = {}
    for ag in ["fara-7b", "browser-use"]:
        for cond in ["C0", "C1", "C1S", "C1P", "C2", "C2S", "C2P", "C7", "C8"]:
            vals = by_ac.get((ag, cond), [])
            if not vals:
                continue
            med = float(np.median(vals))
            mn = float(np.mean(vals))
            st = float(np.std(vals))
            summary[(ag, cond)] = (len(vals), med, mn, st)
            print(f"{ag:14s} {cond:>5s} {len(vals):4d} {med:8.0f} {mn:8.0f} {st:8.0f}")
        print()

    print("\n=== 2x2 schema-vs-proxy decomposition (Δ pseudo-IDs vs C0) ===")
    for ag in ["fara-7b", "browser-use"]:
        c0 = summary.get((ag, "C0"))
        if not c0:
            continue
        c0_med = c0[1]

        def delta(cond):
            s = summary.get((ag, cond))
            if not s:
                return "n/a"
            return f"{(s[1] - c0_med) / c0_med * 100:+.0f}%"

        print(f"\n  {ag}:  C0 baseline median = {c0_med:.0f}")
        print(f"  Search primitive ablation:")
        print(f"    C1S (schema only):  {delta('C1S')}")
        print(f"    C1P (proxy only):   {delta('C1P')}")
        print(f"    C1  (schema+proxy): {delta('C1')}")
        print(f"  Navigate primitive ablation:")
        print(f"    C2S (schema only):  {delta('C2S')}")
        print(f"    C2P (proxy only):   {delta('C2P')}")
        print(f"    C2  (schema+proxy): {delta('C2')}")

    with OUT_DECOMP.open("w") as f:
        f.write("agent,condition,n,median,mean,std,delta_pct_vs_C0\n")
        for (ag, cond), (n, med, mn, st) in summary.items():
            c0 = summary.get((ag, "C0"))
            d = (med - c0[1]) / c0[1] * 100 if c0 else float("nan")
            f.write(f"{ag},{cond},{n},{med:.1f},{mn:.1f},{st:.1f},{d:+.2f}\n")

    print(f"\n[T12] -> {OUT_DECOMP}")


if __name__ == "__main__":
    main()
