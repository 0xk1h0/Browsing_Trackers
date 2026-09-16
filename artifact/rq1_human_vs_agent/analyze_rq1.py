#!/usr/bin/env python3
"""RQ1 — primary paired analysis: human vs each agent on the 10-task pool.

For each of 7 evaluated agents, reports the matched human↔agent paired
difference on four metric classes (see Methodology §4):

  M_P  pseudonymous identifiers transmitted / session
  M_T  unique tracker hosts contacted / session
  M_X  cross-site transitions / session
  M_F  fingerprint-API calls / session

Each cell shows human median, agent median, Hodges–Lehmann median difference
(agent − human), bootstrap 95% CI, and Wilcoxon signed-rank p (one-sided
H1: agent > human).

The paired design ties one human session (P{nnn}, task T) to one agent rep
on the same task; matching is performed inside the upstream extractor and
preserved in `data/per_session_full.csv`.

Inputs:
  data/agent_stats.json     # pre-aggregated paired stats per (agent, metric)
  data/per_session_full.csv # raw 360-session counts (humans + agents)

Outputs:
  expected_outputs/rq1_paired.tsv
  expected_outputs/rq1_paired.json   (verbatim from agent_stats.json)
  stdout                              # human-readable
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "agent_stats.json"
OUT_TSV = HERE / "expected_outputs" / "rq1_paired.tsv"
OUT_JSON = HERE / "expected_outputs" / "rq1_paired.json"

METRICS = [
    ("M_P", "pseudo-IDs"),
    ("M_T", "trk hosts"),
    ("M_X", "xsite trns"),
    ("M_F", "FP API calls"),
]


def main():
    with SRC.open() as f:
        d = json.load(f)

    n_part = d.get("n_participants")
    n_hum = d.get("n_human_sessions")
    target = d.get("target_tasks", [])

    print(f"=== RQ1 — human vs agent paired analysis (matched 10-task pool) ===")
    print(f"  n_participants = {n_part}, n_human_sessions = {n_hum}, "
          f"n_tasks = {len(target)}")
    print()
    fmt = "{:<18s} {:>12s} {:>10s} {:>10s} {:>10s} {:>12s} {:>12s}"
    print(fmt.format("Agent", "metric", "human_med",
                     "agent_med", "HL Δ", "CI95", "p"))
    print("-" * 96)

    rows = []
    for agent, stats in sorted(d.get("per_agent", {}).items()):
        for key, label in METRICS:
            m = stats.get("per_metric", {}).get(key)
            if not m:
                continue
            ci = m.get("ci95_median_delta", [None, None])
            ci_str = f"[{ci[0]:.1f},{ci[1]:.1f}]" if ci[0] is not None else "—"
            p = m.get("wilcoxon_p_one_sided_greater")
            p_str = "n/a" if p is None else (f"{p:.1e}" if p < 1e-3 else f"{p:.3g}")
            rows.append({
                "agent": agent, "metric": key,
                "human_median": m.get("human_median"),
                "agent_median": m.get("agent_median"),
                "median_delta": m.get("median_delta"),
                "hl_delta": m.get("hl_delta"),
                "ci_lo": ci[0], "ci_hi": ci[1],
                "wilcoxon_p_greater": p,
                "n_pairs": m.get("n_pairs"),
            })
            print(fmt.format(
                agent, label,
                f"{m.get('human_median'):.1f}",
                f"{m.get('agent_median'):.1f}",
                f"{m.get('hl_delta'):+.1f}",
                ci_str, p_str,
            ))
        print()

    OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_TSV.open("w") as f:
        f.write("agent\tmetric\thuman_median\tagent_median\tmedian_delta"
                "\thl_delta\tci_lo\tci_hi\twilcoxon_p_greater\tn_pairs\n")
        for r in rows:
            f.write("\t".join(str(r.get(k, "")) for k in
                              ["agent", "metric", "human_median",
                               "agent_median", "median_delta", "hl_delta",
                               "ci_lo", "ci_hi", "wilcoxon_p_greater",
                               "n_pairs"]) + "\n")
    with OUT_JSON.open("w") as f:
        json.dump(d, f, indent=2)
    print(f"[RQ1] -> {OUT_TSV.relative_to(HERE)} + rq1_paired.json")


if __name__ == "__main__":
    main()
