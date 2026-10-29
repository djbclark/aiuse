---
schema_version: 1
handoff_id: af7c
parent_handoff_ids: [bc29]
lineage: deterministic
chain: [standalone-a351]
repo: aiuse
workspace: billing-pace-bedrock
branch: main
head_sha: a85d39cb06b4f407ce5d4693e63389b1319a4d70
created_at: 2026-10-29T17:41:20.951196-04:00
writer: codex
---

# Handoff — aiuse 3.11.0 released and installed

## The Goal

Completed: audit the previous agent's work, repair omp pace and the AWS spend
row, publish a full minor release, then leave durable context and end this
session. No continuation work is required for the release.

## Where We Are

[GitHub 3.11.0](https://github.com/djbclark/aiuse/releases/tag/v3.11.0) and
[PyPI 3.11.0](https://pypi.org/project/aiuse/3.11.0/) are published. Homebrew
3.11.0 is installed and `brew test djbclark/aiuse/aiuse` passed. The release
script completed successfully through `just release 3.11.0 --allow-dirty`
with an isolated `--tap-path`.

Release tag commit: `53f0f3a`. Canonical formula commit: `a85d39c`.
Tap commit: `a6b135b`. The frontmatter records HEAD before this handoff commit.
Only unrelated untracked `opencode.json` remains; preserve it.

The owned workspace is `/Users/djbclark/.cow/pastures/aiuse/billing-pace-bedrock`.
The tap workspace is `/Users/djbclark/.cow/pastures/homebrew-aiuse/release-3-11-0`.
Shared checkouts are fast-forwarded after publication. Do not develop there.

## What We Tried

A measured service-only Bedrock total of $3.45 did not match the requested
AWS Billing home account total. Net cost was zero because promotional credits
offset charges. Neither was the requested measure. The operator selected
whole-account month-to-date charges before credits, with no breakdown.

Adding a monthly clock only in CodexBar's omp parser was insufficient: the
Admin API override replaced the window and discarded duration. Existing
cached override rows also took an early return. Both paths now retain pace.

## Key Decisions

Keep `bedrock` provider/config identity and `codexbar` collector identity for
compatibility; label the displayed amount `AWS MTD`. Sum all AWS service
charges before credit/refund offsets and show one amount plus measurement time.
No category, credit, or net breakdown appears on the board. Cache identity
includes account, UTC billing month, and meter scope; old Bedrock-only dollars
are never relabelled as account totals. Paid reads retain the six-hour gate.

omp uses matching grant start and expiry, otherwise a nominal month. Active
Admin API grant metadata wins against incompatible CodexBar promotional rows.
The installed wide board showed -20% pace with $80/$200 remaining.

## Decisions

- [decision] Ship 3.11.0; rejected: another patch release; because the operator explicitly requested incrementing Y.
- [decision] Show overall AWS MTD only; rejected: Bedrock-only dollars and category breakdowns; because the operator selected the account total and asked for a single amount.
- [decision] Installed watch smoke precedes version probes and formula tests; rejected: testing watch after another usage command could warm its state.

## Relations

- continues [[HANDOFF_standalone-a351_bedrock-unknown-release_2026-10-29_bc29]]
- decided_in [[HANDOFF_standalone-a351_release-3-11-0_2026-10-29_af7c]]

## Evidence & Data

[Publish workflow](https://github.com/djbclark/aiuse/actions/runs/40958938725)
succeeded, including metadata checks and PyPI OIDC publishing. Full pytest:
1484 passed in 20.73s. The watch-order regression and release-script suite:
20 passed. Release pre-push all-files quality gates passed.

The exact installed binary `/opt/homebrew/opt/aiuse/bin/aiuse` first ran watch,
then reported `aiuse 3.11.0`. Its wide watch board showed omp -20%, $80/$200,
and `↑ $18.19 AWS MTD (as of 10/29 5:02pm)`. $16.89 was an earlier reading;
do not hard-code either figure. The 5:02pm value was verified through native
AWS credentials belonging to the operator's console account.

Durable implementation notes: [AWS meter](../bedrock-quota.md) and
[omp credits](../omp-cloud-credits.md). Billing code commits: `688bc4c`,
`62b4b36`, `5146e44`. Release watch-order commit: `8f849bf`.
Release-unit files: `packaging/release.py`, `tests/test_release_script.py`,
`pyproject.toml`, `src/aiuse/__init__.py`, `uv.lock`, `docs/packaging.md`,
`packaging/homebrew/aiuse.rb`, `AGENTS.md`, `docs/current-status.md`, and this
handoff. Earlier implementation paths: `git diff --name-only c39c082..5146e44`.

## Operator Feedback

The operator confirmed it works and authorized the full minor release.
Use `aiuse watch` as the first usage command when verifying an installation.
Do not use `--all-providers` or watch's `a` sweep without session-specific
permission. OpenCode Go/Zen remain disabled by operator configuration.

## Where We're Going

1. Release is complete; no automatic follow-up implementation. If the operator
   chooses the separate TLS issue, inspect `bd show aiuse-hap` first.
2. SIPB TLS finding remains open and separate: its expiry exception uses
   `verify=False`, bypassing hostname and issuer validation as well. It was
   deliberately outside this billing repair.
3. [CodexBar issue 4846](https://github.com/steipete/CodexBar/issues/4846)
   reports credit-netting and unavailable-versus-zero handling upstream.
4. If task IDs are absent in a checkout, run `bd dolt pull`; then inspect them.

## Detached jobs

None. All subagent reports and release-process output were collected.
The two owned `/tmp/aiuse-*-live-20261029.json` diagnostic stores and locks were
removed; the actual default measured cache was preserved.

## Quick Start

```bash
cd /Users/djbclark/src/aiuse
aiuse watch --once
aiuse --version
bd dolt pull
bd show aiuse-hap
```

A document-only handoff is retained because this task is complete. The
operator authorized ending the session; finish/quit replaces a new baton cycle.
