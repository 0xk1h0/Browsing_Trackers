#!/usr/bin/env bash
# anonymize_check.sh — fail-loudly grep for identifying signals in the artifact.
# Exit 0 iff every category is clean.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

EXCLUDE_COMMON=(
  --exclude-dir=.git
  --exclude-dir=__pycache__
  --exclude-dir=.venv
  --exclude-dir=node_modules
  --exclude-dir=.scan_results
  --exclude-dir=.omc
  # Binary archives (sanitization is verified by the publisher before archiving)
  --exclude=*.gz
  --exclude=*.xz
  --exclude=*.bz2
  --exclude=*.tar
  --exclude=*.zip
  --exclude=*.pdf
  --exclude=*.png
  --exclude=*.jpg
  --exclude=*.pyc
)

fail=0
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }

run_check () {
  local name="$1" pattern="$2"; shift 2
  local hits exit_code
  hits=$(grep -rlP "${EXCLUDE_COMMON[@]}" "$@" "$pattern" . 2>/dev/null)
  exit_code=$?
  # grep returns 1 when no matches found; treat that as clean. Other non-zero
  # codes (2 = error, 3 = malformed pattern) are real failures.
  if [[ $exit_code -gt 1 ]]; then
    red "[ERROR] $name (grep exit $exit_code)"
    fail=1
    return
  fi
  if [[ -z "$hits" ]]; then
    green "[OK] $name"
  else
    red "[FAIL] $name"
    printf '    %s\n' $hits
    fail=1
  fi
}

# scrub_session_paths.py contains the /home/<user>/ regex by design.
run_check "Local-machine paths (/home/<user>/, /Users/<user>/)" \
  '/home/[a-zA-Z0-9_-]+|/Users/[a-zA-Z0-9_-]+' \
  --exclude-dir=tracker_lists --exclude=scrub_session_paths.py

run_check "Korean text in source/config" \
  '[\x{AC00}-\x{D7A3}]' \
  --exclude-dir=locale --exclude=ko.json --exclude-dir=docs --exclude-dir=tracker_lists \
  --exclude=*.pdf --exclude=*.png --exclude=*.jpg

# The GitHub handle was a double-blind guard before acceptance. The paper is
# accepted and the repository is now intentionally attributable, so the handle
# is allowed where it forms the canonical repository or Colab URL and is still
# flagged anywhere else.
run_check "Internal hostnames / emails / org markers" \
  'etri\.re\.kr|@etri|etri-gpu|timkh0625|kiho\.k|(?<!github\.com/)(?<!github/)0xk1h0' \
  --exclude-dir=tracker_lists --exclude=anonymize_check.sh

run_check "RFC1918 internal IPs" \
  '\b(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]+\.[0-9]+\b' \
  --exclude-dir=tracker_lists

# The study network's own public address. Private-range checks never catch a
# public IP, and trackers echo the client IP back into captured traffic, so this
# is checked explicitly rather than by range.
run_check "Study-network public IP" \
  '129\.254\.184\.3' \
  --exclude-dir=tracker_lists --exclude=scrub_user_study_archive.py

run_check "API key signatures (sk-/hf_/ghp_/AKIA)" \
  '(sk-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})' \
  --exclude-dir=tracker_lists

# The grep sweeps above skip compressed archives, so scan inside them too.
# Without this, operator-local paths can hide in the shipped per-session data.
echo
if command -v python3 >/dev/null 2>&1; then
  if python3 "$ROOT/scripts/scrub_session_paths.py" --check >/dev/null 2>&1; then
    green "[OK] Operator-local paths inside shipped .jsonl.gz archives"
  else
    red "[FAIL] Operator-local paths inside shipped .jsonl.gz archives"
    echo "    run: python3 scripts/scrub_session_paths.py"
    fail=1
  fi
  if python3 "$ROOT/scripts/scrub_user_study_archive.py" --check >/dev/null 2>&1; then
    green "[OK] Operator identifiers inside the user-study .tar.xz archive"
  else
    red "[FAIL] Operator identifiers inside the user-study .tar.xz archive"
    echo "    run: python3 scripts/scrub_user_study_archive.py"
    fail=1
  fi
else
  red "[SKIP] archive scans need python3"
fi

echo
if [[ $fail -eq 0 ]]; then
  green "All anonymization checks passed."
  exit 0
else
  red "Anonymization checks FAILED — redact the matches above."
  exit 1
fi
