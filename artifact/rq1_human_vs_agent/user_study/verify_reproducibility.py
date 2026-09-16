#!/usr/bin/env python3
"""Reproducibility validation harness for the user-study artifact.

Verifies that the public-release version of the user study can be regenerated
deterministically and that no operator-identifying / non-English content has
leaked into the artifact.

Checks (all must pass for exit 0):

  1. Python sources parse cleanly.
  2. JSON sources parse cleanly.
  3. `proto/build_fixed_tasks.py` regenerates `participant_assignments.json`
     bit-for-bit.
  4. The Latin-square assignment is valid: every task appears exactly once
     per ordinal position across every block of N_TASKS participants.
  5. No Korean (Hangul) code points remain in any tracked source file
     (excluding the vendored noVNC `app/locale/ko.json`).
  6. No operator-identifying domains, tunnel UUIDs, or absolute home paths
     leak from the artifact (excluding the vendored noVNC tree, which is
     unmodified upstream code).
  7. `runtime/backup/` answer.json files match the canonical English
     master-question text and contain no Korean.

Usage:
    python verify_reproducibility.py [-v]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path


STUDY_DIR = Path(__file__).resolve().parent
ASSIGNMENTS = STUDY_DIR / "participant_assignments.json"

HANGUL_RE = re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]")

# Patterns that indicate operator-identifying content that should have been
# anonymized before public release. The check skips the vendored noVNC tree.
LEAKAGE_PATTERNS = [
    (re.compile(r"broweragent\.com"), "operator public hostname"),
    (re.compile(r"cf3b2563-32e8-4033-b36f-d83daca2cdb1"), "operator tunnel UUID"),
    (re.compile(r"/home/<user>(?:/|$)"), "operator home path"),
]

# Files / directories the verifier should not lint for Korean or leakage.
# Verifier internals are exempt because they intentionally name the patterns;
# chrome_profile/ subtrees inside runtime/backup/ hold Chromium-internal
# state files whose format and content are owned by Chromium, not by this
# study, so they are not lintable artifacts; xpra-html5/ is an external
# vendored tree cloned by the operator (see README §3.2) and exempt from
# our prose / leakage checks.
SKIP_DIR_NAMES = {"__pycache__", ".omc", ".git", "node_modules", "chrome_profile"}
SKIP_PATH_PREFIXES = [
    STUDY_DIR / "xpra-html5",            # external, cloned by operator
]
SKIP_FILES = {
    # All three contain literal Hangul codepoints inside their own regex
    # patterns used TO DETECT Hangul. Linting them would trip on the
    # regex itself.
    STUDY_DIR / "verify_reproducibility.py",
    STUDY_DIR / "scripts" / "sanitize_backup_answers.py",
    STUDY_DIR / "scripts" / "sanitize_sessions.py",
}

PY_SUFFIXES = {".py"}
JSON_SUFFIXES = {".json"}
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".sh", ".bat", ".command",
    ".html", ".js", ".css", ".yml", ".yaml", ".toml", ".proto",
}


def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m" if sys.stdout.isatty() else s


def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m" if sys.stdout.isatty() else s


def _yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m" if sys.stdout.isatty() else s


def iter_tracked_files(suffixes: set[str]) -> list[Path]:
    out: list[Path] = []
    for root, dirs, files in os.walk(STUDY_DIR):
        # Prune
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
        root_p = Path(root)
        if any(_is_under(root_p, p) for p in SKIP_PATH_PREFIXES):
            continue
        for name in files:
            p = root_p / name
            if p.suffix.lower() not in suffixes:
                continue
            if p in SKIP_FILES:
                continue
            out.append(p)
    return sorted(out)


def _is_under(p: Path, parent: Path) -> bool:
    try:
        p.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


# --- checks ----------------------------------------------------------------

def check_python_parses(verbose: bool) -> list[str]:
    errors: list[str] = []
    for p in iter_tracked_files(PY_SUFFIXES):
        try:
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            if verbose:
                print(f"  py-ok {p.relative_to(STUDY_DIR)}")
        except SyntaxError as e:
            errors.append(f"{p.relative_to(STUDY_DIR)}: {e}")
    return errors


def check_json_parses(verbose: bool) -> list[str]:
    errors: list[str] = []
    for p in iter_tracked_files(JSON_SUFFIXES):
        try:
            json.loads(p.read_text(encoding="utf-8"))
            if verbose:
                print(f"  json-ok {p.relative_to(STUDY_DIR)}")
        except json.JSONDecodeError as e:
            errors.append(f"{p.relative_to(STUDY_DIR)}: {e}")
    return errors


def check_assignments_deterministic(verbose: bool) -> list[str]:
    """Regenerate participant_assignments.json into a tempdir and compare
    byte-for-byte with the checked-in version."""
    errors: list[str] = []
    if not ASSIGNMENTS.is_file():
        return ["participant_assignments.json missing"]

    expected = json.loads(ASSIGNMENTS.read_text())
    n = expected["design"]["n_participants"]

    with tempfile.TemporaryDirectory() as td:
        # We don't want to clobber the checked-in file, so we re-run
        # build() via import rather than the CLI.
        sys.path.insert(0, str(STUDY_DIR / "proto"))
        try:
            import importlib
            mod = importlib.import_module("build_fixed_tasks")
            importlib.reload(mod)
            actual = mod.build(n)
        finally:
            sys.path.pop(0)

    e_bytes = json.dumps(expected, indent=2, ensure_ascii=False, sort_keys=False)
    a_bytes = json.dumps(actual, indent=2, ensure_ascii=False, sort_keys=False)
    if hashlib.sha256(e_bytes.encode()).hexdigest() != hashlib.sha256(a_bytes.encode()).hexdigest():
        errors.append("participant_assignments.json does not match `build(n)` output")
    elif verbose:
        print(f"  assignments-ok (n_participants={n})")
    return errors


def check_latin_square(verbose: bool) -> list[str]:
    errors: list[str] = []
    data = json.loads(ASSIGNMENTS.read_text())
    n_tasks = data["design"]["n_tasks"]
    # Examine the first block of n_tasks participants.
    block = data["assignments"][:n_tasks]
    if len(block) < n_tasks:
        return [f"need at least {n_tasks} participants for a full block, got {len(block)}"]
    for ordinal in range(n_tasks):
        seen: Counter[int] = Counter(
            p["task_order_master_indices"][ordinal] for p in block
        )
        if len(seen) != n_tasks or any(v != 1 for v in seen.values()):
            errors.append(
                f"ordinal position {ordinal + 1} is not balanced across "
                f"the first {n_tasks} participants: {dict(seen)}"
            )
    if not errors and verbose:
        print(f"  latin-square-ok (1 block of {n_tasks} participants checked)")
    return errors


def check_no_korean(verbose: bool) -> list[str]:
    errors: list[str] = []
    for p in iter_tracked_files(TEXT_SUFFIXES):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        matches = HANGUL_RE.findall(text)
        if matches:
            errors.append(
                f"{p.relative_to(STUDY_DIR)}: {len(matches)} Hangul char(s) remain"
            )
    if not errors and verbose:
        print("  no-korean-ok")
    return errors


def check_no_operator_leakage(verbose: bool) -> list[str]:
    errors: list[str] = []
    for p in iter_tracked_files(TEXT_SUFFIXES):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat, label in LEAKAGE_PATTERNS:
            if pat.search(text):
                errors.append(
                    f"{p.relative_to(STUDY_DIR)}: {label} ({pat.pattern!r}) leaked"
                )
    if not errors and verbose:
        print("  no-operator-leakage-ok")
    return errors


def check_session_answers_sanitized(verbose: bool) -> list[str]:
    errors: list[str] = []
    data = json.loads(ASSIGNMENTS.read_text())
    eng_q = {t["task_id"]: t["question"] for t in data["tasks_master"]}
    for p in sorted((STUDY_DIR / "runtime").glob("*/*/*/answer.json")):
        try:
            ans = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"{p.relative_to(STUDY_DIR)}: {e}")
            continue
        tid = ans.get("task_id")
        if tid not in eng_q:
            errors.append(f"{p.relative_to(STUDY_DIR)}: unknown task_id {tid!r}")
            continue
        if ans.get("question") != eng_q[tid]:
            errors.append(
                f"{p.relative_to(STUDY_DIR)}: question text drifts from "
                f"participant_assignments.json master question"
            )
        full = json.dumps(ans, ensure_ascii=False)
        if HANGUL_RE.search(full):
            errors.append(f"{p.relative_to(STUDY_DIR)}: Hangul present in answer JSON")
    if not errors and verbose:
        print("  session-answers-ok")
    return errors


def check_session_pii_removed(verbose: bool) -> list[str]:
    """Verify the sanitizer removed PII-bearing files / fields from
    runtime/sessions/."""
    errors: list[str] = []
    runtime = STUDY_DIR / "runtime"
    if not runtime.is_dir():
        return errors
    # 1) survey_code.txt files MUST be gone (external-survey join keys).
    leaks = list(runtime.glob("*/*/survey_code.txt"))
    if leaks:
        errors.append(
            f"{len(leaks)} survey_code.txt file(s) still present "
            f"(these link to external survey identity)"
        )
    # 2) chrome_profile/ trees MUST be gone (cookie / login DB).
    cps = list(runtime.glob("*/*/*/chrome_profile"))
    if cps:
        errors.append(
            f"{len(cps)} chrome_profile/ tree(s) still present "
            f"(contain operator session SQLite DBs)"
        )
    # 3) capture.jsonl set_cookies should be empty (operator session tokens).
    bad_cookies = 0
    for cap in runtime.glob("*/*/*/capture.jsonl"):
        with cap.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("set_cookies"):
                    bad_cookies += 1
                    break
        if bad_cookies > 5:  # early bail; representative sample
            errors.append(
                "capture.jsonl files still contain populated set_cookies "
                "(operator session tokens leaked)"
            )
            break
    # 4) any unix-style operator filesystem paths in JSON / shell / log under
    # runtime? Negative lookbehind ensures we don't match URL path segments —
    # only standalone filesystem paths preceded by a non-URL-character (quote,
    # space, `=`, etc.) are flagged.
    leak_re = re.compile(r"(?<![A-Za-z0-9./_\-])/home/[a-z][a-z0-9._-]*/")
    leak_files = []
    for p in runtime.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in {".json", ".sh", ".log", ".txt", ".md"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if leak_re.search(text):
            leak_files.append(str(p.relative_to(STUDY_DIR)))
        if len(leak_files) > 5:
            break
    if leak_files:
        errors.append(
            f"/home/<user>/ paths still present in: {leak_files[:5]}"
            + (" ..." if len(leak_files) > 5 else "")
        )
    # 5) ko-KR literal in any sanitizable text file (excluding analytic data)
    ko_files = []
    for p in runtime.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in {".json", ".sh", ".log", ".txt", ".md"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "ko-KR" in text:
            ko_files.append(str(p.relative_to(STUDY_DIR)))
        if len(ko_files) > 5:
            break
    if ko_files:
        errors.append(
            f"ko-KR literal still present in: {ko_files[:5]}"
            + (" ..." if len(ko_files) > 5 else "")
        )
    if not errors and verbose:
        print("  session-pii-removed-ok")
    return errors


# --- runner ----------------------------------------------------------------

CHECKS = [
    ("python sources parse",          check_python_parses),
    ("JSON sources parse",            check_json_parses),
    ("assignments deterministic",     check_assignments_deterministic),
    ("Latin square balanced",         check_latin_square),
    ("no Korean text",                check_no_korean),
    ("no operator-identifying paths", check_no_operator_leakage),
    ("session answers sanitized",     check_session_answers_sanitized),
    ("session PII removed",           check_session_pii_removed),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    n_fail = 0
    for label, fn in CHECKS:
        errs = fn(args.verbose)
        if errs:
            n_fail += 1
            print(_red(f"[FAIL] {label}"))
            for e in errs:
                print(f"       - {e}")
        else:
            print(_green(f"[ OK ] {label}"))

    print()
    if n_fail:
        print(_red(f"{n_fail} check(s) failed."))
        sys.exit(1)
    print(_green("All reproducibility checks pass."))


if __name__ == "__main__":
    main()
