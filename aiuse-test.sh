#!/usr/bin/env bash
# Rehearse a release without publishing anything.
#
#   ./aiuse-test.sh [X.Y.Z] [--quick]
#
# X.Y.Z defaults to the next patch version. --quick runs pytest instead of the
# full `just ci` gate. Nothing here commits, tags, pushes, uploads to PyPI,
# creates a GitHub release, or touches Homebrew / pipx: the release itself is
# `just release X.Y.Z` (docs/packaging.md), and this script stops short of it.
#
# Steps: preflight state -> quality gate -> `release.py --dry-run` ->
# build sdist+wheel -> twine check -> install the wheel in a throwaway venv and
# smoke-test it -> syntax-check the Homebrew formula.
set -uo pipefail

cd "$(dirname "$0")"

VERSION=""
QUICK=0
for arg in "$@"; do
  case "$arg" in
    --quick) QUICK=1 ;;
    -h | --help)
      sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*)
      echo "unknown option: $arg" >&2
      exit 2
      ;;
    *) VERSION="$arg" ;;
  esac
done

CURRENT=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
if [ -z "$VERSION" ]; then
  VERSION="${CURRENT%.*}.$((${CURRENT##*.} + 1))"
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/aiuse-test.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

FAILED=()
WARNED=()
step() { printf '\n==> %s\n' "$*"; }
ok() { printf '  ok: %s\n' "$*"; }
warn() {
  printf '  WARN: %s\n' "$*"
  WARNED+=("$*")
}
fail() {
  printf '  FAIL: %s\n' "$*"
  FAILED+=("$*")
}
# run <label> <cmd...>: log to $WORK, show the tail on failure.
run() {
  local label=$1
  shift
  local log="$WORK/$(printf '%s' "$label" | tr -c 'A-Za-z0-9' '_').log"
  if "$@" >"$log" 2>&1; then
    ok "$label"
  else
    fail "$label (rc=$?)"
    tail -25 "$log" | sed 's/^/      /'
    return 1
  fi
}

echo "aiuse release rehearsal: $CURRENT -> $VERSION (nothing will be published)"

step "1. Preflight"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
[ "$BRANCH" = main ] && ok "on main" || warn "on branch '$BRANCH', not main"
if [ -z "$(git status --porcelain)" ]; then
  ok "working tree clean"
else
  warn "working tree dirty: release.py refuses this without --allow-dirty"
fi
if git fetch -q origin 2>/dev/null; then
  BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
  AHEAD=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)
  [ "$BEHIND" = 0 ] && ok "not behind origin/main" || warn "$BEHIND commit(s) behind origin/main"
  [ "$AHEAD" = 0 ] && ok "nothing unpushed" || warn "$AHEAD unpushed commit(s): the release pushes them"
else
  warn "git fetch origin failed"
fi
gh auth status >/dev/null 2>&1 && ok "gh authenticated" || warn "gh not authenticated (release needs it)"
if PUBLISHED=$(curl -fsS --max-time 15 https://pypi.org/pypi/aiuse/json 2>/dev/null |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])' 2>/dev/null); then
  echo "  PyPI currently has aiuse $PUBLISHED; repo is at $CURRENT"
  [ "$PUBLISHED" = "$VERSION" ] && warn "PyPI already has $VERSION: that upload would fail"
else
  warn "could not read the PyPI version"
fi

step "2. Quality gate"
if [ "$QUICK" = 1 ]; then
  run "pytest" uv run --extra dev pytest -q
else
  run "just ci (pytest + pre-commit + just-check)" just ci
fi

step "3. release.py --dry-run $VERSION"
DRY="$WORK/dry-run.log"
if uv run python packaging/release.py "$VERSION" --dry-run --allow-dirty >"$DRY" 2>&1; then
  ok "release.py accepts $VERSION; every step below ran as a dry run"
  sed 's/^/      /' "$DRY"
else
  fail "release.py --dry-run $VERSION (rc=$?)"
  tail -25 "$DRY" | sed 's/^/      /'
fi

step "4. Build sdist + wheel (version $CURRENT, from the current tree)"
DIST="$WORK/dist"
if run "uv build" uv build --out-dir "$DIST"; then
  ls "$DIST" | sed 's/^/      /'
  run "twine check" uvx twine check "$DIST"/*
fi

step "5. Install the wheel in a throwaway venv and smoke-test it"
WHEEL=$(ls "$DIST"/*.whl 2>/dev/null | head -1)
if [ -n "$WHEEL" ]; then
  VENV="$WORK/venv"
  if run "create venv + install wheel" bash -c "uv venv '$VENV' && uv pip install --python '$VENV/bin/python' '$WHEEL'"; then
    for cmd in aiuse ai; do
      OUT=$("$VENV/bin/$cmd" --version 2>&1)
      case "$OUT" in
        "aiuse $CURRENT"*) ok "$cmd --version -> $OUT" ;;
        *) fail "$cmd --version printed '$OUT', expected 'aiuse $CURRENT'" ;;
      esac
    done
    run "aiuse --help" "$VENV/bin/aiuse" --help
  fi
else
  fail "no wheel was built, skipping the install test"
fi

step "6. Homebrew formula"
FORMULA=packaging/homebrew/aiuse.rb
if [ -f "$FORMULA" ]; then
  if command -v ruby >/dev/null 2>&1; then
    run "ruby -c $FORMULA" ruby -c "$FORMULA"
  else
    warn "ruby not found, formula syntax unchecked"
  fi
else
  warn "$FORMULA not found"
fi

step "Result"
if [ "${#WARNED[@]}" -gt 0 ]; then
  printf '  %d warning(s):\n' "${#WARNED[@]}"
  printf '    - %s\n' "${WARNED[@]}"
fi
if [ "${#FAILED[@]}" -gt 0 ]; then
  printf '  %d failure(s):\n' "${#FAILED[@]}"
  printf '    - %s\n' "${FAILED[@]}"
  echo "NOT ready to release."
  exit 1
fi
echo "Rehearsal passed. When ready: just release $VERSION"
