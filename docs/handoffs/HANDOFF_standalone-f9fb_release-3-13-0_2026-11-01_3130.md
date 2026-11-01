---
schema_version: 1
handoff_id: "3130"
parent_handoff_ids: [dead9]
lineage: none
chain: [standalone-f9fb]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 239775073e5bf0d418bc066cb83f238ce105d1fb
created_at: 2026-11-01T12:52:00-05:00
writer: antigravity
---

# Handoff — Release v3.13.0 and Semgrep CI / Pre-push Scoping

## The Goal

1. Scope the local Semgrep hook to changed files only (`stages: [pre-push]`, `pass_filenames: true`, `exclude: "^.github/"`) and add a GitHub Actions workflow running full Semgrep scans on push/PR, preventing high CPU usage on the Mac.
2. Cut a new full release (`3.13.0`) encompassing all work:
   - Muse Keychain prompt fix (opt-in `AIUSE_MUSE_KEYCHAIN=1` and inactive subscription bypass).
   - Copilot weekly (7-day) and session quota windows in tokscale and codexbar collectors.
   - Copilot quota architecture and rate-limit documentation in `docs/copilot-quota.md`.
   - CI full Semgrep scan workflow and scoped pre-push hook.
   - Local `opencode.json` gitignore.

## Where We Are

Full release `3.13.0` is cut, verified, and live on both PyPI and Homebrew.

Key milestones:

1. **Semgrep Hook Scoping & CI**:
   - Added [.github/workflows/semgrep.yml](file:///Users/djbclark/src/aiuse/.github/workflows/semgrep.yml) to run full Semgrep scan on push and PR.
   - Updated [.pre-commit-config.yaml](file:///Users/djbclark/src/aiuse/.pre-commit-config.yaml) to restrict Semgrep to pre-push with changed files only, excluding `.github/`. Routine commits skip Semgrep entirely.
   - Verified pre-push hook runs in seconds. CI Semgrep workflow completed green.
2. **Release v3.13.0**:
   - Added changelog entry in [CHANGELOG.md](file:///Users/djbclark/src/aiuse/CHANGELOG.md).
   - Executed `just release 3.13.0 --notes-file /tmp/aiuse-3.13.0-notes.md`.
   - All 1549 tests passed.
   - GitHub Release `v3.13.0` created and OIDC publish to PyPI succeeded.
   - Homebrew formula in repo and tap `~/src/homebrew-aiuse` updated and pushed.
   - Local Homebrew installation upgraded to `3.13.0` and tested cleanly.
   - Both `aiuse --version` and `ai --version` on PATH report `aiuse 3.13.0`.
3. **Peer Coordination**:
   - Relayed acknowledgement to `1e61c979` via `relay.py` and received confirmation.
   - Sent `hermes-ping` for release `v3.13.0`.

## Quick Start

```bash
cd ~/src/aiuse && git fetch origin && git pull --ff-only
aiuse --version
ai --version
```
