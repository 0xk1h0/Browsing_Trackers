#!/usr/bin/env python3
"""Rebuild data/rq3_layer_isolation/per_session.csv from raw mitmproxy flows.

REQUIRES Tier-3 raw capture data: a directory tree of the form

    <raw_root>/agent=<name>/condition=<C>/task=<task>/rep=<r>/capture.jsonl

which is **not shipped** with this artifact (per IRB protocol — see
docs/ETHICS.md). If you have generated your own captures by running the
end-to-end pipeline (see INSTALL.md), point this script at their root with
`--raw-root` and it will regenerate per_session.csv from scratch.

The shipped per_session.csv was produced by running this script against the
project's internal raw capture archive.

Usage:
    python scripts/rebuild_layer_isolation.py --raw-root /path/to/captures
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

HERE = Path(__file__).resolve().parent
OUT_CSV = HERE.parent / "data" / "rq3_layer_isolation" / "per_session.csv"

PSEUDONYM_KEYWORDS = (
    "clientid", "client_id", "sessionid", "session_id", "uuid", "guid",
    "adid", "advertising_id", "device_id", "_ga", "_gid", "fbp", "fbc",
    "gclid", "dclid", "msclkid", "clickid",
)
PSEUDONYM_TOKENS = {"cid", "sid", "uid"}


def _is_pseudo_key(key: str) -> bool:
    k = key.lower()
    return any(kw in k for kw in PSEUDONYM_KEYWORDS) or k in PSEUDONYM_TOKENS


def count_pseudo_ids(cap_path: Path) -> int:
    n_url = n_cookie = n_set = 0
    try:
        with cap_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = r.get("url", "")
                if url:
                    try:
                        qs = urlparse(url).query
                        if qs:
                            for k, v in parse_qsl(qs, keep_blank_values=True):
                                if v and _is_pseudo_key(k):
                                    n_url += 1
                    except Exception:
                        pass
                headers = r.get("headers") or {}
                for hk, hv in headers.items():
                    if hk.lower() == "cookie" and hv:
                        n_cookie += sum(1 for tok in hv.split(";") if "=" in tok)
                sc = r.get("set_cookies") or []
                if isinstance(sc, list):
                    n_set += len(sc)
                resp_h = r.get("response_headers") or {}
                for rh in resp_h:
                    if rh.lower() == "set-cookie":
                        v = resp_h[rh]
                        if isinstance(v, str) and v:
                            n_set += v.count("\n") + 1
    except OSError:
        return 0
    return n_url + n_cookie + n_set


def walk_sessions(root: Path):
    for agent_dir in sorted(root.glob("agent=*")):
        agent = agent_dir.name.split("=", 1)[1]
        for cond_dir in sorted(agent_dir.glob("condition=*")):
            cond = cond_dir.name.split("=", 1)[1]
            for task_dir in sorted(cond_dir.glob("task=*")):
                task = task_dir.name.split("=", 1)[1]
                for rep_dir in sorted(task_dir.glob("rep=*")):
                    rep = rep_dir.name.split("=", 1)[1]
                    cap = rep_dir / "capture.jsonl"
                    if cap.exists():
                        yield (agent, cond, task, rep, cap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-root", type=Path, required=True,
                    help="path to capture archive (Tier-3, not shipped)")
    ap.add_argument("--out", type=Path, default=OUT_CSV)
    args = ap.parse_args()

    if not args.raw_root.exists():
        ap.error(f"raw root does not exist: {args.raw_root}")

    rows = []
    for agent, cond, task, rep, cap in walk_sessions(args.raw_root):
        rows.append((agent, cond, task, rep, count_pseudo_ids(cap)))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["agent", "condition", "task", "rep", "pseudo_id_count"])
        w.writerows(rows)

    print(f"[rebuild] {len(rows)} sessions -> {args.out}")


if __name__ == "__main__":
    main()
