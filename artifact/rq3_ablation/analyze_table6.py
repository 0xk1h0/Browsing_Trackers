#!/usr/bin/env python3
"""RQ3 ablation — Table 6 + extended metrics (643-task scale).

For each (agent, condition) cell, report:
  - median per-session pseudo-ID count (primary metric)
  - median session duration (s)
  - median cross-site transitions per session
  - median unique tracker hosts per session
  - operational success rate
  - mean unique third-party hosts blocked at the proxy
    (ablated conditions only; reveals how often the proxy fired)

Also runs Mann-Whitney U C0 vs {C2, C7, C8} per agent on pseudo-ID counts.

Inputs:
  data/rq3_643/aggregate_per_session.csv
  data/rq3_643/pseudo_ids_per_session.csv

Outputs:
  data/rq3_643/full_metrics_per_cell.csv
  data/rq3_643/ablation_stat_tests.csv
  stdout summary table
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import numpy as np
from scipy.stats import mannwhitneyu

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data" / "rq3_643"
AGG_CSV = DATA_DIR / "aggregate_per_session.csv"
PSEUDO_CSV = DATA_DIR / "pseudo_ids_per_session.csv"
PER_CELL_CSV = DATA_DIR / "aggregate_per_cell.csv"
ACCURACY_JSON = DATA_DIR / "task_accuracy_eval.json"
TASK_SUCC_ABLATION_JSON = DATA_DIR / "task_success_ablation.json"
OUT_DIR = DATA_DIR


def load_task_success_ablation() -> dict[tuple[str, str], dict]:
    """Per-(agent, condition) operational success from stopped.json files.

    `agent_self_status == "success"` fraction per ablation cell.
    """
    if not TASK_SUCC_ABLATION_JSON.exists():
        return {}
    with TASK_SUCC_ABLATION_JSON.open() as f:
        d = json.load(f)
    return {(agent, cond): v
            for agent, conds in d.items()
            for cond, v in conds.items()}


def load_per_task_success() -> dict[tuple[str, str], float]:
    """Per-(agent, condition) mean success rate across 643 tasks.

    Source: aggregate_per_cell.csv `success_rate` column (per-task aggregate
    over reps).
    """
    by_ac: dict[tuple[str, str], list[float]] = defaultdict(list)
    if not PER_CELL_CSV.exists():
        return {}
    with PER_CELL_CSV.open() as f:
        for r in csv.DictReader(f):
            try:
                by_ac[(r["agent"], r["condition"])].append(float(r["success_rate"]))
            except (ValueError, KeyError):
                continue
    return {k: (sum(v) / len(v) * 100 if v else 0.0) for k, v in by_ac.items()}


def load_c0_task_accuracy() -> dict[str, float]:
    """C0 LLM-judge task accuracy per agent from task_accuracy_eval.json."""
    if not ACCURACY_JSON.exists():
        return {}
    with ACCURACY_JSON.open() as f:
        d = json.load(f)
    return {agent: v.get("task_accuracy") for agent, v in d.items()}


def _norm_task(t: str) -> str:
    return t.replace("_", " ")


def _norm_rep(r: str) -> str:
    return str(int(r))


def load_rows():
    agg = {}
    with AGG_CSV.open() as f:
        for row in csv.DictReader(f):
            key = (row["agent"], row["condition"], _norm_task(row["task"]),
                   _norm_rep(row["rep"]))
            agg[key] = row

    pseudo = {}
    with PSEUDO_CSV.open() as f:
        for row in csv.DictReader(f):
            key = (row["agent"], row["condition"], _norm_task(row["task"]),
                   _norm_rep(row["rep"]))
            pseudo[key] = int(row["pseudo_id_count"])

    merged = []
    for key, a in agg.items():
        merged.append({
            "agent": key[0],
            "condition": key[1],
            "task": key[2],
            "rep": key[3],
            "records": int(a.get("records", 0) or 0),
            "third_party_hosts": int(a.get("third_party_hosts", 0) or 0),
            "unique_hosts": int(a.get("unique_hosts", 0) or 0),
            "duration_s": float(a.get("duration_seconds", 0) or 0),
            "task_success": a.get("task_success", "") or "",
            "return_code": int(a.get("return_code", 0) or 0),
            "rq3_blocks_search": int(a.get("rq3_blocks_search", 0) or 0),
            "rq3_blocks_offdomain": int(a.get("rq3_blocks_offdomain", 0) or 0),
            "rq3_blocks_cmp": int(a.get("rq3_blocks_cmp", 0) or 0),
            "pseudo_id_count": pseudo.get(key, None),
        })
    return merged


def main():
    rows = [r for r in load_rows() if r["pseudo_id_count"] is not None]
    print(f"[T6] {len(rows)} merged sessions with pseudo-ID")

    by_ac = defaultdict(list)
    for r in rows:
        by_ac[(r["agent"], r["condition"])].append(r)

    per_task_success = load_per_task_success()
    c0_task_acc = load_c0_task_accuracy()
    task_succ_ablation = load_task_success_ablation()

    print("\n=== Per-(agent, condition) extended metrics ===")
    cols = ["agent", "cond", "n",
            "pseudo_med", "pseudo_mean",
            "dur_med", "dur_mean",
            "trk_med", "trk_mean",
            "blk_off_mean", "blk_search_mean", "blk_cmp_mean",
            "rc0_pct", "task_succ_pct"]
    hdr = " ".join(f"{c:>11s}" if i > 1 else f"{c:13s}" for i, c in enumerate(cols))
    print(hdr); print("-" * len(hdr))

    cell_rows = []
    for ag in sorted({r["agent"] for r in rows}):
        for cond in ["C0", "C2", "C5", "C7", "C8"]:
            sessions = by_ac.get((ag, cond), [])
            if not sessions:
                continue
            n = len(sessions)
            pids = [s["pseudo_id_count"] for s in sessions]
            durs = [s["duration_s"] for s in sessions]
            trks = [s["third_party_hosts"] for s in sessions]
            blk_off = [s["rq3_blocks_offdomain"] for s in sessions]
            blk_search = [s["rq3_blocks_search"] for s in sessions]
            blk_cmp = [s["rq3_blocks_cmp"] for s in sessions]
            rc0_pct = sum(1 for s in sessions if s["return_code"] == 0) / n * 100
            task_succ = per_task_success.get((ag, cond))
            task_succ_str = f"{task_succ:6.1f}" if task_succ is not None else "   n/a"
            print(f"{ag:13s} {cond:>5s} {n:5d}  "
                  f"{int(median(pids)):6d} {int(mean(pids)):6d}  "
                  f"{median(durs):6.1f} {mean(durs):6.1f}  "
                  f"{int(median(trks)):4d} {int(mean(trks)):4d}  "
                  f"{mean(blk_off):6.2f} {mean(blk_search):6.2f} {mean(blk_cmp):6.2f}  "
                  f"{rc0_pct:6.1f} {task_succ_str}")
            cell_rows.append({
                "agent": ag, "condition": cond, "n": n,
                "pseudo_median": int(median(pids)), "pseudo_mean": int(mean(pids)),
                "duration_median_s": round(median(durs), 1),
                "duration_mean_s": round(mean(durs), 1),
                "third_party_hosts_median": int(median(trks)),
                "third_party_hosts_mean": int(mean(trks)),
                "blocks_offdomain_mean": round(mean(blk_off), 2),
                "blocks_search_mean": round(mean(blk_search), 2),
                "blocks_cmp_mean": round(mean(blk_cmp), 2),
                "rc0_pct": round(rc0_pct, 1),
                "task_success_pct": round(task_succ, 1) if task_succ is not None else None,
            })

    if task_succ_ablation:
        print("\n=== Operational success rate per (agent, condition) ===")
        print(f"  {'agent':14s} {'cond':>4s}  {'n':>5s}  {'success':>8s}  {'rate':>7s}")
        print("  " + "-" * 50)
        for (ag, cond), v in sorted(task_succ_ablation.items()):
            print(f"  {ag:14s} {cond:>4s}  {v['n']:5d}  {v['success']:8d}  "
                  f"{v['success_rate_pct']:6.1f}%")

    print("\n=== Mann-Whitney U (per-agent, vs C0 baseline) ===")
    print(f"{'agent':14s} {'cond':>5s} {'U':>10s} {'p':>10s} {'effect (med diff)':>20s}")
    print("-" * 70)
    stat_rows = []
    for ag in sorted({r["agent"] for r in rows}):
        c0 = [s["pseudo_id_count"] for s in by_ac.get((ag, "C0"), [])]
        for cond in ["C2", "C5", "C7", "C8"]:
            ck = [s["pseudo_id_count"] for s in by_ac.get((ag, cond), [])]
            if not c0 or not ck:
                continue
            U, p = mannwhitneyu(ck, c0, alternative="two-sided")
            eff = int(median(ck) - median(c0))
            print(f"{ag:14s} {cond:>5s} {U:10.0f} {p:10.4g} {eff:+20d}")
            stat_rows.append({
                "agent": ag, "condition": cond,
                "U_statistic": float(U), "p_value": float(p),
                "median_diff_vs_C0": eff,
            })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "full_metrics_per_cell.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(cell_rows[0].keys()))
        w.writeheader()
        w.writerows(cell_rows)
    with (OUT_DIR / "ablation_stat_tests.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(stat_rows[0].keys()))
        w.writeheader()
        w.writerows(stat_rows)
    print(f"\n[T6] -> {OUT_DIR}/full_metrics_per_cell.csv + ablation_stat_tests.csv")


if __name__ == "__main__":
    main()
