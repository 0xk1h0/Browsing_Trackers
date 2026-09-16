#!/usr/bin/env python3
"""Table 4 — tracker-family composition: human vs agent.

For each tracker family (cross_device_acr, native_recommendation,
identity_resolution, audience_measurement, ...), reports per-cohort:
  - mean requests / session
  - median requests / session
  - session touch rate (% of sessions with ≥1 request to that family)

Together with the odds-ratio / Fisher exact p (vs human cohort) from
`categorical_stats.csv`.

Key finding: agents touch tracker families humans rarely or never touch
(cross_device_acr 0% human → 9.3% agent; native_recommendation 1.4% → 9.3%).

Inputs:
  data/category_summary.json     # per-family mean/median/touch rates
  data/categorical_stats.csv     # odds ratio + Fisher p per family

Outputs:
  expected_outputs/table4_family_composition.tsv
  stdout
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC_SUM = HERE / "data" / "category_summary.json"
SRC_STATS = HERE / "data" / "categorical_stats.csv"
OUT = HERE / "expected_outputs" / "table4_family_composition.tsv"


def main():
    with SRC_SUM.open() as f:
        summary = json.load(f)

    stats = {}
    with SRC_STATS.open() as f:
        for r in csv.DictReader(f):
            stats[r["category"]] = r

    print("=== Table 4 — tracker-family composition (human vs agent) ===")
    fmt = "{:<26s} {:>9s} {:>9s} {:>10s} {:>10s} {:>10s} {:>12s}"
    print(fmt.format("Family", "h_mean", "a_mean",
                     "h_touch%", "a_touch%", "OR", "p (one-side)"))
    print("-" * 92)

    rows = []
    for cell in summary:
        cat = cell["category"]
        s = stats.get(cat, {})
        try:
            or_ = float(s.get("odds_ratio", "")) if s.get("odds_ratio") else float("nan")
            p = float(s.get("p_one_sided_agent_gt_human", "")) if s.get("p_one_sided_agent_gt_human") else float("nan")
        except ValueError:
            or_ = p = float("nan")
        rows.append({
            "family": cat,
            "h_mean": cell["h_mean"],
            "a_mean": cell["a_mean"],
            "h_median": cell["h_med"],
            "a_median": cell["a_med"],
            "h_touch_pct": cell["h_touch_pct"],
            "a_touch_pct": cell["a_touch_pct"],
            "odds_ratio": or_,
            "p_one_sided_agent_gt": p,
        })
        print(fmt.format(
            cat,
            f"{cell['h_mean']:.2f}",
            f"{cell['a_mean']:.2f}",
            f"{cell['h_touch_pct']:.1f}",
            f"{cell['a_touch_pct']:.1f}",
            f"{or_:.2f}" if or_ == or_ else "—",
            f"{p:.1e}" if p == p and p < 1e-3 else (f"{p:.3g}" if p == p else "—"),
        ))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        f.write("family\th_mean\ta_mean\th_median\ta_median\th_touch_pct"
                "\ta_touch_pct\todds_ratio\tp_one_sided_agent_gt\n")
        for r in rows:
            f.write("\t".join(str(r.get(k, "")) for k in
                              ["family", "h_mean", "a_mean", "h_median",
                               "a_median", "h_touch_pct", "a_touch_pct",
                               "odds_ratio", "p_one_sided_agent_gt"]) + "\n")
    print(f"\n[T4] -> {OUT.relative_to(HERE)}")


if __name__ == "__main__":
    main()
