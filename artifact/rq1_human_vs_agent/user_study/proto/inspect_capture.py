#!/usr/bin/env python3
"""Quick summary of a Phase-1 prototype capture file.

Usage:
    python inspect_capture.py <capture.jsonl>
    python inspect_capture.py runtime/proto_*/capture.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse


def summarise(path: Path) -> dict:
    if not path.exists():
        return {"error": f"file not found: {path}"}

    n_lines = 0
    hosts: Counter[str] = Counter()
    methods: Counter[str] = Counter()
    statuses: Counter[int] = Counter()
    schemes: Counter[str] = Counter()
    content_types: Counter[str] = Counter()
    sample_urls: list[str] = []

    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            n_lines += 1

            url = rec.get("url") or rec.get("request_url") or ""
            method = rec.get("method") or rec.get("request_method") or ""
            # The agentcloak addon writes `response_status`, not `status_code`.
            status = (
                rec.get("response_status")
                or rec.get("status_code")
                or rec.get("response_status_code")
                or 0
            )
            ctype = (
                rec.get("content_type")
                or rec.get("response_content_type")
                or ""
            )

            if url:
                try:
                    p = urlparse(url)
                    if p.hostname:
                        hosts[p.hostname] += 1
                    if p.scheme:
                        schemes[p.scheme] += 1
                except Exception:
                    pass

            if method:
                methods[method] += 1
            if status:
                statuses[int(status)] += 1
            if ctype:
                content_types[ctype.split(";")[0].strip()] += 1

            if len(sample_urls) < 8 and url:
                sample_urls.append(url[:140])

    return {
        "file": str(path),
        "n_requests": n_lines,
        "unique_hosts": len(hosts),
        "top_hosts": hosts.most_common(15),
        "methods": dict(methods),
        "schemes": dict(schemes),
        "status_codes": dict(statuses.most_common(8)),
        "content_types": dict(content_types.most_common(8)),
        "sample_urls": sample_urls,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("capture", nargs="+", help="path(s) to capture.jsonl")
    args = ap.parse_args()

    for c in args.capture:
        s = summarise(Path(c))
        print("=" * 60)
        print(f"  {s.get('file')}")
        print("=" * 60)
        if "error" in s:
            print(f"  ERROR: {s['error']}")
            continue
        print(f"  Requests          : {s['n_requests']}")
        print(f"  Unique hostnames  : {s['unique_hosts']}")
        print(f"  Methods           : {s['methods']}")
        print(f"  Schemes           : {s['schemes']}")
        print(f"  Status codes      : {s['status_codes']}")
        print(f"  Content types     : {s['content_types']}")
        print(f"\n  Top hosts:")
        for h, n in s["top_hosts"]:
            print(f"    {n:5d}  {h}")
        print(f"\n  Sample URLs:")
        for u in s["sample_urls"]:
            print(f"    {u}")
        print()


if __name__ == "__main__":
    main()
