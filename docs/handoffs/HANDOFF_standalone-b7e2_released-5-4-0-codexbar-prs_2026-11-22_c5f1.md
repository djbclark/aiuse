---
schema_version: 1
handoff_id: c5f1
parent_handoff_ids: [8490]
lineage: deterministic
chain: [standalone-b7e2]
repo: aiuse
workspace: aiuse
branch: main
head_sha: aec24c8c9fb6218c857aa13aa73e1edcd3bfb7ae
created_at: 2026-11-22T13:30:00-05:00
writer: claude-code
---

# Handoff — aiuse 5.4.0 (schema 1.8) released; CodexBar PRs waiting

## The Goal

Keep aiuse current after the 5.4.0 release, and move the two CodexBar upstream PRs (5142, 5146) to merge.

## Where We Are

- aiuse **5.4.0** is released (operator asked 2026-11-22): [GitHub](https://github.com/djbclark/aiuse/releases/tag/v5.4.0), [PyPI](https://pypi.org/project/aiuse/5.4.0/), Homebrew tap updated. `main` = origin/main = `aec24c8`, CI green. This supersedes the "unreleased" wording in handoff 8490.
- `CHANGELOG.md` was restructured: the schema 1.8 items are the `[5.4.0]` entry, the already-shipped v4.1.2 items that sat in `[Unreleased]` are a `[4.1.2]` entry, and an empty `[Unreleased]` heading stays on top (markdownlint needs it for the link).
- CodexBar PR 5146 (googledeveloper unapplied credit): behavior proof posted (before/after on the real unclaimed-credit account), PR body updated, `@clawsweeper re-review` requested and acknowledged. Fork build run 45047299202.
- CodexBar PR 5142 (Debug pane sorting): still blocked on screenshots of Settings > Debug. Build for PR head 93396c4 is at `/Users/djbclark/src/CodexBar-fork-build/proof-5142/app/CodexBar.app`; operator takes the screenshots (GUI automation of the Debug sidebar row failed). ClawSweeper also wants an owner decision on alphabetical ordering.
- `frdminc/main` fast-forwarded to upstream `a26ca57b5` (0.85.1 development). No merge needed yet.
- Claude monthly-row quirk: not a zero-limit cost bar (CodexBar's menu card handles `limit <= 0` for Claude); cause still unknown; nothing filed.

## Where We're Going

1. Operator screenshots for PR 5142, then draft the comment, update the PR body, post after an OK, request re-review.
2. Operator creates the Google Cloud billing account; then claim the $100 credit, create an AI Studio key, store it via sudo-secretspec as `GEMINI_API_KEY_DJBCLARK_ULTRA`, add the `googledeveloper` mapping in `collectors/codexbar.py`.
3. Check the Ultra account's Argon fold after about 6 h of collection (count only, never print keys).
4. Watch PRs 5142 and 5146 and issues 5144 and 5145 upstream.
5. Bead `aiuse-mpv` belongs to another session; leave it.

## Detached jobs

none.
