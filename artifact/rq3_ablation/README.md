# RQ3 — Action-space ablation (2×2×2 schema + proxy)

Fara-7B and Browser-Use on the 643-task pool under 9 conditions
(`C0` default + 8 schema/proxy toggles). 6,430 sessions.

## Reproduce

```bash
bash scripts/run_repro.sh
```

## Outputs

| Output | Script |
|---|---|
| Table 6 — pseudo-ID median + success rate per (agent, condition) | `analyze_table6.py` |
| Table 12 / Appendix F — schema vs proxy isolation | `analyze_table12.py` |

## Condition matrix

See `paper_tables_for_reference/rq3_conditions.tex` (Table 5).

| ID | search | navigate | CMP | back | Notes |
|---|:---:|:---:|:---:|:---:|---|
| C0 | ✓ | ✓ | ✓ | ✓ | default |
| C1 | ✗ | ✓ | ✓ | ✓ | schema + proxy |
| C2 | ✓ | ✗ | ✓ | ✓ | schema (Fara) / proxy (BU) |
| C5 | ✓ | ✓ | ✓ | ✗ | negative control |
| C7 | ✓ | ✗ | ✗ | ✓ | Pareto-favorable |
| C8 | ✗ | ✗ | ✗ | ✓ | maximum |

## Intervention code

- Schema-layer (agent tool registry): `interventions/schema_patches/`
  (`fara_disabled_actions.py`, `browser_use_ablation.py`).
- Proxy-layer (mitmproxy addon): `interventions/proxy_filters/rq3_enforcement.py`.

## Rebuild

```bash
python scripts/compute_task_success_ablation.py \
    --raw-root /path/to/data/rq7_policy_ablation_643/
python scripts/rebuild_layer_isolation.py \
    --raw-root /path/to/data/rq7_policy_ablation/
```
