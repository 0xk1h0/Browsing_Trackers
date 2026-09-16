#!/usr/bin/env python3
"""verify.py — diff regenerated reproduction outputs against the shipped golden snapshot.

Run after `bash REPRODUCE.sh`:

    python3 verify.py              # every module
    python3 verify.py rq1 rq3      # same module tags REPRODUCE.sh accepts
    python3 verify.py --update     # authors only: refresh golden/ from current outputs

The per-RQ scripts overwrite the files under `*/expected_outputs/` (and, for RQ3,
under `rq3_ablation/data/`), so those cannot be compared against themselves. A
read-only copy of every regenerated text artifact lives in `golden/`, mirroring the
repo layout; this script diffs the freshly written files against that snapshot. No
git history needed — a zip/tarball download works the same.

TSV / CSV / JSON are compared value-by-value. Numbers use a 1e-6 relative tolerance
so a different BLAS / libm on the evaluator's machine cannot cause a spurious
failure; everything else must match exactly. PDF / PNG figures embed a build
timestamp and are therefore checked for existence, non-zero size and file magic
only, not byte equality.

Exit code 0 = all checks passed, 1 = at least one mismatch, 2 = usage error.

Standard library only.
"""

from __future__ import annotations

import csv
import json
import math
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden"

REL_TOL = 1e-6
ABS_TOL = 1e-12
MAX_DIFFS_PER_FILE = 10

# Regenerated text artifacts (diffed against golden/) and figures (existence only).
# Paths are relative to the module directory.
MODULES: dict[str, dict[str, list[str]]] = {
    "rq1_human_vs_agent": {
        "tables": [
            "expected_outputs/rq1_paired.tsv",
            "expected_outputs/rq1_paired.json",
            "expected_outputs/table1_trk_hosts.tsv",
            "expected_outputs/table4_family_composition.tsv",
        ],
        "figures": [
            "figures/fig_rq1_paired_diff.pdf",
            "figures/fig_rq1_paired_diff.png",
        ],
    },
    "rq2_dose_response": {
        "tables": [
            "expected_outputs/table2.tsv",
            "expected_outputs/table3.tsv",
            "expected_outputs/table3_tail_share.tsv",
            "expected_outputs/cmp_summary.tsv",
            "expected_outputs/paired_per_agent.tsv",
            "expected_outputs/rtb_summary.json",
        ],
        "figures": [
            "figures/fig3_dose_response.pdf",
            "figures/fig3_dose_response.png",
        ],
    },
    "rq3_ablation": {
        # analyze_table6.py / analyze_table12.py write into data/, not expected_outputs/.
        "tables": [
            "data/rq3_643/full_metrics_per_cell.csv",
            "data/rq3_643/ablation_stat_tests.csv",
            "data/rq3_layer_isolation/decomposition.csv",
        ],
        "figures": [],
    },
    "cross_benchmark": {
        "tables": [
            "expected_outputs/table7.tsv",
            "expected_outputs/mind2web_spearman.json",
        ],
        "figures": [],
    },
}

ALIASES = {
    "rq1": "rq1_human_vs_agent",
    "rq2": "rq2_dose_response",
    "rq3": "rq3_ablation",
    "cross": "cross_benchmark",
    "m2w": "cross_benchmark",
    "mind2web": "cross_benchmark",
}

MAGIC = {".pdf": b"%PDF-", ".png": b"\x89PNG\r\n\x1a\n"}


# ---------------------------------------------------------------- value compare


def as_float(value):
    """Return value as float, or None if it is not a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def values_match(golden, observed) -> bool:
    g, o = as_float(golden), as_float(observed)
    if g is None or o is None:
        return golden == observed
    if math.isnan(g) and math.isnan(o):
        return True
    return math.isclose(g, o, rel_tol=REL_TOL, abs_tol=ABS_TOL)


# ---------------------------------------------------------------- table compare


def read_table(path: Path, delimiter: str) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return [row for row in csv.reader(f, delimiter=delimiter)]


def diff_table(golden: Path, observed: Path, delimiter: str) -> tuple[list[str], str]:
    g_rows = read_table(golden, delimiter)
    o_rows = read_table(observed, delimiter)
    if not g_rows:
        return [f"golden {golden} is empty"], ""

    header = g_rows[0]
    shape = f"{len(g_rows) - 1} rows x {len(header)} cols"
    diffs: list[str] = []

    if len(g_rows) != len(o_rows):
        diffs.append(f"row count: golden {len(g_rows)}, observed {len(o_rows)}")

    for r, (g_row, o_row) in enumerate(zip(g_rows, o_rows), start=1):
        if len(g_row) != len(o_row):
            diffs.append(f"row {r}: column count golden {len(g_row)}, observed {len(o_row)}")
            continue
        key = f" ({header[0]}={g_row[0]})" if r > 1 and g_row else ""
        for c, (g_val, o_val) in enumerate(zip(g_row, o_row)):
            if values_match(g_val, o_val):
                continue
            col = header[c] if c < len(header) else f"#{c + 1}"
            diffs.append(
                f"row {r}{key}, column '{col}': golden {g_val!r}, observed {o_val!r}"
            )
            if len(diffs) >= MAX_DIFFS_PER_FILE:
                return diffs, shape
    return diffs, shape


# ----------------------------------------------------------------- json compare


def diff_json_node(golden, observed, path: str, diffs: list[str]) -> None:
    if len(diffs) >= MAX_DIFFS_PER_FILE:
        return
    if isinstance(golden, dict) and isinstance(observed, dict):
        for key in golden:
            if key not in observed:
                diffs.append(f"{path}.{key}: missing from observed output")
            else:
                diff_json_node(golden[key], observed[key], f"{path}.{key}", diffs)
        for key in observed:
            if key not in golden:
                diffs.append(f"{path}.{key}: unexpected key in observed output")
        return
    if isinstance(golden, list) and isinstance(observed, list):
        if len(golden) != len(observed):
            diffs.append(f"{path}: length golden {len(golden)}, observed {len(observed)}")
        for i, (g_item, o_item) in enumerate(zip(golden, observed)):
            diff_json_node(g_item, o_item, f"{path}[{i}]", diffs)
        return
    if type(golden) is not type(observed) and as_float(golden) is None:
        diffs.append(f"{path}: golden {golden!r}, observed {observed!r}")
        return
    if not values_match(golden, observed):
        diffs.append(f"{path}: golden {golden!r}, observed {observed!r}")


def diff_json(golden: Path, observed: Path) -> tuple[list[str], str]:
    g = json.loads(golden.read_text(encoding="utf-8"))
    o = json.loads(observed.read_text(encoding="utf-8"))
    diffs: list[str] = []
    diff_json_node(g, o, "$", diffs)
    shape = f"{len(g)} top-level keys" if isinstance(g, dict) else f"{len(g)} items"
    return diffs, shape


# ---------------------------------------------------------------------- checks


def check_table(module: str, rel: str) -> tuple[bool, str, list[str]]:
    observed = HERE / module / rel
    golden = GOLDEN / module / rel
    label = f"{module}/{rel}"
    if not golden.exists():
        return False, label, [f"golden/{module}/{rel} missing — run: python3 verify.py --update"]
    if not observed.exists():
        return False, label, ["not generated — run: bash REPRODUCE.sh"]

    suffix = observed.suffix.lower()
    try:
        if suffix == ".json":
            diffs, shape = diff_json(golden, observed)
        else:
            diffs, shape = diff_table(golden, observed, "\t" if suffix == ".tsv" else ",")
    except (ValueError, UnicodeDecodeError) as exc:  # unreadable / malformed output
        return False, label, [f"could not parse: {exc}"]
    return (not diffs), f"{label}  ({shape})", diffs


def check_figure(module: str, rel: str) -> tuple[bool, str, list[str]]:
    path = HERE / module / rel
    label = f"{module}/{rel}"
    if not path.exists():
        return False, label, ["not generated — run: bash REPRODUCE.sh"]
    size = path.stat().st_size
    if size == 0:
        return False, label, ["file is empty"]
    magic = MAGIC.get(path.suffix.lower())
    if magic:
        with path.open("rb") as f:
            if f.read(len(magic)) != magic:
                return False, label, [f"not a valid {path.suffix.lstrip('.').upper()} file"]
    # Figures embed a build timestamp, so content is deliberately not byte-compared.
    return True, f"{label}  ({size:,} bytes, content not byte-compared)", []


# ---------------------------------------------------------------------- driver


def resolve_modules(args: list[str]) -> list[str]:
    if not args:
        return list(MODULES)
    selected: list[str] = []
    for tag in args:
        module = ALIASES.get(tag, tag)
        if module not in MODULES:
            print(f"[verify] unknown module: {tag}", file=sys.stderr)
            print(f"[verify] known: {', '.join(sorted(set(ALIASES) | set(MODULES)))}", file=sys.stderr)
            sys.exit(2)
        if module not in selected:
            selected.append(module)
    return selected


def update_golden(modules: list[str]) -> int:
    copied = 0
    for module in modules:
        for rel in MODULES[module]["tables"]:
            src = HERE / module / rel
            if not src.exists():
                print(f"[verify] skip (not generated): {module}/{rel}", file=sys.stderr)
                continue
            dst = GOLDEN / module / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            print(f"[verify] golden <- {module}/{rel}")
            copied += 1
    print(f"[verify] refreshed {copied} golden file(s) under {GOLDEN}")
    print("[verify] review `git diff golden/` before committing.")
    return 0


def main(argv: list[str]) -> int:
    args = list(argv)
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 0
    update = "--update" in args
    if update:
        args.remove("--update")
    modules = resolve_modules(args)

    if update:
        return update_golden(modules)

    if not GOLDEN.is_dir():
        print(f"[verify] golden snapshot missing: {GOLDEN}", file=sys.stderr)
        print("[verify] authors: create it with `python3 verify.py --update`.", file=sys.stderr)
        return 2

    failures: list[tuple[str, list[str]]] = []
    total = 0
    for module in modules:
        print(f"\n--- {module} ---")
        for rel in MODULES[module]["tables"]:
            ok, label, diffs = check_table(module, rel)
            total += 1
            print(f"[verify] {'ok  ' if ok else 'FAIL'} {label}")
            if not ok:
                failures.append((label, diffs))
                for line in diffs:
                    print(f"           {line}")
        for rel in MODULES[module]["figures"]:
            ok, label, diffs = check_figure(module, rel)
            total += 1
            print(f"[verify] {'ok  ' if ok else 'FAIL'} {label}")
            if not ok:
                failures.append((label, diffs))
                for line in diffs:
                    print(f"           {line}")

    print()
    if failures:
        sys.stdout.flush()  # keep the stderr summary after the stdout log when piped
        print(f"[verify] {len(failures)} of {total} checks FAILED:", file=sys.stderr)
        for label, diffs in failures:
            print(f"[verify]   {label}", file=sys.stderr)
            for line in diffs:
                print(f"[verify]     {line}", file=sys.stderr)
        if any("golden " in line for _, diffs in failures for line in diffs):
            print(
                f"[verify] tolerance for numeric cells is rel_tol={REL_TOL:g}; "
                "a mismatch above that is a real difference, not float noise.",
                file=sys.stderr,
            )
        return 1
    print(f"[verify] {total} checks over {len(modules)} module(s).")
    print("[verify] All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
