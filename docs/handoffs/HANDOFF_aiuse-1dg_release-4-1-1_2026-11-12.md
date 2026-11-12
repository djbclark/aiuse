# aiuse 4.1.1 release handoff

PR [56](https://github.com/djbclark/aiuse/pull/56) merged as `1c6a846`.
The operator explicitly authorized the merge and full release.

## Delivered

Actionable quota ranking now uses projected unused value per day until reset.
This promotes behind-pace allowances nearing expiry. A one-day floor bounds
urgency; unknown or passed deadlines use 30 days. Shared-budget, depleted,
prepaid, and conserve safeguards remain. The release also includes the existing
unreleased Claude model-cap label fix.

## Release evidence

1. Version commit/tag: `7a2b43b`, annotated `v4.1.1`.
2. [GitHub release](https://github.com/djbclark/aiuse/releases/tag/v4.1.1).
3. [PyPI 4.1.1](https://pypi.org/project/aiuse/4.1.1/) contains wheel and sdist;
   [OIDC publication](https://github.com/djbclark/aiuse/actions/runs/43237556230) succeeded.
4. Canonical formula commit `6cff49e`; tap commit `b257e4b`. Formula copies match.
5. Full preflight CI: 1,918 tests and all quality checks passed;
   [run](https://github.com/djbclark/aiuse/actions/runs/43236570805).
6. `just release 4.1.1 --skip-tests --notes-file /tmp/aiuse-4.1.1-release-notes.md
--tap-path /Users/djbclark/src/homebrew-aiuse-release-4.1.1-wt` completed through
   `bg`. Tests ran in CI, not locally. Homebrew upgrade and `brew test` passed.
7. Exact Homebrew prefix binary, default `aiuse`, and `ai` all report 4.1.1.
   Installed `watch --once` rendered; `aiuse doctor` exited 0 with no hard problems.
   The existing Bedrock billing-unavailable row remains; no collector repair was included.

## Workspaces

Release checkouts are `/Users/djbclark/src/aiuse-release-4.1.1-wt` and
`/Users/djbclark/src/homebrew-aiuse-release-4.1.1-wt`, both claimed for this release.
They are isolated `main` clones because the release tool requires that branch.
Local release log: `/tmp/aiuse-4.1.1-release.log`.
