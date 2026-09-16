#!/usr/bin/env python3
"""Table 1 — 10-task tracker-host matrix.

For each of the 10 matched tasks, reports the median unique-tracker-host count
across the human cohort and each agent cohort. Builds a (10 task × 8 cohort)
matrix that's the visual headline of RQ1: agents systematically reach more
tracker hosts than humans on the same task.

Inputs:
  data/per_session_full.csv  # 360 sessions × 13 columns

Outputs:
  expected_outputs/table1_trk_hosts.tsv
  stdout
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "per_session_full.csv"
OUT = HERE / "expected_outputs" / "table1_trk_hosts.tsv"

AGENT_ORDER = [
    "human",
    "Browser-Use", "Fara-7B", "OpenCUA-7B",
    "SoM-GLM", "SoM-GPT-5", "UI-TARS", "GUI-Owl-8B",
]


def main():
    by_task_agent = defaultdict(lambda: defaultdict(list))
    tasks_seen = set()
    with SRC.open() as f:
        for r in csv.DictReader(f):
            try:
                trk = int(r["trk_hosts"])
            except (ValueError, KeyError):
                continue
            by_task_agent[r["task_id"]][r["agent"]].append(trk)
            tasks_seen.add(r["task_id"])

    tasks = sorted(tasks_seen)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fmt_h = "{:<22s}" + " {:>10s}" * len(AGENT_ORDER)
    fmt_r = "{:<22s}" + " {:>10d}" * len(AGENT_ORDER)

    print(f"=== Table 1 — median unique tracker hosts per session ===")
    print(fmt_h.format("Task", *AGENT_ORDER))
    print("-" * (22 + 11 * len(AGENT_ORDER)))

    with OUT.open("w") as fout:
        fout.write("task\t" + "\t".join(AGENT_ORDER) + "\n")
        for task in tasks:
            row_vals = []
            for ag in AGENT_ORDER:
                vals = by_task_agent[task].get(ag, [])
                row_vals.append(int(median(vals)) if vals else 0)
            print(fmt_r.format(task[:22], *row_vals))
            fout.write(task + "\t" + "\t".join(str(v) for v in row_vals) + "\n")

    print()
    overall = []
    for ag in AGENT_ORDER:
        vals = [v for task in tasks for v in by_task_agent[task].get(ag, [])]
        overall.append(int(median(vals)) if vals else 0)
    print(fmt_r.format("OVERALL median", *overall))
    print(f"\n[T1] -> {OUT.relative_to(HERE)}")


if __name__ == "__main__":
    main()
