#!/usr/bin/env python3
"""Table 4: tracker-family composition, human vs agent, on the matched pool.

Computed from data/per_session_categories.csv, which holds per-session request
counts to each tracker-service family for the 220 human sessions and the 140
agent sessions (7 agents x 10 tasks x 2 repetitions). For each family:

  mean and median requests per session, per cohort,
  touch rate: share of sessions with at least one request to the family,
  odds ratio of touching the family, human odds over agent odds,
  one-sided Fisher exact p (H1: agent sessions touch the family more often).

Table 4 prints the means and touch rates. The abstract's 0% against 9.3%
(cross-device ACR) and 1.4% against 9.3% (native recommendation) are two of
the touch-rate pairs; their Fisher p values are the ones CLAIMS.md quotes.

Inputs:
  data/per_session_categories.csv
Outputs:
  expected_outputs/table4_family_composition.tsv
  stdout
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from scipy.stats import fisher_exact

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "per_session_categories.csv"
OUT = HERE / "expected_outputs" / "table4_family_composition.tsv"
META = {"cohort", "agent", "id", "task_id", "n_tracker_hosts"}
COLUMNS = ["family", "h_mean", "a_mean", "h_median", "a_median", "h_touch_pct",
           "a_touch_pct", "odds_ratio", "p_one_sided_agent_gt"]


def main() -> None:
    with SRC.open(newline="") as f:
        rows = list(csv.DictReader(f))
    families = [k for k in rows[0] if k not in META]
    human = [r for r in rows if r["cohort"] == "human"]
    agent = [r for r in rows if r["cohort"] == "agent"]

    print("=== Table 4: tracker-family composition (human vs agent) ===")
    print(f"  human sessions = {len(human)}, agent sessions = {len(agent)}")
    fmt = "{:<26s} {:>9s} {:>9s} {:>10s} {:>10s} {:>10s} {:>12s}"
    print(fmt.format("Family", "h_mean", "a_mean", "h_touch%", "a_touch%", "OR", "p (one-side)"))
    print("-" * 92)

    out = []
    for fam in families:
        h = np.array([float(r[fam]) for r in human])
        a = np.array([float(r[fam]) for r in agent])
        nh, na = len(h), len(a)
        ht, at = int((h > 0).sum()), int((a > 0).sum())
        odds = (ht / (nh - ht)) / (at / (na - at)) if at and ht < nh and at < na else float("nan")
        p = float(fisher_exact([[at, na - at], [ht, nh - ht]], alternative="greater").pvalue)
        row = {
            "family": fam,
            "h_mean": float(h.mean()), "a_mean": float(a.mean()),
            "h_median": float(np.median(h)), "a_median": float(np.median(a)),
            "h_touch_pct": 100.0 * ht / nh, "a_touch_pct": 100.0 * at / na,
            "odds_ratio": round(odds, 3), "p_one_sided_agent_gt": p,
        }
        out.append(row)
        print(fmt.format(fam, f"{row['h_mean']:.2f}", f"{row['a_mean']:.2f}",
                         f"{row['h_touch_pct']:.1f}", f"{row['a_touch_pct']:.1f}",
                         f"{odds:.2f}" if odds == odds else "-",
                         f"{p:.1e}" if p < 1e-3 else f"{p:.3g}"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        f.write("\t".join(COLUMNS) + "\n")
        for row in out:
            f.write("\t".join(str(row[k]) for k in COLUMNS) + "\n")
    print(f"\n[T4] -> {OUT.relative_to(HERE)}")


if __name__ == "__main__":
    main()
