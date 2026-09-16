# Shared task pools

Task definitions used by more than one RQ live here so each consumer reads
from a single source of truth.

| File                              | Used by | Description                                                                                       |
|-----------------------------------|---------|---------------------------------------------------------------------------------------------------|
| `webvoyager/WebVoyager_data.jsonl`| RQ2, RQ3| 643 WebVoyager tasks (one JSON object per line). Downloaded from the WebVoyager release; not modified. |
| `mind2web/Online_Mind2Web.json`   | Cross-benchmark | 300 Online Mind2Web tasks across 145 websites. `confirmed_task` + `website` + `level` + `reference_length` fields per record. |

The user-study 10-task pool is in
`rq1_human_vs_agent/data/participant_assignments.json` because it's RQ1-only.

## Schemas

### WebVoyager (`webvoyager/WebVoyager_data.jsonl`)

```json
{"web": "Google Search", "id": "Google_Search--28", "ques": "...", "start_url": "..."}
```

### Online Mind2Web (`mind2web/Online_Mind2Web.json`)

```json
{
  "task_id": "<sha1>_<seq>",
  "confirmed_task": "Find the store location and hours of …",
  "website": "https://www.traderjoes.com/",
  "level": "easy | medium | hard",
  "reference_length": 6
}
```

Both files are loaded by the rebuild scripts shipped under each consuming
RQ (see `rq3_ablation/scripts/run_643_sensitivity.py` for WebVoyager and
`cross_benchmark/scripts/rebuild_mind2web_per_session.py` for Mind2Web).
