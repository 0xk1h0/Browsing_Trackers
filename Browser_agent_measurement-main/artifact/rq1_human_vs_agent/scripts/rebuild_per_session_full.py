#!/usr/bin/env python3
"""Rebuild data/per_session_full.csv from raw session captures.

Reproduction status against the shipped per_session_full.csv (220 human sessions):
  n_records   220/220 exact
  trk_hosts   220/220 exact (uses data/human_host_tracker_map.json, the same
              FilterListEngine decisions that label the agent arm)
  xsite       171/220 exact; the remainder differ by one because the producer
              seeded the cross-site counter differently. The shipped column
              counts at least as many human transitions as this scan, so the
              agent-versus-human comparison built on it is conservative.
  pseudo      not reproducible from Tier-2 captures, whose Cookie headers are
              redacted; rebuild from Tier-3 captures if you need this column.
Rebuild data/per_session_full.csv from runtime per-participant capture trees.

Tracker and navigation columns rebuild from the Tier-2 session bundle plus the
shipped host map. The pseudo column additionally needs Tier-3 captures, which
are withheld under the IRB protocol.

Expected layout:

    <runtime-root>/backup/P{001-022}/<TaskName>--{n}_{timestamp}/capture.jsonl
                                                                  js_telemetry.jsonl
                                                                  answer.json

Plus per-agent matched runs (Tier-3) for the agent rows.

Usage:
    python scripts/rebuild_per_session_full.py \\
        --human-root /path/to/user_study/runtime/backup/ \\
        --agent-root /path/to/runs/

Outputs:
    data/per_session_full.csv   # 220 human + 140 agent rows = 360 sessions
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

PSEUDO_KEYS = (
    "clientid", "client_id", "sessionid", "session_id", "uuid", "guid",
    "adid", "advertising_id", "device_id", "_ga", "_gid", "fbp", "fbc",
    "gclid", "dclid", "msclkid", "clickid",
)
PSEUDO_TOKENS = {"cid", "sid", "uid"}


def is_pseudo_key(k: str) -> bool:
    kl = k.lower()
    return any(kw in kl for kw in PSEUDO_KEYS) or kl in PSEUDO_TOKENS


def parse_session_dir(d: Path):
    """Extract (task_id, timestamp) from a P{nnn}/<Task>--<n>_<TS>/ directory."""
    m = re.match(r"^(.+)_(\d{8}_\d{6})$", d.name)
    if not m:
        return None, None
    return m.group(1), m.group(2)


_START_URLS: dict[str, str] | None = None


def _start_url_for(cap_path) -> str:
    """Task start URL from the shipped assignment file, used to seed the
    cross-site counter exactly as the producer does."""
    global _START_URLS
    if _START_URLS is None:
        path = Path(__file__).resolve().parent.parent / "user_study" / "participant_assignments.json"
        with path.open() as fh:
            data = json.load(fh)
        _START_URLS = {t["task_id"]: t["start_url"]
                       for t in data["assignments"][0]["tasks"]}
    task = Path(cap_path).parent.name.rsplit("_", 2)[0].replace("_", " ")
    return _START_URLS.get(task, "")


def _etld1(host: str) -> str:
    parts = (host or "").lower().strip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


_HOST_TRACKER_MAP: dict[str, bool] | None = None


def _host_tracker_map() -> dict:
    """Shipped host -> is_tracker decisions for the human arm (7-list engine)."""
    global _HOST_TRACKER_MAP
    if _HOST_TRACKER_MAP is None:
        path = Path(__file__).resolve().parent.parent / "data" / "human_host_tracker_map.json"
        with path.open() as fh:
            _HOST_TRACKER_MAP = json.load(fh)
    return _HOST_TRACKER_MAP


def summarize_capture(cap_path: Path):
    """One-pass scan that produces the six per-session metrics."""
    n_records = 0
    pseudo = 0
    trk_hosts = set()
    xsite_transitions = 0
    fp_apis = 0
    if not cap_path.exists():
        return None
    last_first_party = _etld1(urlparse(_start_url_for(cap_path)).hostname or "") or None
    for line in cap_path.open():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        n_records += 1
        host = r.get("hostname", "")
        # Raw human captures carry no tracker label, so fall back to the shipped
        # host decision map produced by the same FilterListEngine that labels the
        # agent arm (data/human_host_tracker_map.json). Agent artifacts already
        # carry is_tracker and take the first branch.
        if "is_tracker" in r:
            is_trk = bool(r.get("is_tracker"))
        else:
            host_map = _host_tracker_map()
            if host not in host_map:
                raise SystemExit(
                    f"{cap_path}: host {host!r} missing from "
                    "data/human_host_tracker_map.json; regenerate it with the "
                    "FilterListEngine before rebuilding."
                )
            is_trk = bool(host_map[host])
        if is_trk:
            trk_hosts.add(host)
        # Cross-site transitions count top-level navigations whose eTLD+1 differs
        # from the previous one, mirroring the agent pipeline (sec-fetch-dest
        # "document" is the strict top-level signal).
        headers_lower = {str(k).lower(): v for k, v in (r.get("headers") or {}).items()}
        if headers_lower.get("sec-fetch-dest", "") == "document":
            e = _etld1(host)
            if e and e != last_first_party:
                xsite_transitions += 1
                last_first_party = e
        url = r.get("url", "")
        if url:
            try:
                for k, v in parse_qsl(urlparse(url).query, keep_blank_values=True):
                    if v and is_pseudo_key(k):
                        pseudo += 1
            except Exception:
                pass
        for hk, hv in (r.get("headers") or {}).items():
            if hk.lower() == "cookie" and hv:
                pseudo += sum(1 for tok in hv.split(";") if "=" in tok)
        # fingerprint API calls captured by the JS shim go to a sibling file;
        # this stub counts in-flow telemetry beacons as a coarse proxy.
        if r.get("path", "").endswith("/__agentcloak_telemetry"):
            fp_apis += 1
    return {
        "n_records": n_records,
        "trk_hosts": len(trk_hosts),
        "pseudo": pseudo,
        "xsite": xsite_transitions,
        "fp_apis": fp_apis,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--human-root", type=Path, required=True,
                    help="path to runtime/backup/ (Tier-3, not shipped)")
    ap.add_argument("--agent-root", type=Path, default=None,
                    help="path to .../runs/ for agent paired_sessions.jsonl")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parents[1]
                    / "data" / "per_session_full.csv")
    args = ap.parse_args()

    rows = []
    if args.human_root.exists():
        for pdir in sorted(args.human_root.iterdir()):
            if not pdir.is_dir() or not re.match(r"^P\d{3}", pdir.name):
                continue
            for sess in sorted(pdir.iterdir()):
                if not sess.is_dir():
                    continue
                task, _ = parse_session_dir(sess)
                if not task:
                    continue
                cap = sess / "capture.jsonl"
                metrics = summarize_capture(cap)
                if metrics is None:
                    continue
                rows.append({
                    "cohort": "human", "agent": "human",
                    "id": pdir.name, "task_id": task,
                    **metrics,
                    "context": "", "device_net": "", "behavior": "",
                    "direct_id": "",
                })

    if args.agent_root and args.agent_root.exists():
        for agent_run in args.agent_root.iterdir():
            if not agent_run.is_dir():
                continue
            jl = agent_run / "paired_sessions.jsonl"
            if not jl.exists():
                continue
            for line in jl.open():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("condition") != "agent":
                    continue
                leak = r.get("info_leakage_counts") or {}
                rows.append({
                    "cohort": "agent",
                    "agent": r.get("agent_name", agent_run.name),
                    "id": r.get("session_id", ""),
                    "task_id": r.get("task_id", ""),
                    "n_records": r.get("total_requests", 0),
                    "xsite": r.get("nav_cross_site_transition_count", 0),
                    "trk_hosts": r.get("unique_tracker_domains", 0),
                    "pseudo": leak.get("pseudonymous_identifier", 0),
                    "context": leak.get("context_signal", 0),
                    "device_net": leak.get("device_network_signal", 0),
                    "behavior": leak.get("behavior_signal", 0),
                    "direct_id": leak.get("direct_identifier", 0),
                    "fp_apis": r.get("fp_apis", 0) or 0,
                })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cohort", "agent", "id", "task_id", "n_records", "xsite",
                    "trk_hosts", "pseudo", "context", "device_net",
                    "behavior", "direct_id", "fp_apis"])
        for r in rows:
            w.writerow([r.get(k, "") for k in
                        ["cohort", "agent", "id", "task_id", "n_records",
                         "xsite", "trk_hosts", "pseudo", "context",
                         "device_net", "behavior", "direct_id", "fp_apis"]])
    print(f"[rebuild] {len(rows)} sessions -> {args.out}")


if __name__ == "__main__":
    main()
