# RQ2 — Per-agent exposure and dose-response (643 tasks)

7 agents × 1,286 ITT sessions on the WebVoyager 643-task benchmark.

## Reproduce

```bash
bash scripts/run_repro.sh
```

## Outputs

| Output | Script |
|---|---|
| Table 2 — per-agent navigation + exposure | `analyze_table2.py` |
| Table 3 — action-space affordance bins | `analyze_table3.py` |
| Figure 3 — dose-response 3 panels | `plot_dose_response.py` |
| Per-agent ITT paired (vs scripted_same_site) | `analyze_paired.py` |
| §6 RTB broadcast (50-session Fara-7B upper-Q) | `analyze_rtb.py` |
| §6 CMP decisions | `analyze_cmp.py` |

## Data tier

Tier-1 aggregates only (`data/*.json`, `data/*.csv`). The full per-session
record format is defined in `per_session_full.csv` (see schema in
`../rq1_human_vs_agent/README.md`).

## Rebuild dose-response binning from raw flows

```bash
python scripts/rebuild_dose_response_643.py --runs-root /path/to/runs/
```
