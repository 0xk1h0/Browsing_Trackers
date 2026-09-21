#!/usr/bin/env python3
"""RQ1: paired human-vs-agent comparison on the matched 10-task pool.

Every number here is computed from data/per_session_full.csv, the shipped
per-session table: 220 human sessions (22 participants x 10 tasks) and 20
sessions per agent (10 tasks x 2 repetitions). Nothing is read from a
pre-aggregated summary.

Pairing follows the paper's design. Each human session (participant, task) is
paired with the agent's per-task median over its repetitions on that task, so
every agent-metric cell has 220 pairs. For each cell the script reports

  human and agent mean and median,
  mean and median paired difference (agent - human),
  the Hodges-Lehmann estimate of the difference (median of Walsh averages),
  a 95% percentile bootstrap CI of the median difference
      (10,000 resamples from numpy default_rng(0), so it is deterministic),
  the one-sided Wilcoxon signed-rank test, H1: agent > human, with zero
      differences dropped (scipy zero_method="wilcox").

Metric columns: M_P pseudonymous identifiers, M_T unique tracker hosts,
M_X cross-site transitions, M_F fingerprinting API calls.

Inputs:
  data/per_session_full.csv

Outputs:
  expected_outputs/rq1_paired.tsv
  expected_outputs/rq1_paired.json
  stdout
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "per_session_full.csv"
OUT_TSV = HERE / "expected_outputs" / "rq1_paired.tsv"
OUT_JSON = HERE / "expected_outputs" / "rq1_paired.json"

# (key in the outputs, column in per_session_full.csv, label for stdout)
METRICS = [
    ("M_P", "pseudo", "pseudo-IDs"),
    ("M_T", "trk_hosts", "trk hosts"),
    ("M_X", "xsite", "xsite trns"),
    ("M_F", "fp_apis", "FP API calls"),
]
N_BOOT = 10_000
SEED = 0


def hodges_lehmann(d: np.ndarray) -> float:
    walsh = (d[:, None] + d[None, :]) / 2.0
    return float(np.median(walsh[np.triu_indices(len(d))]))


def bootstrap_median_ci(d: np.ndarray) -> list[float]:
    rng = np.random.default_rng(SEED)
    meds = np.median(rng.choice(d, size=(N_BOOT, len(d)), replace=True), axis=1)
    return [float(np.quantile(meds, 0.025)), float(np.quantile(meds, 0.975))]


def paired_cell(agent: str, key: str, col: str, human: list[dict], agent_rows: list[dict]) -> dict:
    by_task: dict[str, list[float]] = {}
    for r in agent_rows:
        by_task.setdefault(r["task_id"], []).append(float(r[col]))
    task_median = {t: float(np.median(v)) for t, v in by_task.items()}
    paired = [r for r in human if r["task_id"] in task_median]
    h = np.array([float(r[col]) for r in paired])
    a = np.array([task_median[r["task_id"]] for r in paired])
    d = a - h
    w = stats.wilcoxon(d, alternative="greater", zero_method="wilcox")
    return {
        "agent": agent,
        "metric": key,
        "n_pairs": int(len(d)),
        "human_mean": float(np.mean(h)),
        "human_median": float(np.median(h)),
        "agent_mean": float(np.mean(a)),
        "agent_median": float(np.median(a)),
        "mean_delta": float(np.mean(d)),
        "median_delta": float(np.median(d)),
        "hl_delta": hodges_lehmann(d),
        "ci95_median_delta": bootstrap_median_ci(d),
        "wilcoxon_stat": float(w.statistic),
        "wilcoxon_p_one_sided_greater": float(w.pvalue),
    }


def main() -> None:
    with SRC.open(newline="") as f:
        rows = list(csv.DictReader(f))
    human = [r for r in rows if r["cohort"] == "human"]
    agents = list(dict.fromkeys(r["agent"] for r in rows if r["cohort"] == "agent"))
    target = sorted({r["task_id"] for r in human})

    result = {
        "n_participants": len({r["id"] for r in human}),
        "n_human_sessions": len(human),
        "target_tasks": target,
        "per_agent": {},
    }

    print("=== RQ1: human vs agent paired analysis (matched 10-task pool) ===")
    print(f"  n_participants = {result['n_participants']}, "
          f"n_human_sessions = {result['n_human_sessions']}, n_tasks = {len(target)}")
    print()
    fmt = "{:<18s} {:>12s} {:>10s} {:>10s} {:>10s} {:>16s} {:>10s}"
    print(fmt.format("Agent", "metric", "human_med", "agent_med", "HL delta", "CI95", "p"))
    print("-" * 96)

    tsv_rows = []
    for agent in agents:
        agent_rows = [r for r in rows if r["agent"] == agent]
        per_metric = {}
        for key, col, label in METRICS:
            m = paired_cell(agent, key, col, human, agent_rows)
            per_metric[key] = m
            lo, hi = m["ci95_median_delta"]
            p = m["wilcoxon_p_one_sided_greater"]
            print(fmt.format(agent, label, f"{m['human_median']:.1f}", f"{m['agent_median']:.1f}",
                             f"{m['hl_delta']:+.1f}", f"[{lo:.1f},{hi:.1f}]",
                             f"{p:.1e}" if p < 1e-3 else f"{p:.3g}"))
            tsv_rows.append([agent, key, m["human_median"], m["agent_median"], m["median_delta"],
                             m["hl_delta"], lo, hi, p, m["n_pairs"]])
        result["per_agent"][agent] = {
            "n_agent_sessions": len(agent_rows),
            "n_tasks_covered": len({r["task_id"] for r in agent_rows}),
            "per_metric": per_metric,
        }
        print()

    OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_TSV.open("w", newline="") as f:
        f.write("agent\tmetric\thuman_median\tagent_median\tmedian_delta"
                "\thl_delta\tci_lo\tci_hi\twilcoxon_p_greater\tn_pairs\n")
        for r in sorted(tsv_rows, key=lambda r: r[0]):
            f.write("\t".join(str(v) for v in r) + "\n")
    with OUT_JSON.open("w") as f:
        json.dump(result, f, indent=2)
    print(f"[RQ1] -> {OUT_TSV.relative_to(HERE)} + rq1_paired.json")


if __name__ == "__main__":
    main()
