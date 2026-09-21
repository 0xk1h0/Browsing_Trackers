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
| §6 RTB broadcast (50-session Fara-7B upper-Q) | `analyze_rtb.py` |
| §6 CMP decisions | `analyze_cmp.py` |

## Data

Per-session records: `data/paired_sessions/<agent>_paired_sessions.jsonl.gz`,
one record per agent session, and `data/rtb_50sessions.csv` for the 50-session
RTB sample. Recorded inputs that are read as shipped, each mapped in
`../CLAIMS.md`: `data/consent_decisions_v2.json` and
`data/accept_heuristic_validation.json` (CMP decisions),
`data/papadogiannakis_replication.json`, and the host ranking in
`data/rtb_50sessions_summary.json`.

## Rebuild dose-response binning from raw flows

```bash
python scripts/rebuild_dose_response_643.py --runs-root /path/to/runs/
```
