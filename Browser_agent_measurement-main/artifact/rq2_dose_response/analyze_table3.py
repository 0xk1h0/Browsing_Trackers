#!/usr/bin/env python3
"""Table 3 — action-space affordance bins (dose-response binning).

Agent sessions are binned by `nav_cross_site_transition_count` into the bins
the paper uses (0, 1, 2, 3-5, 6-10, 11+).  For each bin we report n, the
population share, the per-session medians of unique tracker hosts,
pseudonymous identifiers, context signals and device/network signals, and the
share of sessions that triggered at least one fingerprinting API call.

Everything is derived here from the shipped per-session records: the paper's
RQ2 §7.1 pool is exactly 7 agents x 1,286 intent-to-treat WebVoyager sessions
(9,002 sessions).  All seven agent files must be present; a missing file is an
error rather than a silently smaller pool.

Inputs:
  data/paired_sessions/*.jsonl.gz    # one JSON session per line, 7 agents

Outputs:
  expected_outputs/table3.tsv
  stdout                             # human-readable
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
SESSIONS = HERE / "data" / "paired_sessions"
OUT = HERE / "expected_outputs" / "table3.tsv"

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

COLUMNS = ["bin", "n", "pop_pct", "med_trk", "med_pseudo",
           "med_ctx", "med_dev", "fp_pct"]


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
    total = len(sessions)
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
            "pop_pct": round(100 * len(grp) / total, 1),
            # `unique_tracker_domains` is the per-session unique tracker-host count.
            "med_trk": median(s["unique_tracker_domains"] for s in grp),
            "med_pseudo": leak("pseudonymous_identifier"),
            "med_ctx": leak("context_signal"),
            "med_dev": leak("device_network_signal"),
            "fp_pct": round(
                100 * sum(s["fingerprinting_api_call_count"] > 0 for s in grp)
                / len(grp), 1),
        })
    return rows


def num(v) -> str:
    """Medians land on .5 for even-sized bins; don't hide that behind rounding."""
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"


def main():
    sessions = load_agent_sessions()
    rows = bin_rows(sessions)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fmt = "{:>5s} {:>8s} {:>9s} {:>9s} {:>10s} {:>10s} {:>10s} {:>9s}"
    print(f"pool: {len(sessions)} agent sessions "
          f"from {len(AGENT_FILES)} agents")
    print(fmt.format("bin", "n", "pop_pct",
                     "med_trk", "med_pseudo", "med_ctx", "med_dev", "fp_pct"))
    print("-" * 85)
    for r in rows:
        print(fmt.format(
            r["bin"], str(r["n"]), f"{r['pop_pct']:.1f}",
            num(r["med_trk"]), num(r["med_pseudo"]),
            num(r["med_ctx"]), num(r["med_dev"]),
            f"{r['fp_pct']:.1f}%",
        ))

    with OUT.open("w") as f:
        f.write("\t".join(COLUMNS) + "\n")
        for r in rows:
            f.write("\t".join(
                str(r[k]) if k in ("bin", "n") else
                f"{r[k]:.1f}" if k in ("pop_pct", "fp_pct") else num(r[k])
                for k in COLUMNS) + "\n")
    print(f"\n[T3] -> {OUT.relative_to(HERE)}")


def _selfcheck():
    """The pool and the binning must stay exactly as the paper defines them."""
    sessions = load_agent_sessions()
    assert len(sessions) == 9002, len(sessions)
    rows = bin_rows(sessions)
    assert [r["bin"] for r in rows] == [b[0] for b in BINS]
    assert sum(r["n"] for r in rows) == len(sessions)
    assert abs(sum(r["pop_pct"] for r in rows) - 100) < 0.5
    print("selfcheck ok")


if __name__ == "__main__":
    import sys
    _selfcheck() if "--selfcheck" in sys.argv else main()
