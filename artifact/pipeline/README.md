# Pipeline

Capture, classify, aggregate. Every per-session record in
`../rq*_/data/` was produced once by this pipeline.

```
capture/
  mitm_addon.py          mitmproxy passive capture + RQ3 enforcement
  hook.js                JS shim: Canvas/WebGL/AudioContext/screen/navigator
  js_fingerprint_monitor.py   Python helper that emits the shim
  proxy_runner.sh
classifier/
  family_classifier.py   71-family + 7 super-category classifier (standalone)
  families.json          super-category ↔ family map
  classify.py            full TrafficAnalyzer (reference)
  families.py            full CookieAnalyzer (reference)
  tracker_lists/         March 2026 snapshot of 7 filter lists + 7-list lookup
aggregation/
  six_metrics.py         MetricAnalyzer (reference)
```

## Tracker rule

`is_tracker = True` iff ≥1 of the 7 filter lists flags the host.

## Leakage classes

`pseudo` / `context` / `behavior` / `device_net` / `direct_id` —
keyword sets and per-class weights live in `classifier/family_classifier.py`.

## Run

```bash
export AGENTCLOAK_CAPTURE_FILE=$PWD/capture.jsonl
export AGENTCLOAK_FP_CAPTURE_FILE=$PWD/js_telemetry.jsonl
bash capture/proxy_runner.sh --listen-port 8080 --ssl-insecure
```

Inject `hook.js` via Playwright `page.add_init_script`.
