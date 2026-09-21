#!/usr/bin/env python3
"""RTB broadcast on the 50-session upper-quartile Fara-7B sample (Section 6.2).

Computed from data/rtb_50sessions.csv: one row per session with the number of
requests to real-time-bidding endpoints and how many of them carried an
identifying cookie. The sample is the top quartile of Fara-7B sessions by
pseudonymous-identifier count. Reports, as the paper does,

  total RTB requests across the sample,
  median, mean and standard deviation (n-1) of RTB requests per session,
  the interquartile range, using the Weibull quantile convention
      (numpy method="weibull"), which is the convention behind the printed
      22 to 492; the numpy default (linear) gives 28 to 482.5,
  the pooled share of RTB requests carrying an identifying cookie, and the
      per-session median of that share over sessions with RTB traffic.

The ranking of RTB endpoint hosts cannot be recomputed from the CSV, which has
no per-host column. It is read as recorded from data/rtb_50sessions_summary.json
and printed for reference only.

Inputs:
  data/rtb_50sessions.csv
  data/rtb_50sessions_summary.json   (host ranking only)
Outputs:
  expected_outputs/rtb_summary.json
  stdout
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PER = HERE / "data" / "rtb_50sessions.csv"
HOSTS = HERE / "data" / "rtb_50sessions_summary.json"
OUT = HERE / "expected_outputs" / "rtb_summary.json"


def main() -> None:
    with PER.open(newline="") as f:
        rows = list(csv.DictReader(f))
    rtb = np.array([int(r["rtb_requests"]) for r in rows])
    ident = np.array([int(r["rtb_with_identifying_cookie"]) for r in rows])
    with_traffic = rtb > 0
    per_session_share = 100.0 * ident[with_traffic] / rtb[with_traffic]
    q25, q75 = np.percentile(rtb, [25, 75], method="weibull")

    summary = {
        "n_sessions": int(len(rtb)),
        "total_rtb_requests": int(rtb.sum()),
        "rtb_with_identifying_cookie": int(ident.sum()),
        "identifying_cookie_share_pct": round(100.0 * ident.sum() / rtb.sum(), 1),
        "per_session_median_identifying_share_pct": round(float(np.median(per_session_share)), 1),
        "sessions_with_rtb_traffic": int(with_traffic.sum()),
        "median_rtb_per_session": float(np.median(rtb)),
        "mean_rtb_per_session": round(float(rtb.mean()), 1),
        "sd_rtb_per_session": round(float(rtb.std(ddof=1)), 1),
        "iqr_rtb_per_session_weibull": [round(float(q25), 1), round(float(q75), 1)],
    }
    with HOSTS.open() as f:
        summary["top_10_rtb_hosts_recorded"] = json.load(f)["top_10_rtb_hosts"][:10]

    s = summary
    print("=== RTB broadcast (50-session upper-quartile Fara-7B sample) ===")
    print(f"  Sessions:                     {s['n_sessions']} ({s['sessions_with_rtb_traffic']} with RTB traffic)")
    print(f"  Total RTB requests:           {s['total_rtb_requests']:,}")
    print(f"  Per session: median {s['median_rtb_per_session']:.0f}, mean {s['mean_rtb_per_session']:.1f} "
          f"+/- {s['sd_rtb_per_session']:.1f}, IQR {q25:.0f} to {q75:.0f}")
    print(f"  With identifying cookie:      {s['identifying_cookie_share_pct']:.1f}% pooled, "
          f"per-session median {s['per_session_median_identifying_share_pct']:.1f}%")
    print("  Top RTB-endpoint hosts, as recorded from raw flows (count):")
    for host, count in s["top_10_rtb_hosts_recorded"]:
        print(f"    {host:<42s} {count:>8,}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n[RTB] -> {OUT.relative_to(HERE)}")


if __name__ == "__main__":
    main()
