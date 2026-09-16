# RQ1 — Human vs agent on the matched 10-task pool

22 participants × 10 tasks paired against 7 agents × 2 reps × 10 tasks.
220 paired (P × task) comparisons per (agent × metric) cell.

## Reproduce

```bash
bash scripts/run_repro.sh
```

## Outputs

| Output | Script |
|---|---|
| Table 1 — per-task tracker host matrix | `analyze_table1.py` |
| Table 4 — tracker family composition | `analyze_table4.py` |
| Primary paired Wilcoxon (4 metrics × 7 agents) | `analyze_rq1.py` |
| Paired-difference CDF figure | `plot_paired_diff.py` |

## Per-session schema (`data/per_session_full.csv`)

`cohort, agent, id, task_id, n_records, xsite, trk_hosts, pseudo, context,
device_net, behavior, direct_id, fp_apis` — 220 human + 140 agent rows.

## Rebuild from raw captures (Tier-3)

```bash
python scripts/rebuild_per_session_full.py \
    --human-root /path/to/user_study/runtime/backup/ \
    --agent-root /path/to/baseline_paper/.../runs/
```

Raw captures not shipped (`../docs/ETHICS.md`).
