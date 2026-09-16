#!/usr/bin/env python3
"""CMP consent decision analysis (§6 — 78.3% accept rate headline).

Reports, for each agent that encountered a CMP banner:
  - sessions with CMP network signal
  - explicit-accept count
  - explicit-reject count
  - aggregate accept_pct_of_decided

Also reports the cross-agent aggregate accept rate (the 78.3% number).

Inputs:
  data/consent_decisions_v2.json    # per-agent CMP outcome
  data/accept_heuristic_validation.json  # accept-rule validation

Outputs:
  expected_outputs/cmp_summary.tsv
  stdout
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "consent_decisions_v2.json"
VALID = HERE / "data" / "accept_heuristic_validation.json"
OUT = HERE / "expected_outputs" / "cmp_summary.tsv"


def main():
    with SRC.open() as f:
        decisions = json.load(f)

    print("=== CMP decisions per agent ===")
    fmt = "{:<18s} {:>10s} {:>10s} {:>8s} {:>8s} {:>14s}"
    print(fmt.format("Agent", "n_sessions", "n_w_CMP", "accept", "reject",
                     "accept_%_decided"))
    print("-" * 78)

    tot_acc = 0
    tot_rej = 0
    tot_decided = 0
    tot_sessions = 0
    tot_cmp = 0
    rows = []

    for agent, d in decisions.items():
        n = d["n_sessions"]
        n_cmp = d["n_with_cmp_network"]
        acc = d["explicit_accept"]
        rej = d["explicit_reject"]
        decided = acc + rej
        pct_dec = (acc / decided * 100) if decided > 0 else float("nan")

        tot_sessions += n
        tot_cmp += n_cmp
        tot_acc += acc
        tot_rej += rej
        tot_decided += decided

        rows.append((agent, n, n_cmp, acc, rej, pct_dec))
        print(fmt.format(
            agent, str(n), str(n_cmp), str(acc), str(rej),
            (f"{pct_dec:.1f}%" if decided > 0 else "n/a")))

    aggregate = (tot_acc / tot_decided * 100) if tot_decided > 0 else float("nan")
    print("-" * 78)
    print(fmt.format(
        "AGGREGATE", str(tot_sessions), str(tot_cmp),
        str(tot_acc), str(tot_rej),
        f"{aggregate:.1f}%"))
    print()
    print(f"Aggregate accept rate (of {tot_decided} decided CMP encounters): "
          f"{aggregate:.1f}%")
    print(f"CMP-encountered sessions: {tot_cmp} of {tot_sessions} "
          f"({tot_cmp/tot_sessions*100:.1f}%)")

    if VALID.exists():
        with VALID.open() as f:
            v = json.load(f)
        print()
        print("=== Accept-heuristic validation (median pseudo-ID pre/post CMP) ===")
        afmt = "{:<18s} {:>5s} {:>10s} {:>10s} {:>10s}"
        print(afmt.format("Agent", "n", "med_pre", "med_post", "med_ratio"))
        print("-" * 60)
        for agent, d in v.items():
            p = d.get("processed", {})
            if p.get("n"):
                print(afmt.format(
                    agent, str(p["n"]),
                    f"{p.get('median_pre',0):.1f}",
                    f"{p.get('median_post',0):.1f}",
                    f"{p.get('median_ratio',0):.1f}"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        f.write("agent\tn_sessions\tn_with_cmp\texplicit_accept\texplicit_reject"
                "\taccept_pct_of_decided\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
        f.write(f"AGGREGATE\t{tot_sessions}\t{tot_cmp}\t{tot_acc}\t{tot_rej}"
                f"\t{aggregate:.1f}\n")
    print(f"\n[CMP] -> {OUT.relative_to(HERE)}")


if __name__ == "__main__":
    main()
