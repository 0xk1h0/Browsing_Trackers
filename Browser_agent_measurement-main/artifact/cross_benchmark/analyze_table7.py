#!/usr/bin/env python3
"""Table 7 — cross-benchmark default-condition exposure, WebVoyager vs Mind2Web.

Both benchmark columns are derived from the shipped per-session records;
neither is read from a pre-aggregated summary or a hardcoded constant. Only
records with condition == "agent" are counted.

Per agent (7 agents paired across the two benchmarks):
  X-site (mean)     mean of nav_cross_site_transition_count
  Pseudo IDs (mean) mean of info_leakage_counts["pseudonymous_identifier"]
  delta %           (Mind2Web - WebVoyager) / WebVoyager * 100

Plus the agent-level Spearman rank correlation of the pseudo-ID means across
the seven paired agents: it tests whether the per-agent ordering carries over
to a different task pool.

Inputs:
  ../rq2_dose_response/data/paired_sessions/*.jsonl.gz  # WebVoyager, 7 x 1,286
  data/paired_sessions/*.jsonl.gz                       # Mind2Web,   7 x 600

Outputs:
  expected_outputs/table7.tsv             # per-agent rows (cross-site and pseudo-ID)
  expected_outputs/mind2web_spearman.json
  stdout                                  # human-readable, incl. x-site columns
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
WV_DATA = HERE.parent / "rq2_dose_response" / "data" / "paired_sessions"
M2W_DATA = HERE / "data" / "paired_sessions"
OUT_TSV = HERE / "expected_outputs" / "table7.tsv"
OUT_JSON = HERE / "expected_outputs" / "mind2web_spearman.json"

# The two run families use different file-naming conventions for the same
# agent, so the pairing is spelled out rather than inferred.
# paper label -> (WebVoyager file stem, Mind2Web file stem)
AGENT_FILES = {
    "Browser-Use": ("browseruse_bu30b_paired_v1", "browseruse_bu30b_mind2web_paired_v1"),
    "Fara-7B": ("e1_fara7b_paired_v1", "mind2web_fara7b_v1"),
    "GUI-Owl-8B": ("e1_guiowl_paired_v1", "mind2web_guiowl_v1"),
    "OpenCUA-7B": ("e1_opencua_paired_v1", "mind2web_opencua_v1"),
    "SoM-GLM": ("e1_somglm_paired_v1", "mind2web_somglm_v1"),
    "SoM-GPT-5": ("e1_somgpt5_paired_v1", "mind2web_somgpt5_v1"),
    "UI-TARS": ("e1_uitars_paired_v1", "mind2web_uitars_v1"),
}
SUFFIX = "_paired_sessions.jsonl.gz"
WV_ITT_PER_AGENT = 1286  # intent-to-treat sessions per agent on WebVoyager

# The paper's Table 7 prints both a cross-site column and a pseudo-ID column, so
# both are written here. Reviewers compare the cross-site columns against the
# per-agent shifts quoted in Section 8.
COLUMNS = ["agent", "n_sessions",
           "webvoyager_mean_xsite", "mind2web_mean_xsite", "xsite_delta_pct",
           "webvoyager_mean_pseudo", "mind2web_mean_pseudo", "delta_pct"]


def load_agent(path: Path) -> list[dict]:
    with gzip.open(path, "rt") as f:
        return [r for r in map(json.loads, f) if r.get("condition") == "agent"]


def load_side(data_dir: Path, stems: dict[str, str], tag: str) -> dict[str, list[dict]]:
    """Load every expected agent from one benchmark, failing loudly on drift."""
    if not data_dir.is_dir():
        sys.exit(f"[T7] FATAL: {tag} session directory not found: {data_dir}")
    missing = {lab: s for lab, s in stems.items()
               if not (data_dir / (s + SUFFIX)).is_file()}
    if missing:
        sys.exit(f"[T7] FATAL: {tag} agent files missing from {data_dir}: "
                 f"{sorted(missing.items())}")

    sessions = {lab: load_agent(data_dir / (s + SUFFIX)) for lab, s in stems.items()}
    empty = sorted(lab for lab, s in sessions.items() if not s)
    if empty:
        sys.exit(f"[T7] FATAL: no condition=='agent' records for {tag} {empty}")

    # An unmapped file holding agent sessions would silently leave an agent out
    # of the pool, so refuse to run rather than report a partial table.
    expected = {s + SUFFIX for s in stems.values()}
    extra = sorted(p.name for p in data_dir.glob("*" + SUFFIX)
                   if p.name not in expected and load_agent(p))
    if extra:
        sys.exit(f"[T7] FATAL: unmapped {tag} agent session files in {data_dir}: "
                 f"{extra}. Add them to AGENT_FILES or remove them.")
    return sessions


def means(sessions: list[dict]) -> tuple[float, float]:
    xsite = np.array([s["nav_cross_site_transition_count"] for s in sessions], float)
    pseudo = np.array([s["info_leakage_counts"]["pseudonymous_identifier"]
                       for s in sessions], float)
    return float(xsite.mean()), float(pseudo.mean())


def main() -> None:
    wv = load_side(WV_DATA, {l: s[0] for l, s in AGENT_FILES.items()}, "WebVoyager")
    m2w = load_side(M2W_DATA, {l: s[1] for l, s in AGENT_FILES.items()}, "Mind2Web")

    off_pool = {lab: len(s) for lab, s in wv.items() if len(s) != WV_ITT_PER_AGENT}
    if off_pool:
        sys.exit(f"[T7] FATAL: WebVoyager pool is not {WV_ITT_PER_AGENT} ITT "
                 f"sessions per agent: {sorted(off_pool.items())}")

    rows, paired = [], []
    for label in sorted(AGENT_FILES):
        wv_x, wv_p = means(wv[label])
        m2w_x, m2w_p = means(m2w[label])
        rows.append({
            "agent": label,
            "n_sessions": len(m2w[label]),
            "webvoyager_mean_pseudo": round(wv_p, 1),
            "mind2web_mean_pseudo": round(m2w_p, 1),
            "delta_pct": round((m2w_p - wv_p) / wv_p * 100, 1),
            "wv_xsite": wv_x,
            "m2w_xsite": m2w_x,
            "xsite_delta_pct": (m2w_x - wv_x) / wv_x * 100,
            # rounded copies for the TSV, which the claims compare against
            "webvoyager_mean_xsite": round(wv_x, 2),
            "mind2web_mean_xsite": round(m2w_x, 2),
        })
        paired.append({"agent": label, "wv": wv_p, "m2w": m2w_p})

    rho = float(stats.spearmanr([p["wv"] for p in paired],
                                [p["m2w"] for p in paired]).statistic)

    n_wv = sum(len(s) for s in wv.values())
    n_m2w = sum(len(s) for s in m2w.values())
    print(f"Table 7 — cross-benchmark exposure, {len(rows)} agents, "
          f"{n_wv} WebVoyager + {n_m2w} Mind2Web agent sessions\n")
    head = "{:<13s} {:>7s} {:>7s} {:>8s}   {:>8s} {:>8s} {:>8s}"
    print(head.format("Agent", "X.WV", "X.M2W", "d%", "Pseu.WV", "Pseu.M2W", "d%"))
    print("-" * 68)
    for r in rows:
        print("{agent:<13s} {wv_xsite:7.2f} {m2w_xsite:7.2f} {xsite_delta_pct:+7.1f}%"
              "   {webvoyager_mean_pseudo:8.1f} {mind2web_mean_pseudo:8.1f} "
              "{delta_pct:+7.1f}%".format(**r))

    n_up = sum(1 for r in rows if r["m2w_xsite"] > r["wv_xsite"])
    print(f"\nCross-site transitions higher on Mind2Web: {n_up} of {len(rows)} agents"
          f" ({', '.join(r['agent'] for r in rows if r['m2w_xsite'] > r['wv_xsite'])})")
    print(f"Spearman rho, pseudo-ID means (WV vs M2W, n={len(paired)} agents): {rho:.3f}")

    OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_TSV.open("w") as f:
        f.write("\t".join(COLUMNS) + "\n")
        for r in rows:
            row = dict(r)
            row["xsite_delta_pct"] = round(row["xsite_delta_pct"], 1)
            f.write("\t".join(str(row[c]) for c in COLUMNS) + "\n")

    with OUT_JSON.open("w") as f:
        json.dump({"n_agents_paired": len(paired),
                   "spearman_rho": rho,
                   "paired": paired}, f, indent=2)
    print(f"\n[T7] -> {OUT_TSV.relative_to(HERE)} + {OUT_JSON.name}")


if __name__ == "__main__":
    main()
