#!/usr/bin/env bash
# Run aiuse from this source tree (the newest, unreleased code) instead of the
# installed pipx/Homebrew copy. Use it to try changes before `just release`.
#
#   ./aiuse-test.sh [aiuse args...]     e.g. ./aiuse-test.sh --available
#
# Works from any directory; arguments pass straight through to `aiuse`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --project "$ROOT" aiuse "$@"
