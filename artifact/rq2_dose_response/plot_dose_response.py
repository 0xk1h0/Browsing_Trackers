#!/usr/bin/env python3
"""Figure 3 — dose-response: identifier exposure vs cross-site transitions.

Three panels:
  (a) median pseudo-ID count per session vs cross-site-transition bin
  (b) median context-signal count per session vs same bin
  (c) fingerprinting-triggered session share (%) vs same bin

Bins are recomputed here from the shipped per-session records — the same
binning analyze_table3.py writes — over the paper's RQ2 §7.1 pool of 7 agents
x 1,286 intent-to-treat WebVoyager sessions (9,002 sessions).  All seven agent
files must be present; a missing file is an error, not a smaller pool.

Inputs:
  data/paired_sessions/*.jsonl.gz    # one JSON session per line, 7 agents

Outputs:
  figures/fig3_dose_response.pdf
  figures/fig3_dose_response.png
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from statistics import median

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
SESSIONS = HERE / "data" / "paired_sessions"
OUT_PDF = HERE / "figures" / "fig3_dose_response.pdf"
OUT_PNG = HERE / "figures" / "fig3_dose_response.png"

# The paper's seven agents; the file names are the shipped per-agent captures.
AGENT_FILES = {
    "Browser-Use": "browseruse_bu30b_paired_v1_paired_sessions.jsonl.gz",
    "Fara-7B": "e1_fara7b_paired_v1_paired_sessions.jsonl.gz",
    "GUI-Owl-8B": "e1_guiowl_paired_v1_paired_sessions.jsonl.gz",
    "OpenCUA-7B": "e1_opencua_paired_v1_paired_sessions.jsonl.gz",
    "SoM-GLM": "e1_somglm_paired_v1_paired_sessions.jsonl.gz",
    "SoM-GPT-5": "e1_somgpt5_paired_v1_paired_sessions.jsonl.gz",
    "UI-TARS": "e1_uitars_paired_v1_paired_sessions.jsonl.gz",
}

# (label, low, high) — high None means open-ended.
BINS = [("0", 0, 0), ("1", 1, 1), ("2", 2, 2),
        ("3-5", 3, 5), ("6-10", 6, 10), ("11+", 11, None)]


def load_agent_sessions(root: Path = SESSIONS) -> list[dict]:
    """Every condition == "agent" session across all seven agent files."""
    out = []
    for agent, name in sorted(AGENT_FILES.items()):
        path = root / name
        if not path.exists():
            raise FileNotFoundError(
                f"missing per-session data for {agent}: {path}. "
                f"All {len(AGENT_FILES)} agent files are required; the paper's "
                f"pool is 7 agents x 1,286 ITT sessions.")
        with gzip.open(path, "rt") as f:
            for line in f:
                if not line.strip():
                    continue
                s = json.loads(line)
                if s.get("condition") == "agent":
                    out.append(s)
    return out


def bin_rows(sessions: list[dict]) -> list[dict]:
    rows = []
    for label, lo, hi in BINS:
        grp = [s for s in sessions
               if lo <= s["nav_cross_site_transition_count"]
               and (hi is None or s["nav_cross_site_transition_count"] <= hi)]
        if not grp:
            continue
        leak = lambda k: median(s["info_leakage_counts"][k] for s in grp)  # noqa: E731
        rows.append({
            "bin": label,
            "n": len(grp),
            "med_pseudo": leak("pseudonymous_identifier"),
            "med_ctx": leak("context_signal"),
            "fp_pct": 100 * sum(s["fingerprinting_api_call_count"] > 0
                                for s in grp) / len(grp),
        })
    return rows


def main():
    rows = bin_rows(load_agent_sessions())

    bins = [r["bin"] for r in rows]
    med_pseudo = [float(r["med_pseudo"]) for r in rows]
    med_ctx = [float(r["med_ctx"]) for r in rows]
    fp_pct = [float(r["fp_pct"]) for r in rows]
    n = [int(r["n"]) for r in rows]

    fig, axes = plt.subplots(1, 3, figsize=(11, 3.0), constrained_layout=True)

    bar_kw = dict(color="#2c3e50", alpha=0.85, edgecolor="black", linewidth=0.5)
    x = list(range(len(bins)))

    axes[0].bar(x, med_pseudo, **bar_kw)
    axes[0].set_xticks(x); axes[0].set_xticklabels(bins)
    axes[0].set_xlabel("cross-site transition bin")
    axes[0].set_ylabel("median pseudonymous IDs / session")
    axes[0].set_title("(a) pseudo-ID exposure")
    axes[0].grid(axis="y", alpha=0.3)

    axes[1].bar(x, med_ctx, **bar_kw)
    axes[1].set_xticks(x); axes[1].set_xticklabels(bins)
    axes[1].set_xlabel("cross-site transition bin")
    axes[1].set_ylabel("median context signals / session")
    axes[1].set_title("(b) context-signal exposure")
    axes[1].grid(axis="y", alpha=0.3)

    axes[2].bar(x, fp_pct, color="#c0392b", alpha=0.85, edgecolor="black", linewidth=0.5)
    axes[2].set_xticks(x); axes[2].set_xticklabels(bins)
    axes[2].set_xlabel("cross-site transition bin")
    axes[2].set_ylabel("sessions w/ FP trigger (%)")
    axes[2].set_title("(c) fingerprinting incidence")
    axes[2].set_ylim(0, max(fp_pct) * 1.15)
    axes[2].grid(axis="y", alpha=0.3)

    # Annotate n above each bar in panel (a)
    for xi, ni in zip(x, n):
        axes[0].text(xi, med_pseudo[xi] * 1.02 + 5, f"n={ni}",
                     ha="center", va="bottom", fontsize=7, color="#555555")

    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PDF)
    fig.savefig(OUT_PNG, dpi=150)
    print(f"[F3] -> {OUT_PDF.relative_to(HERE)} (+ .png)")


if __name__ == "__main__":
    main()
