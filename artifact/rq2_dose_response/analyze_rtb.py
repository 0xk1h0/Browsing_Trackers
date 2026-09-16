#!/usr/bin/env python3
"""RTB-broadcast audit on the 50-session upper-quartile Fara-7B sample (§6.2).

Reports the headline numbers cited in the paper:
  - total RTB requests observed
  - share of RTB requests carrying an identifying cookie
  - top RTB-endpoint hosts

The 50-session sample is drawn from the upper quartile of per-session
pseudonymous-identifier count (uniform random over the top-25%), seed=42.
Pre-computed from the raw mitmproxy flows by `scripts/rebuild_rtb_50sessions.py`.

Inputs:
  data/rtb_50sessions_summary.json   # aggregate
  data/rtb_50sessions.csv            # per-session breakdown

Outputs:
  expected_outputs/rtb_summary.json  # copy of input with derived fields
  stdout                             # human-readable
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
SUM = HERE / "data" / "rtb_50sessions_summary.json"
PER = HERE / "data" / "rtb_50sessions.csv"
OUT = HERE / "expected_outputs" / "rtb_summary.json"


def main():
    with SUM.open() as f:
        s = json.load(f)

    per_session_rtb = []
    if PER.exists():
        with PER.open() as f:
            for r in csv.DictReader(f):
                # column name might be 'rtb_requests' or similar; be defensive
                for k in ("rtb_requests", "rtb_count", "n_rtb", "total"):
                    if k in r and r[k]:
                        try:
                            per_session_rtb.append(int(float(r[k])))
                            break
                        except ValueError:
                            pass

    n = s["n_sessions_with_capture"]
    total = s["total_rtb_requests"]
    id_share = s["identifying_cookie_share_pct"]

    print("=== RTB broadcast (50-session upper-quartile Fara-7B) ===")
    print(f"  Sessions:            {n}")
    print(f"  Total RTB requests:  {total:,}")
    print(f"  Identifying-cookie share: {id_share:.1f}%")
    if per_session_rtb:
        per_session_rtb.sort()
        print(f"  Median RTB requests/session: {int(median(per_session_rtb)):,}")
        print(f"  Mean   RTB requests/session: {sum(per_session_rtb)/len(per_session_rtb):.1f}")
    print()
    print("  Top RTB-endpoint hosts (count):")
    for host, count in s["top_10_rtb_hosts"][:10]:
        print(f"    {host:<42s} {count:>8,}")

    derived = dict(s)
    if per_session_rtb:
        derived["median_rtb_per_session"] = int(median(per_session_rtb))
        derived["mean_rtb_per_session"] = round(
            sum(per_session_rtb) / len(per_session_rtb), 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        json.dump(derived, f, indent=2)
    print(f"\n[RTB] -> {OUT.relative_to(HERE)}")


if __name__ == "__main__":
    main()
