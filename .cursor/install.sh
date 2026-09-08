#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for aiuse.
#
# Mirrors the GitHub `test` workflow toolchain (uv + just + bun) so a Cloud
# Agent can run the same commands CI runs: `uv run pytest`,
# `uv run pre-commit run --all-files`, and `just --fmt --check`.
#
# Safe to re-run: every tool install is guarded, and `uv sync` /
# `bun install` converge against the checked-in lockfiles without rewriting
# them.
set -euo pipefail

# Resolve the repo root from this script's location so the command works no
# matter what directory `install` is invoked from.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# User-local bin dirs are already on PATH in this image, but export them here
# so freshly installed tools are visible within this same script run.
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"

# --- uv: Python interpreter + virtualenv + dependency manager ---
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# --- just: task runner used by the justfile and CI ---
if ! command -v just >/dev/null 2>&1; then
  echo "Installing just..."
  curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to "$HOME/.local/bin"
fi

# --- bun: runs the docs lint/format tools via bunx (prettier, markdownlint) ---
if ! command -v bun >/dev/null 2>&1; then
  echo "Installing bun..."
  curl -fsSL https://bun.sh/install | bash
fi

# Pin Python 3.11 to match the CI matrix and pyproject target-version.
uv python install 3.11

# Create/refresh the .venv with runtime + dev dependencies from uv.lock.
uv sync --extra dev --python 3.11

# Documentation tooling (prettier, markdownlint) from the frozen bun lockfile.
bun install --frozen-lockfile

# Pre-fetch pre-commit hook environments (ruff, typos, yamllint, bandit,
# gitleaks) so the `pre-commit run --all-files` quality gate is fast on first
# use and its downloads are captured in the environment snapshot.
uv run pre-commit install-hooks

echo "aiuse Cloud Agent environment ready."
