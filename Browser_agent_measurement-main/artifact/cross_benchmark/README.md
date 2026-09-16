# Cross-benchmark — Mind2Web

7 agents × 600 Mind2Web sessions = 4,200 sessions. Validates that the
per-agent ranking on WebVoyager carries over to a different task pool.

## Reproduce

```bash
bash scripts/run_repro.sh
```

Outputs Table 7 + Spearman ρ on per-agent pseudo-ID means.

## Schema

`data/mind2web_per_session.csv` — agent, condition, task, domain,
session_id, success flags, navigation counts, per-class leakage counts
(see column header).

## Rebuild

```bash
python scripts/rebuild_mind2web_per_session.py --runs-root /path/to/runs/
```
