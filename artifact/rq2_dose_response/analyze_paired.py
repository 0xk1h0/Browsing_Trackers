#!/usr/bin/env python3
"""Per-agent ITT paired analysis vs scripted same-site reference (§7).

For each of the 6 agents in the source, report the agent vs scripted-same-site
baseline gap on:
  - tracker_presence  (binary: any tracker contacted?)
  - tracker_request_share
  - unique_tracker_domains
  - tracker_requests
  - total_requests

with bootstrap CI and Holm-corrected p. Pre-aggregated by
`paper/scripts/analyze_e1_multiagent.py` from raw paired_sessions.jsonl.

Inputs:
  data/multiagent_paired_results.json

Outputs:
  expected_outputs/paired_per_agent.tsv
  stdout
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "multiagent_paired_results.json"
OUT = HERE / "expected_outputs" / "paired_per_agent.tsv"

BASELINE = "scripted_same_site"
METRICS = [
    ("tracker_presence",       "trk_present"),
    ("unique_tracker_domains", "uniq_trk_doms"),
    ("tracker_requests",       "trk_reqs"),
    ("tracker_request_share",  "trk_share"),
    ("total_requests",         "tot_reqs"),
]


def main():
    with SRC.open() as f:
        d = json.load(f)

    print(f"=== Per-agent paired analysis vs `{BASELINE}` (ITT) ===")
    fmt = "{:<18s} {:>14s} {:>10s} {:>10s} {:>10s} {:>10s} {:>10s}"
    print(fmt.format("Agent", "metric", "agent_μ", "base_μ",
                     "gap", "median_Δ", "p_holm"))
    print("-" * 96)

    rows = []
    for entry in d:
        if entry.get("baseline_variant") != BASELINE:
            continue
        agent = entry["agent"]
        n = entry.get("matched_pairs")
        for key, label in METRICS:
            m = entry["itt_metrics"].get(key)
            if not m:
                continue
            row = {
                "agent": agent,
                "metric": label,
                "n_pairs": n,
                "agent_mean": m.get("agent_mean"),
                "baseline_mean": m.get("baseline_mean"),
                "gap": m.get("gap"),
                "median_gap": m.get("median_gap"),
                "ci_lo": m.get("ci_lo"),
                "ci_hi": m.get("ci_hi"),
                "p_holm": m.get("p_value_holm"),
            }
            rows.append(row)
            print(fmt.format(
                agent, label,
                _fmt_n(m.get("agent_mean")),
                _fmt_n(m.get("baseline_mean")),
                _fmt_n(m.get("gap")),
                _fmt_n(m.get("median_gap")),
                _fmt_p(m.get("p_value_holm")),
            ))
        print()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        f.write("agent\tmetric\tn_pairs\tagent_mean\tbaseline_mean\tgap"
                "\tmedian_gap\tci_lo\tci_hi\tp_holm\n")
        for r in rows:
            f.write("\t".join(str(r.get(k, "")) for k in
                              ["agent", "metric", "n_pairs",
                               "agent_mean", "baseline_mean", "gap",
                               "median_gap", "ci_lo", "ci_hi", "p_holm"]) + "\n")
    print(f"[paired] -> {OUT.relative_to(HERE)}")


def _fmt_n(x):
    if x is None:
        return "-"
    try:
        if x != x:
            return "n/a"
        return f"{x:.3f}" if abs(x) < 10 else f"{x:.1f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_p(p):
    if p is None:
        return "-"
    try:
        if p != p:
            return "n/a"
        if p < 1e-9:
            return f"{p:.1e}"
        return f"{p:.4g}"
    except (TypeError, ValueError):
        return "-"


if __name__ == "__main__":
    main()
