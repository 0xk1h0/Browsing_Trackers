#!/usr/bin/env python3
"""Build per-(agent, condition) operational-success Tier-1 file from raw stopped.json.

Each ablation session writes a `stopped.json` next to its `capture.jsonl`,
containing the agent's self-reported `agent_self_status` field (one of
`success`, `hit_max_rounds`, `agent_error`, `unknown`, `""`). The
operational-success rate per (agent, condition) is computed as

    rate = count(agent_self_status == "success") / total_sessions

This matches the **commented-out v1** of paper Table 6 (`tab:rq3-ablation-2x2x2`):
Fara-7B C0 = 54.6%, Browser-Use C0 = 99.5%. The active v2 numbers in that
table (Fara C0 = 89.5%, BU C0 = 36.8%) cannot be derived from any data
file in the project tree at the time of this artifact build — see
`../NOTES.md` for the full discrepancy analysis.

Inputs (Tier-3 — NOT shipped):
  <raw-root>/agent=<name>/condition=<C>/task=<T>/rep=<r>/stopped.json

Outputs (Tier-1 — shipped under `data/rq3_643/`):
  task_success_ablation.json — per-(agent, condition) breakdown:
    {agent: {condition: {n, success, success_rate_pct, breakdown}}}

Usage:
    python scripts/compute_task_success_ablation.py \\
        --raw-root /path/to/data/rq7_policy_ablation_643/

    # Without --raw-root, runs against the path the paper used (Tier-3
    # path embedded only for documentation; will error if not present).
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

OUT_DEFAULT = Path(__file__).resolve().parents[1] / "data" / "rq3_643" / "task_success_ablation.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-root", type=Path, required=True,
                    help="path to rq7_policy_ablation_643/ root (Tier-3)")
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args = ap.parse_args()

    if not args.raw_root.exists():
        ap.error(f"raw root does not exist: {args.raw_root}")

    by_agent_cond_status: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: defaultdict(int))
    by_agent_cond_total: dict[tuple[str, str], int] = defaultdict(int)

    for agent_dir in sorted(args.raw_root.glob("agent=*")):
        agent = agent_dir.name.split("=", 1)[1]
        for cond_dir in sorted(agent_dir.glob("condition=*")):
            cond = cond_dir.name.split("=", 1)[1]
            for task_dir in sorted(cond_dir.glob("task=*")):
                for rep_dir in sorted(task_dir.glob("rep=*")):
                    sj = rep_dir / "stopped.json"
                    if not sj.exists():
                        continue
                    try:
                        d = json.load(sj.open())
                    except json.JSONDecodeError:
                        continue
                    status = d.get("agent_self_status", "") or "_empty"
                    by_agent_cond_status[(agent, cond)][status] += 1
                    by_agent_cond_total[(agent, cond)] += 1

    out: dict[str, dict[str, dict]] = {}
    print(f"{'agent':14s} {'cond':>4s}  {'n':>5s}  "
          f"{'success':>8s}  {'rate':>7s}  status breakdown")
    print("-" * 96)
    for (agent, cond), total in sorted(by_agent_cond_total.items()):
        statuses = by_agent_cond_status[(agent, cond)]
        succ = statuses.get("success", 0)
        rate = succ / total * 100 if total else 0
        out.setdefault(agent, {})[cond] = {
            "n": total,
            "success": succ,
            "success_rate_pct": round(rate, 1),
            "status_breakdown": dict(statuses),
        }
        breakdown = ", ".join(f"{s}={c}" for s, c in
                              sorted(statuses.items(), key=lambda x: -x[1])
                              if s != "_empty")
        print(f"{agent:14s} {cond:>4s}  {total:5d}  {succ:8d}  "
              f"{rate:6.1f}%  {breakdown[:40]}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        json.dump(out, f, indent=2)
    print(f"\n[task-succ-ablation] -> {args.out}")


if __name__ == "__main__":
    main()
