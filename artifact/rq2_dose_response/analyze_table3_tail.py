#!/usr/bin/env python3
"""Paper Table 3 - per-agent action-space affordances and deep-navigation tail share.

The affordance columns (which primitives an agent exposes, and its architecture
class) are configuration, read from agents/registry.json. The two tail-share
columns are measured, computed here from the shipped per-session records over
the paper's pool of seven agents and 1,286 intent-to-treat sessions each.

    Bins 0-1    share of sessions with at most one cross-site transition
    Bins 11+    share of sessions with eleven or more

Inputs:
  ../agents/registry.json
  data/paired_sessions/*_paired_v1_paired_sessions.jsonl.gz

Outputs:
  expected_outputs/table3_tail_share.tsv
  stdout
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "paired_sessions"
REGISTRY = HERE.parent / "agents" / "registry.json"
OUT = HERE / "expected_outputs" / "table3_tail_share.tsv"

# file stem -> the label the paper uses
AGENTS = {
    "e1_fara7b": "Fara-7B",
    "browseruse_bu30b": "Browser-Use",
    "e1_somglm": "SoM-GLM",
    "e1_somgpt5": "SoM-GPT-5",
    "e1_guiowl": "GUI-Owl-8B",
    "e1_opencua": "OpenCUA-7B",
    "e1_uitars": "UI-TARS",
}
# the paper's label -> the registry key
REGISTRY_KEY = {
    "Fara-7B": "fara-7b",
    "Browser-Use": "browser-use",
    "SoM-GLM": "som-glm",
    "SoM-GPT-5": "som-gpt-5",
    "GUI-Owl-8B": "gui-owl-8b",
    "OpenCUA-7B": "opencua-7b",
    "UI-TARS": "ui-tars-1.5-7b",
}
PARADIGM = {
    "browser-tooling": "Browser-tooling",
    "visible-element-som": "Visible-element",
    "pixel-only-cua": "Pixel-only CUA",
}
# Whether the agent exposes a named search / navigate primitive, and the minimum
# number of primitive actions one cross-site hop costs it. This is action-space
# configuration described in Section 5.2.1, not a measurement.
AFFORDANCE = {
    "Fara-7B": ("yes", "yes", "1"),
    "Browser-Use": ("yes", "yes", "1"),
    "SoM-GLM": ("yes", "yes", "1"),
    "SoM-GPT-5": ("yes", "yes", "1"),
    "GUI-Owl-8B": ("no", "no", "1-2"),
    "OpenCUA-7B": ("no", "no", ">=3"),
    "UI-TARS": ("no", "no", ">=3"),
}


def transitions(path: Path) -> list[int]:
    out = []
    with gzip.open(path, "rt", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("condition") != "agent":
                continue
            out.append(int(rec.get("nav_cross_site_transition_count", 0) or 0))
    return out


def main() -> int:
    if not DATA.is_dir():
        print(f"ERROR: per-session data not found at {DATA}", file=sys.stderr)
        return 2
    registry = json.loads(REGISTRY.read_text())["agents"]

    rows = []
    for stem, label in AGENTS.items():
        path = DATA / f"{stem}_paired_v1_paired_sessions.jsonl.gz"
        if not path.is_file():
            print(f"ERROR: missing per-session file for {label}: {path}", file=sys.stderr)
            return 2
        xs = transitions(path)
        if not xs:
            print(f"ERROR: no agent sessions parsed for {label}", file=sys.stderr)
            return 2
        search, navigate, cost = AFFORDANCE[label]
        rows.append(
            {
                "agent": label,
                "search": search,
                "navigate": navigate,
                "nav_cost": cost,
                "bins_0_1_pct": round(100.0 * sum(1 for x in xs if x <= 1) / len(xs), 1),
                "bins_11_plus_pct": round(100.0 * sum(1 for x in xs if x >= 11) / len(xs), 1),
                "architecture": PARADIGM.get(
                    registry[REGISTRY_KEY[label]].get("action_paradigm", ""), "?"
                ),
                "n_sessions": len(xs),
            }
        )

    rows.sort(key=lambda r: -r["bins_11_plus_pct"])
    cols = ["agent", "search", "navigate", "nav_cost", "bins_0_1_pct",
            "bins_11_plus_pct", "architecture", "n_sessions"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            f.write("\t".join(str(r[c]) for c in cols) + "\n")

    total = sum(r["n_sessions"] for r in rows)
    print(f"Table 3 - affordances and deep-navigation tail share, {total} agent sessions\n")
    head = (f"{'Agent':14s} {'Search':>7s} {'Nav':>5s} {'Cost':>6s} "
            f"{'Bins 0-1':>9s} {'Bins 11+':>9s}  Architecture")
    print(head)
    print("-" * len(head))
    for r in rows:
        print(f"{r['agent']:14s} {r['search']:>7s} {r['navigate']:>5s} "
              f"{r['nav_cost']:>6s} {r['bins_0_1_pct']:8.1f}% {r['bins_11_plus_pct']:8.1f}%  "
              f"{r['architecture']}")
    print(f"\n[T3] -> {OUT.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
