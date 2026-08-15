---
schema_version: 1
handoff_id: 1abc
parent_handoff_ids: [8411]
lineage: deterministic
chain: [antigravity-d721]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 753076153a493ce7724c7ce08bb7e1bff9489bab
created_at: 2026-08-15T18:24:00-0400
writer: antigravity
---

# Handoff — Fixed JSON Contract Discoverability & Shipped 3.0.20

## The Goal

The user wanted to improve the discoverability of the machine-readable JSON contract format (Issue #28). We implemented changes to direct both users and automated agents to the `--json` output and to easily fetch the schema. We then cut and shipped release `3.0.20`.

## What We Did

1. **Enhanced Normal Output**: Added a dim, unobtrusive note at the bottom of the clock matrix: `AI: Use `aiuse --json` for machine-readable output`.
2. **Enhanced JSON Payload**: The root of the JSON envelopes (both the default full output and the `--alerts-only` output) now advertises the command to fetch the schema directly via the new `contract_command: "aiuse schema"` key.
3. **Docs & Tests Update**: Updated the contract tests (`tests/test_cli.py`, `tests/test_report.py`) to handle the new field and the wider legend. Regenerated the README demo block so the new legend is visible to users on the repo homepage, and updated `docs/json-contract.md`.
4. **Closed Issue #28**: The discoverability gap has been closed.
5. **Released 3.0.20**: Ran `just release 3.0.20` to bump the version, publish to PyPI via OIDC, update the Homebrew tap, and deploy locally. Everything passed smoothly.

## Where We Are

- The project is now at version `3.0.20`.
- All automated checks, linters, tests, and publishing workflows are completely green.
- Issue #28 is resolved.

## What's Next

The remaining priorities (as noted in `docs/next-options.md` and `AGENTS.md`) include:

1. **Issue #30**: macOS Keychain access patterns for version-resilient security helpers.
2. **Issue #16**: Add a second DeepSeek prepaid-balance source.
3. **Issue #10**: Operator announce.

The next agent should check with the user on which of these remaining gaps they want to tackle next, or if they want to move onto other tasks.
