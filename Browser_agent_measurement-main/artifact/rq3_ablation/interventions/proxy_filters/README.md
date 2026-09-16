# Proxy filter (Layer 3)

mitmproxy addon that drops requests at the wire level under three toggles.

| Env var | Effect |
|---|---|
| `RQ3_BLOCK_SEARCH=1` | drop search-engine hosts/paths (google /search, bing /search, ...) |
| `RQ3_BLOCK_OFFDOMAIN=1` | drop off-eTLD+1 navigation requests |
| `RQ3_BLOCK_CMP=1` | drop CMP consent-banner CDNs (OneTrust, Cookiebot, ...) |

Pair with the schema patches in `../schema_patches/`; condition → env-var
mapping is in `../../README.md`.

## Run

```bash
export RQ3_BLOCK_OFFDOMAIN=1
export AGENTCLOAK_CAPTURE_FILE=$PWD/capture.jsonl
mitmdump -s rq3_enforcement.py -s ../../pipeline/capture/mitm_addon.py \
  --listen-port 8080 --ssl-insecure
```

Blocked requests are recorded with `rq3_blocked: true` and a `rq3_reason`
field (`search` / `offdomain` / `cmp`).
