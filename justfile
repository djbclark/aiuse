# aiuse — local test, lint, format, and security checks.

set shell := ["bash", "-uc"]

# Install external data-source tools (cswap, codexbar, caut, OpenUsage, tokscale).
install-deps:
    ./packaging/install-deps.sh

# Report missing data-source tools (exit 1 if any missing).
install-deps-check:
    ./packaging/install-deps.sh --check

# macOS: guided codesign + Keychain Always Allow setup for caut (see docs/macos-keychain-trust.md).
macos-trust:
    aiuse trust setup

macos-trust-status:
    aiuse trust status

macos-sign-caut:
    aiuse trust sign-caut

macos-trust-guide:
    aiuse trust grant-guide

# CodexBar#679: trust CodexBarCLI on com.steipete.codexbar.cache items.
macos-fix-codexbar-cache:
    aiuse trust fix-codexbar-cache

macos-fix-codexbar-cache-dry:
    aiuse trust fix-codexbar-cache --dry-run

# Run the Python test suite.
test:
    uv run --extra dev pytest

# Ruff lint and format verification.
ruff:
    uv run --extra dev ruff check .
    uv run --extra dev ruff format --check .

# Static Python type checking.
mypy:
    uv run --extra dev mypy src tests

# YAML linting.
yamllint:
    uv run --extra dev yamllint -c .yamllint .github .pre-commit-config.yaml .yamllint config

# Markdown linting.
markdownlint:
    bunx markdownlint --config .markdownlint.json README.md

# Markdown and TOML formatting verification.
prettier:
    bunx prettier --plugin=prettier-plugin-toml --check README.md pyproject.toml

# Source-code spelling check.
typos:
    typos

# Python security linting.
bandit:
    uv run --extra dev bandit -ll -r src

# Pattern-based security scan.
semgrep:
    semgrep scan --config auto --quiet --error --exclude .github

# Secret-leak scan.
gitleaks:
    gitleaks detect --no-banner --redact

# Verify the justfile itself.
just-check:
    just --fmt --check
    just --list >/dev/null

# Fast, deterministic code and documentation checks.
check: test ruff mypy yamllint markdownlint prettier typos just-check

# Full lint and security suite.
lint: check bandit semgrep gitleaks

# Apply deterministic formatters and safe Ruff fixes.
format:
    uv run --extra dev ruff check --fix .
    uv run --extra dev ruff format .
    bunx prettier --plugin=prettier-plugin-toml --write README.md pyproject.toml
