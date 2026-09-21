# Browser-Agent Tracker — Artifact

Artifact for the ACSAC 2026 paper *"Brave New Browsing! Tracker Exposure under
Browser-Agent Delegation."*

The paper measures how much third-party tracking a user is exposed to when a
browser agent performs a web task on their behalf, compared with a human
performing the same task from the same starting page. It does this through a
matched user study (22 participants on 10 WebVoyager tasks), a per-agent
dose-response analysis over the full 643-task WebVoyager benchmark with seven
agents, a two-layer action-space ablation on two of them, and a cross-benchmark
check on Online Mind2Web.

This repository reproduces the paper's tables and figures from the per-session
data shipped here. Four results rest on data held back in the collection tree
and are marked in CLAIMS.md. For the mapping from each paper claim to the
script and output file that produces it, see [CLAIMS.md](CLAIMS.md).

---
All paper-side claims are derivable from the per-session data shipped here; no GPU, no mitmproxy, no API calls. Full pipeline rebuild
from raw mitmproxy flows is documented in `INSTALL.md`.

## Quick start

From the repository root, about a minute in total:

```bash
bash install.sh                    # creates .venv, installs 4 pinned packages
PYTHON=$PWD/.venv/bin/python bash artifact/REPRODUCE.sh
.venv/bin/python artifact/verify.py
```

Expected final lines: `[REPRODUCE] All modules reproduced successfully.` then
`[verify] All checks passed.`

Reproduce a subset with `bash artifact/REPRODUCE.sh rq1 rq2 rq3 cross`, or run a
single paper claim end to end with `bash claims/01_matched_exposure/run.sh`.

`uv` is used when present and the script falls back to `venv` + `pip` when it is
not. `bash install.sh --full` additionally installs the collection pipeline
(vLLM, mitmproxy, Playwright); that profile is only needed to re-run the
measurement itself and is documented in [INSTALL.md](INSTALL.md).

## Contents

- [Glossary](#glossary)
- [Collection methodology](#collection-methodology)
- [RQ1 — Human vs agent on the matched 10-task pool](#rq1--human-vs-agent-on-the-matched-10-task-pool)
- [RQ2 — Per-agent dose-response on 643 WebVoyager tasks](#rq2--per-agent-dose-response-on-643-webvoyager-tasks)
- [RQ3 — Action-space ablation](#rq3--action-space-ablation)
- [Cross-benchmark — Mind2Web](#cross-benchmark--mind2web)
- [Browser-agent line-up](#browser-agent-line-up)
- [Data tiers](#data-tiers)
- [Running without installing anything locally](#running-without-installing-anything-locally)
- [Verification](#verification)
- [Anonymization gate](#anonymization-gate)
- [License and citation](#license-and-citation)

## Glossary

| Term | Meaning |
|------|---------|
| **RQ1** | Does delegating browsing to an agent change tracker exposure vs the same human task? (matched 10-task pool) |
| **RQ2** | How does exposure scale with the agent's navigation behavior? (643-task WebVoyager dose-response) |
| **RQ3** | Does removing search / navigate primitives at the tool-schema + proxy layers reduce exposure? (2×2×2 ablation) |
| **M_P** | Pseudonymous identifiers transmitted per session (e.g., `_ga`, `clientid`, `gclid`) |
| **M_T** | Unique tracker hosts contacted per session |
| **M_X** | Cross-site (cross-eTLD+1) transitions per session |
| **M_F** | Fingerprinting-API calls per session (Canvas / WebGL / AudioContext / …) |
| **C0 … C8** | Ablation conditions: C0=default, C2=−navigate, C5=−back (negative control), C7=−navigate −CMP, C8=−search −navigate −CMP. Full matrix in `rq3_ablation/README.md`. |
| **Tier-1 / 2 / 3** | Data-release classes; see [Data tiers](#data-tiers) |

## Collection methodology

Every human and agent session went through the same five-stage measurement
pipeline. Reviewers can re-collect a single session end-to-end with
`bash pipeline/collect_session.sh` (see `INSTALL.md` for the GPU / mitmproxy
prerequisites).

```text
  ┌─ Stage 1 ───────────────────────┐  ┌─ Stage 2 ────────────────┐
  │ Capture (mitmproxy + JS shim)  │  │ Tracker classification    │
  │ pipeline/capture/mitm_addon.py │  │ pipeline/classifier/      │
  │ pipeline/capture/hook.js       │  │   family_classifier.py    │
  └─────────────┬───────────────────┘  └─────────────┬─────────────┘
                ▼                                    ▼
  ┌─ Stage 3 ──────────────────────────────────────────────────────┐
  │ Per-session aggregation: M_P, M_T, M_X, M_F, behavior signals  │
  │ pipeline/aggregation/six_metrics.py                            │
  └─────────────┬──────────────────────────────────────────────────┘
                ▼
  ┌─ Stage 4 ───────────────────────────┐  ┌─ Stage 5 ─────────────┐
  │ Per-RQ statistical analyzers        │  │ Release-time          │
  │ rq{1,2,3}_*/analyze_*.py            │  │ anonymization gate    │
  └─────────────────────────────────────┘  │ scripts/anonymize_*.sh│
                                           └───────────────────────┘
```

| Stage | Component | What it does |
|-------|-----------|--------------|
| 1 | `pipeline/capture/mitm_addon.py` + `hook.js` | Decrypts HTTPS via a locally-trusted CA, writes every request/response to `capture.jsonl`, and injects a JS shim that records every call to Canvas / WebGL / AudioContext / screen / navigator into `js_telemetry.jsonl`. Also enforces the RQ3 proxy-layer toggles (`RQ3_BLOCK_SEARCH`, `RQ3_BLOCK_OFFDOMAIN`, `RQ3_BLOCK_CMP`). |
| 2 | `pipeline/classifier/family_classifier.py` | Tags each host using ≥1 of seven filter lists (Disconnect, EasyList, EasyPrivacy, Fanboy's Enhanced, AdGuard Tracking, Peter Lowe's, uBlock Privacy — March 2026 snapshot). Maps tagged hosts to one of 71 fine-grained families and one of seven super-categories. Standalone — no project-internal imports. |
| 3 | `pipeline/aggregation/six_metrics.py` | Reduces a session to the four metric counts (M_P, M_T, M_X, M_F) plus the context, device/network and behavior signal counts using the keyword sets in `family_classifier.py`. Outputs one row per session — the Tier-1 CSV every RQ analyzer consumes. |
| 4 | `rq{1,2,3}_*/analyze_*.py` | Per-RQ tests (Wilcoxon, Mann-Whitney U, Hodges-Lehmann + bootstrap, Spearman). See each per-RQ section below. |
| 5 | `scripts/anonymize_check.sh` | Greps the released tree for local paths, Korean text, internal hostnames, RFC1918 IPs, API-key signatures. Non-zero exit on any hit. |

## RQ1 — Human vs agent on the matched 10-task pool

**Question.** Does delegating a 10-task pool to an agent change the user's
tracker exposure relative to performing the same tasks directly?

**Design.** 22 participants × 10 fixed WebVoyager tasks under a **cyclic
Latin-square** assignment that counter-balances first-order position
effects (fatigue, learning, attention drift). Each human session is paired
with each agent's rep on the same task; the Wilcoxon test runs on those
220 paired (P × task) deltas per (agent × metric) cell.

**User-study harness** (operator-side, IRB-anonymized): the entire
deployment lives at [`rq1_human_vs_agent/user_study/`](rq1_human_vs_agent/user_study/).
Participants drive a fully instrumented Chromium on the study server via
an HTML5 remote-desktop client; every HTTP request, JS fingerprinting
call, and participant answer is captured per session and per task.
The harness includes:

- `proto/` — Flask web app, task definitions, session spawner, mitmproxy
  stealth addon, capture inspector
- `client/` — HTML5 study client (instructions, task questions, answer form)
- `xpra-html5/` — the HTML5 remote-desktop client the participant used. It is
  an external dependency cloned at deploy time rather than vendored here; see
  `rq1_human_vs_agent/user_study/README.md` section 3.2
- `scripts/sanitize_sessions.py` — removes operator-side leakage from
  captured sessions before release
- `verify_reproducibility.py` — release-gate check (looks for residual
  PII / operator paths in any runtime artefact)
- `participant_assignments.json` — the master task pool + Latin-square
  assignment table

**Tier-1 shipped:** `data/per_session_full.csv` (220 human + 140 agent =
360 rows). Schema documented in `rq1_human_vs_agent/README.md`.

**Tier-2 shipped:** `user_study/runtime/participants_sessions.tar.xz`
(22 MB — sanitized per-task capture bundles for participants P001-P022).

**Run.**

```bash
cd rq1_human_vs_agent
bash scripts/run_repro.sh         # runs all 4 analyzers + plot
# or one at a time:
python analyze_table1.py
python analyze_table4.py
python analyze_rq1.py
python plot_paired_diff.py
```

Outputs land in `rq1_human_vs_agent/expected_outputs/` and `figures/`.

**Outputs.**

| Paper       | Script                                | Result                                          |
|-------------|---------------------------------------|-------------------------------------------------|
| Tab. 1      | `analyze_table1.py`                   | 10-task tracker-host matrix (humans + 7 agents) |
| Tab. 4      | `analyze_table4.py`                   | Tracker-family composition with Fisher's exact  |
| Primary §7  | `analyze_rq1.py`                      | Paired Wilcoxon on (M_P, M_T, M_X, M_F) per agent |
| Fig. (RQ1)  | `plot_paired_diff.py`                 | Paired-difference CDF                           |

Raw-flow rebuild path:

```bash
python scripts/rebuild_per_session_full.py \
    --human-root user_study/runtime/backup/ \
    --agent-root /path/to/baseline_paper/.../runs/
```

## RQ2 — Per-agent dose-response on 643 WebVoyager tasks

**Question.** As the agent's cross-site navigation increases, how does
tracker exposure scale?

**Design.** 7 agents × 1,286 ITT (intent-to-treat) sessions on the full
643-task WebVoyager benchmark. Per-session records are summarized per agent
(Table 2), binned by cross-site-transition count for Table 3 and the
dose-response plot, and joined with the agent registry for the affordance
table.

**Tier-2 shipped:** `data/paired_sessions/<agent>_paired_sessions.jsonl.gz`
— 8 gzipped per-agent summaries (6.6 MB total), one record per session.

**Run.**

```bash
cd rq2_dose_response
bash scripts/run_repro.sh         # runs all 5 analyzers + the dose-response plot
# or one at a time:
python analyze_table2.py
python analyze_table3.py
python plot_dose_response.py
python analyze_rtb.py
python analyze_cmp.py
```

Outputs land in `rq2_dose_response/expected_outputs/` and `figures/`.

**Outputs.**

| Paper      | Script                                         | Result                                                       |
|------------|------------------------------------------------|--------------------------------------------------------------|
| Tab. 2     | `analyze_table2.py`                            | Per-agent 6-metric exposure on 643 tasks                     |
| Tab. 3     | `analyze_table3.py`                            | Action-space affordance bins                                 |
| Fig. 3     | `plot_dose_response.py`                        | Three panels: pseudo-ID, context, FP trigger rate            |
| §6         | `analyze_rtb.py`                               | RTB broadcast on 50-session Fara-7B upper-quartile sample    |
| §6         | `analyze_cmp.py`                               | CMP consent decisions across 7 agents                        |

Raw-flow rebuild path:

```bash
python scripts/rebuild_dose_response_643.py --runs-root /path/to/runs/
```

## RQ3 — Action-space ablation

**Question.** Does removing the search / navigate primitives at the tool
schema and at the proxy layer reduce tracker exposure?

**Design.** Fara-7B and Browser-Use × 5 ablation conditions × 643 tasks =
**6,430 sessions**. Two-layer enforcement:

- **Layer 2 (schema):** the model never sees the disabled action in its tool
  registry — `rq3_ablation/interventions/schema_patches/`.
- **Layer 3 (proxy):** the mitmproxy addon drops the matching request even
  if the agent finds an alternative way to issue it —
  `rq3_ablation/interventions/proxy_filters/`.

Appendix F isolates the layers (schema-only `*S` vs. proxy-only `*P` vs.
both) for the search and navigate primitives.

**Run.**

```bash
cd rq3_ablation
bash scripts/run_repro.sh         # runs Table 6 + Table 12 analyzers
# or one at a time:
python analyze_table6.py
python analyze_table12.py
```

Outputs land in `rq3_ablation/data/rq3_643/` and `rq3_layer_isolation/`.

**Outputs.**

| Paper           | Script                                  | Result                                                       |
|-----------------|-----------------------------------------|--------------------------------------------------------------|
| Tab. 5          | `paper_tables_for_reference/rq3_conditions.tex` | Ablation condition matrix                            |
| Tab. 6          | `analyze_table6.py`                     | 2×2×2 ablation (6,430 sessions)                              |
| Tab. 12 (App F) | `analyze_table12.py`                    | Schema-only vs proxy-only layer isolation                    |

**End-to-end re-collection** (requires GPU + mitmproxy, see `INSTALL.md`):

```bash
# Full 643-task sweep, one agent / one condition
python -m rq3_ablation.scripts.run_643_sensitivity \
    --agent fara-7b --base-url http://localhost:5001/v1 \
    --conditions C0 C2 C7 C8 \
    --shard 0 --num-shards 2 \
    --output-root rq3_ablation/raw/rq7_policy_ablation_643 \
    --max-rounds 30 --timeout 300

# 16-session smoke (2 agents × 4 conds × 2 tasks)
python -m rq3_ablation.scripts.rq3_smoke_test --execute

# Re-aggregate Tier-1 from the raw flows above
python scripts/compute_task_success_ablation.py --raw-root rq3_ablation/raw/rq7_policy_ablation_643/
python scripts/rebuild_layer_isolation.py        --raw-root rq3_ablation/raw/rq7_policy_ablation/
```

## Cross-benchmark — Mind2Web

**Question.** Do per-agent exposure rankings transfer to a different task
pool?

**Design.** 7 agents × 600 Mind2Web tasks = 4,200 sessions. Agent-level
Spearman correlation between per-agent mean M_P on WebVoyager and on
Mind2Web.

**Tier-2 shipped:** `data/paired_sessions/<agent>_paired_sessions.jsonl.gz`
— 7 gzipped per-agent summaries (3.8 MB total).

**Run.**

```bash
cd cross_benchmark
bash scripts/run_repro.sh         # runs the Table 7 analyzer
# or directly:
python analyze_table7.py
```

Output lands in `cross_benchmark/expected_outputs/` (TSV + Spearman JSON).

| Paper  | Script                                          | Result                                       |
|--------|-------------------------------------------------|----------------------------------------------|
| Tab. 7 | `cross_benchmark/analyze_table7.py`             | Cross-benchmark table + Spearman ρ           |

Raw-flow rebuild path:

```bash
python scripts/rebuild_mind2web_per_session.py --runs-root /path/to/runs/
```

## Browser-agent line-up

Three action paradigms govern how the model produces a step:

- **Browser-tooling** — discrete `search` / `navigate` / `click` function calls in the tool schema.
- **Visible-element SoM** — each page screenshot is overlaid with numbered set-of-marks; the model picks a mark index + a verb.
- **Pixel-only CUA** — pyautogui-style `(x, y)` click + keypress + scroll only; no URL primitives.

| Agent           | Backbone                 | Action paradigm           | Step budget | Served via |
|-----------------|--------------------------|---------------------------|------------:|------------|
| Fara-7B         | Qwen2.5-VL-7B            | browser-tooling           | 30           | local vLLM |
| Browser-Use     | bu-30b-a3b-preview (MoE) | browser-tooling           | 100         | local vLLM |
| OpenCUA-7B      | Qwen2.5-VL-7B            | pixel-only CUA            | 100         | local vLLM |
| SoM-GLM         | GLM-4.1V-9B-Thinking     | visible-element SoM       | 100         | local vLLM |
| SoM-GPT-5       | gpt-5                    | visible-element SoM       | 100         | OpenAI API |
| UI-TARS-1.5-7B  | Qwen2.5-VL-7B            | pixel-only CUA            | 100         | local vLLM |
| GUI-Owl-8B      | Qwen3-VL-8B              | pixel-only CUA            | 100         | local vLLM |

Each `agents/<name>/` ships the runner code (`run_agent.py`), the vLLM
serve script (`serve.sh`), and a README pointing at the upstream Hugging
Face weights. Canonical configuration matrix: `agents/registry.json`.

## Data tiers

| Tier | Content                                                                                          | Shipped here? |
|------|--------------------------------------------------------------------------------------------------|---------------|
| 1    | Per-session aggregates (six metrics, family counts, cookie **hashes** with per-campaign salt)    | yes           |
| 2    | Sanitized per-session **summary records** with redacted `Cookie:` / `Set-Cookie` headers and the JS-shim telemetry log (gzipped) | yes (~85 MB) |
| 3    | Raw mitmproxy flow bodies and cookie **values**                                                  | not released (IRB protocol) |

Tier-2 archives shipped:

- `rq1_human_vs_agent/user_study/runtime/participants_sessions.tar.xz` — 22 MB,
  ≈200 per-session bundles (capture + JS telemetry + answer + stopped.json),
  cookies redacted in place, audited to zero `Cookie:` / `Set-Cookie` entries.
- `rq2_dose_response/data/paired_sessions/` — 8 per-agent `paired_sessions.jsonl.gz`
  on WebVoyager (6.6 MB combined), one record per session with the six metrics
  and aggregate counts; the `capture_cookie_redacted` flag records the
  sanitizer pass.
- `cross_benchmark/data/paired_sessions/` — 7 per-agent `paired_sessions.jsonl.gz`
  on Mind2Web (3.8 MB combined), same schema.

The per-campaign salt defeats rainbow-table lookup of well-known cookies
(`_ga`, `fbp`, …). The salt is never released. Every paper claim is
reproducible from Tier-1 alone; Tier-2 supports independent re-aggregation
and auditing.

## Running without installing anything locally

**Google Colab.** The notebook at `notebooks/acsac_ae_colab.ipynb` clones the
repository, installs the four pinned dependencies, runs the reproduction, runs
the verification, and prints the reproduced tables. A standard CPU runtime on
the free tier is enough.

https://colab.research.google.com/github/0xk1h0/Browsing_Trackers/blob/main/artifact/notebooks/acsac_ae_colab.ipynb

**Docker.** From the repository root:

```bash
docker build -t brave-new-browsing .
docker run --rm --network none brave-new-browsing
```

The build is the only step that touches the network. The run is fully offline
and ends with `[verify] All checks passed.` To exercise a single claim instead:

```bash
docker run --rm --network none brave-new-browsing \
    bash claims/01_matched_exposure/run.sh
```

## Verification

`verify.py` regenerates nothing itself. It compares whatever the analyzers last
wrote against the reference snapshot committed under `golden/`, so it works the
same whether you cloned the repository or downloaded a tarball.

```bash
python3 verify.py                 # all modules
python3 verify.py rq1 rq3         # same module tags REPRODUCE.sh accepts
python3 verify.py --update        # authors only: refresh golden/ deliberately
```

18 checks run: 14 numeric diffs over the regenerated TSV, CSV, and JSON
outputs, and 4 structural checks on the generated figures. Numbers are
compared with a relative tolerance of 1e-6 rather than byte-for-byte, so a
different BLAS or libm on your machine does not produce a spurious failure.
Non-numeric cells must match exactly. A mismatch is reported with the file,
the row and column (or the JSON path), the reference value, and the observed
value.

Figures are checked for existence, non-zero size, and file magic, but are
deliberately not byte-compared: matplotlib embeds a creation timestamp, so two
runs of the same code produce PDFs of identical length that differ in a handful
of metadata bytes.

Exit status is 0 on success, 1 on any mismatch, and 2 on a usage error or a
missing `golden/`. On success the last line is exactly:

```
[verify] All checks passed.
```

One scope note. RQ1, RQ2, and the cross-benchmark write into their own
`expected_outputs/`, while RQ3 writes into `data/rq3_643/` and
`data/rq3_layer_isolation/`. `verify.py` checks the paths that are actually
written.

## Anonymization gate

`scripts/anonymize_check.sh` is the release gate. It refuses to pass a tree
that still contains operator-local filesystem paths, internal hostnames or
organizational markers, private RFC1918 addresses, or API-key signatures, and
it scans inside the shipped `.jsonl.gz` archives rather than skipping them.

```bash
bash scripts/anonymize_check.sh   # exit 0 iff every category is clean
```

The archive scan is delegated to `scripts/scrub_session_paths.py --check`.
That script also performs the repair:

```bash
python3 scripts/scrub_session_paths.py        # rewrite in place, idempotent
```

It rewrites absolute home-directory prefixes to `<HOME>/` in the four
provenance and debugging fields that can carry them (`agent_action_log_path`,
`agent_stdout_tail`, `agent_stderr_tail`, `error`). No analysis script reads
those fields, so the rewrite cannot move a published number; this was verified
by recomputing the per-agent exposure summary before and after and diffing the
result.

## License and citation

Our code and documentation are MIT licensed; see `../LICENSE`. The filter-list
snapshots under `pipeline/classifier/tracker_lists/` keep their upstream
licenses, which are not MIT — see
`pipeline/classifier/tracker_lists/LICENSES.md` before redistributing them.

To cite the paper or this artifact, see `../CITATION.cff`.
