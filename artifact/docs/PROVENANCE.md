# Data provenance

How the data shipped in this artifact was collected, where, when, and by what
instrument. This file backs the `provenance` field of `metadata.toml`.

## What was measured

Third-party tracking exposure produced by completing a web task, measured twice
over: once with a human performing the task, and once with an autonomous
browser agent performing the same task from the same starting page.

Exposure is recorded per session as six quantities: pseudonymous identifiers
transmitted, unique tracker hosts contacted, cross-site transitions, calls to
fingerprinting APIs, behavioural signals, and device or network signals.

## When

| Campaign | Window |
|---|---|
| Agent collection (WebVoyager and Online Mind2Web) | 22 March to 13 April 2026 |
| Human user study | conducted within the same period, in a controlled lab setting |
| Tracker filter-list snapshots | frozen 15 March 2026, seven lists, 181,955 hosts |

Filter lists were frozen before collection began so that classification is
stable across the whole campaign and does not drift with upstream list updates.

## Where

All sessions were driven from a single controlled environment. Agents were
served locally on dedicated NVIDIA RTX PRO 6000 Blackwell GPUs (96 GB VRAM),
except SoM-GPT-5, which used the OpenAI API. Participants drove a fully
instrumented Chromium on the study server through an HTML5 remote-desktop
client, so that the human and agent arms shared one capture path.

Requests went to the live public web. No site was modified, no account was
used, and no transaction was completed. Task pools are the public WebVoyager
benchmark (643 tasks; a fixed 10-task subset for the matched study) and Online
Mind2Web (300 tasks for the cross-benchmark check).

## Cohorts and volumes

| Arm | Sessions |
|---|---|
| Human participants | 220 (22 participants x 10 tasks) |
| Agents, matched 10-task pool | 140 (7 agents x 10 tasks x 2 repetitions) |
| Agents, 643-task WebVoyager pool | 9,002 (7 agents x 1,286 intent-to-treat sessions) |
| Agents, Online Mind2Web pool | 4,200 (7 agents x 600 sessions) |
| Action-space ablation | 5,787 sessions across conditions on two agents |

## Instrument

Five stages, all released under `pipeline/`:

1. **Capture.** A per-session mitmdump proxy with a locally trusted CA
   terminates TLS and logs every request and response, while an injected
   JavaScript shim records calls to Canvas, WebGL, AudioContext, screen, and
   navigator.
2. **Classification.** Each host is matched against seven filter lists, then
   mapped to one of 71 fine-grained vendor families and to one of the named
   super-categories used in the paper.
3. **Aggregation.** Each session is reduced to the six metrics above, one row
   per session. This is the Tier-1 data every analysis script consumes.
4. **Analysis.** Per-research-question statistical scripts under `rq1_*`,
   `rq2_*`, `rq3_*`, and `cross_benchmark/`.
5. **Release gate.** `scripts/anonymize_check.sh` refuses to pass a tree that
   still contains local paths, internal hostnames, private addresses, API-key
   signatures, or operator-local paths inside the shipped archives.

Each session used a freshly initialized browser profile, identically for both
cohorts, and kept that profile's state for the duration of the session.

## What is released, and what is not

| Tier | Content | Released |
|---|---|---|
| 1 | Per-session aggregates, family counts, salted cookie hashes | yes |
| 2 | Sanitized per-session summary records and JS telemetry, cookie headers removed | yes |
| 3 | Raw HTTP flow bodies and raw cookie values | no, withheld under the IRB protocol |

The cookie hash salt is per campaign and is never released, which prevents a
dictionary lookup of well-known cookie names against the published hashes.
Every claim in the paper is derivable from Tier 1 alone.

## Known provenance caveats

- The UI-TARS agent has two collection runs. The per-session data shipped here
  is the first run (1,286 sessions per agent, matching the pool size the paper
  reports). One row of the paper's Table 2 was taken from the second, longer
  run. `CLAIMS.md` records the resulting discrepancy and the value the
  camera-ready uses.
- Provenance and debugging fields in the per-session records
  (`agent_action_log_path`, `agent_stdout_tail`, `agent_stderr_tail`, `error`)
  originally contained the collecting machine's absolute paths. These were
  rewritten to a `<HOME>/` placeholder before release with
  `scripts/scrub_session_paths.py`. No analysis script reads those fields, and
  the rewrite was verified to leave every published metric unchanged.

## Ethics

See [ETHICS.md](ETHICS.md) for consent, compensation, data minimization, and
the IRB scope of the human study.
