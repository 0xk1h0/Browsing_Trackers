# Schema patches (Layer 2)

Agent-side tool-registry patches that remove navigation primitives.

| File | Agent | Env var | Recognized actions |
|---|---|---|---|
| `fara_disabled_actions.py` | Fara-7B | `FARA_DISABLED_ACTIONS` | `web_search`, `visit_url`, `history_back`, `pause_and_memorize_fact` |
| `browser_use_ablation.py` | Browser-Use | `BU_DISABLED_ACTIONS` | `search`, `navigate` |

## Conditions

| Condition | Fara `FARA_DISABLED_ACTIONS` | Browser-Use `BU_DISABLED_ACTIONS` |
|---|---|---|
| C0 | (unset) | (unset) |
| C1 | `web_search` | `search` |
| C2 | `visit_url` | `navigate` |
| C5 | `history_back` | n/a |
| C7 | `visit_url` (+ `RQ3_BLOCK_CMP=1`) | `navigate` (+ `RQ3_BLOCK_CMP=1`) |
| C8 | `web_search,visit_url` (+ `RQ3_BLOCK_CMP=1`) | `search,navigate` (+ `RQ3_BLOCK_CMP=1`) |

## Installation

**Fara-7B:** drop `fara_disabled_actions.py` next to
`models/fara/src/fara/_prompts.py`; apply the two-line patches described in
its docstring.

**Browser-Use:** import once before instantiating `Tools()`:

```python
from interventions.schema_patches import browser_use_ablation  # noqa: F401
```

For navigate removal, the patch defers stripping until *after* the initial
start-URL action runs (Browser-Use auto-injects `{'navigate': {url}}` for
the start URL).
