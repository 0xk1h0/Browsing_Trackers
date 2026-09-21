#!/usr/bin/env python3
"""Figure — paired difference distribution (agent − human) per cohort × metric.

For each (agent × metric) cell, computes the paired difference for every
matched (task × participant) pair, then plots the empirical CDF of those
differences. The "0" vertical line is where the agent matches the human;
mass to the right is "agent transmits more".

Inputs:
  data/per_session_full.csv  # 360 sessions

Outputs:
  figures/fig_rq1_paired_diff.{pdf,png}
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "per_session_full.csv"
OUT_PDF = HERE / "figures" / "fig_rq1_paired_diff.pdf"
OUT_PNG = HERE / "figures" / "fig_rq1_paired_diff.png"

AGENTS = ["Browser-Use", "Fara-7B", "OpenCUA-7B",
          "SoM-GLM", "SoM-GPT-5", "UI-TARS", "GUI-Owl-8B"]
COLORS = {
    "Browser-Use": "#8e44ad", "Fara-7B": "#c0392b", "OpenCUA-7B": "#16a085",
    "SoM-GLM": "#27ae60", "SoM-GPT-5": "#e67e22",
    "UI-TARS": "#2980b9", "GUI-Owl-8B": "#7f8c8d",
}
METRICS = [
    ("pseudo", "M_P: pseudo IDs"),
    ("trk_hosts", "M_T: tracker hosts"),
    ("xsite", "M_X: cross-site transitions"),
    ("fp_apis", "M_F: FP API calls"),
]


def load_rows():
    by_cohort = defaultdict(list)
    with SRC.open() as f:
        for r in csv.DictReader(f):
            by_cohort[r["cohort"]].append(r)
    return by_cohort


def median_per_task(rows, agent_filter=None):
    """Median over reps for each task; for humans, median over participants."""
    by_task = defaultdict(list)
    for r in rows:
        if agent_filter is not None and r["agent"] != agent_filter:
            continue
        by_task[r["task_id"]].append({k: r[k] for k in
                                      ("pseudo", "trk_hosts", "xsite", "fp_apis")})
    medians = {}
    for task, recs in by_task.items():
        medians[task] = {
            k: float(np.median([int(rec[k]) for rec in recs]))
            for k in ("pseudo", "trk_hosts", "xsite", "fp_apis")
        }
    return medians


def main():
    cohorts = load_rows()
    human_medians = median_per_task(cohorts["human"])

    fig, axes = plt.subplots(1, 4, figsize=(13, 3.0), constrained_layout=True)

    for ax, (metric_key, metric_label) in zip(axes, METRICS):
        for ag in AGENTS:
            agent_medians = median_per_task(cohorts["agent"], agent_filter=ag)
            diffs = []
            for task, h in human_medians.items():
                a = agent_medians.get(task)
                if a is None:
                    continue
                diffs.append(a[metric_key] - h[metric_key])
            if not diffs:
                continue
            diffs.sort()
            ys = [(i + 1) / len(diffs) for i in range(len(diffs))]
            ax.step(diffs, ys, label=ag, color=COLORS[ag], where="post",
                    linewidth=1.4, alpha=0.85)

        ax.axvline(0, color="black", linewidth=0.5, alpha=0.7)
        ax.set_xlabel(f"Δ {metric_label}   (agent − human)")
        ax.set_ylabel("CDF over 10 tasks")
        ax.set_title(metric_label)
        ax.grid(alpha=0.3)
        if metric_key == "trk_hosts":
            ax.legend(loc="lower right", fontsize=7, ncol=1)

    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PDF)
    fig.savefig(OUT_PNG, dpi=150)
    print(f"[F1] -> {OUT_PDF.relative_to(HERE)} (+ .png)")


if __name__ == "__main__":
    main()
