# Agents

| Agent | Paradigm | Backbone | Served via | Step budget |
|---|---|---|---|---|
| Fara-7B | browser-tooling | Qwen2.5-VL-7B | local vLLM | 30 |
| Browser-Use | browser-tooling | bu-30b-a3b-preview (MoE) | local vLLM | 100 |
| OpenCUA-7B | pixel-only CUA | Qwen2.5-VL-7B | local vLLM | 100 |
| SoM-GLM | visible-element SoM | GLM-4.1V-9B-Thinking | local vLLM | 100 |
| SoM-GPT-5 | visible-element SoM | gpt-5 | OpenAI API | 100 |
| UI-TARS-1.5-7B | pixel-only CUA | Qwen2.5-VL-7B | local vLLM | 100 |
| GUI-Owl-8B | pixel-only CUA | Qwen3-VL-8B | local vLLM | 100 |

Canonical runtime config: `registry.json`.

## Three paradigms

- **Browser-tooling** — discrete `search`/`navigate`/`click` tool calls.
- **Visible-element SoM** — set-of-marks overlay; model picks a mark index.
- **Pixel-only CUA** — pyautogui-style `(x,y)` clicks; no URL primitives.

## RQ3 ablation

`FARA_DISABLED_ACTIONS` / `BU_DISABLED_ACTIONS` env vars; see
`../rq3_ablation/interventions/schema_patches/`.

## Not shipped

Model weights, OpenAI API keys, browser_use pip wheel. Each per-agent
README documents the upstream Hugging Face / pip source.

## What ships per agent, and what an evaluator can actually run

Six agents are driven by a wrapper in this directory. Fara-7B is driven by the
upstream `fara-cli` entry point instead, so it ships `serve.sh` and its registry
entry but no `run_agent.py`; the registry's `command_template` records the exact
invocation. SoM-GPT-5 runs against the OpenAI API and therefore ships
`run_agent.py` but no `serve.sh`.

`serve.sh` runs from the model-serving environment (`requirements-serve.txt`,
see `../INSTALL.md`); `run_agent.py` runs from the harness environment that
`install.sh --full` creates. The two are separate virtual environments.

| Agent | `serve.sh` | `run_agent.py` | driven by |
|---|---|---|---|
| Fara-7B | yes | no | upstream `fara-cli` |
| Browser-Use | yes | yes | wrapper |
| OpenCUA-7B | yes | yes | wrapper |
| SoM-GLM | yes | yes | wrapper |
| SoM-GPT-5 | no | yes | wrapper, OpenAI API |
| UI-TARS-1.5-7B | yes | yes | wrapper |
| GUI-Owl-8B | yes | yes | wrapper |

Running any agent end to end needs a GPU, model weights, a trusted mitmproxy CA,
and Playwright, and it measures the live web, which has changed since the
March to April 2026 collection window. That path is released for inspection and
single-session demonstration and is documented in `../INSTALL.md`; it is not
part of the evaluation this artifact proposes. Everything the paper's tables
rest on is reproduced from the released per-session data without it.

Note on step budgets: `registry.json` records the budget actually used per
agent. Fara-7B ran at 30 rounds, the other six at 100 steps. The paper's
Table 9 prints 100 for all seven, which is corrected in the camera-ready.
