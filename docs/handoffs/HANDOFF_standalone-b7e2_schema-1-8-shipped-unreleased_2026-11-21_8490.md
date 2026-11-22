---
schema_version: 1
handoff_id: 8490
parent_handoff_ids: [b7e2]
lineage: deterministic
chain: [standalone-b7e2]
repo: aiuse
workspace: aiuse
branch: main
head_sha: ecd583b9b79f76280b714745e2a96ca8f48c87f0
created_at: 2026-11-21T23:55:00-05:00
writer: claude-code
---

# Handoff — aiuse schema 1.8 built, reviewed, unreleased; CodexBar 0.85.0 fork installed

## The Goal

Ship aiuse JSON schema 1.8 on main (unreleased, release only on the operator's ask) so the ADE lead can build queue 88 on `unavailable[]`, per-service floors and the corrected `available_at`; keep CodexBar (fork build 0.85.0) healthy and its upstream PRs moving; add Google Cloud/Developer Program credit to aiuse once the operator supplies a billing account.

## Where We Are

- aiuse `main` = origin/main = `ecd583b`, tree clean, CI test+semgrep green on every commit since `abe8459`; full suite 2244 passed.
- Schema 1.8 shipped to main, UNRELEASED (released version is still 5.3.2): `unavailable[]` in `--available --json` (reasons exhausted/near_empty/rate_gated/no_reading, plus blocked-pool fields), `available_at` = latest reset among depleted windows and null when a depleted window has no reset, omp account `available_at` = later of gate and own windows, Argon folds into shared Gemini only on per-account evidence (3+ collected readings over 6h+, store `~/.local/state/aiuse/argon-evidence.json`, written only on the collection path), refresh-gate fields (`refreshable_at`, `refresh_blocked_by_gate`, `gate_remaining_seconds`) derived from the throttle state, floors (`floor_percent`, `floor_basis`, `headroom_above_floor_percent`; `[floors]`, `[floors.providers.*]`, `[floors.pool_families.*]`; `openai_api` prepaid default 10.0).
- Adversary verdicts: BLOCK at abe8459 (6 important), BLOCK at 1cb4793 (2 important, evidence store), APPROVE at 1ad3be3 (0 important). Round-3 nits fixed in `15ce595`, `e0b43bc`, `97a4981`, `ecd583b`. Reports under `/Users/djbclark/.local/state/agent-reports/53cbec7a-2384-40be-8891-5461934c5e24/` (`aiuse-schema-1-8-adversary-report.md`, `...-round2-report.md`, `...-round3-report.md`, `fix-aiuse-1-8-*-report.md`).
- ADE lead is now 37cee5fe-f46d-4f45-8c6c-cc81e86ff401 (leads changed many times this session); it has the FINAL 1.8 notice and acked it (queue 88 may build on 1.8). Nothing owed to ADE unless a successor lead asks again.
- CodexBar 0.85.0 fork build installed at `/Applications/CodexBar.app`, re-signed with the operator's Apple Development team (TeamIdentifier XWZC8G497C); widget snapshot works at `~/Library/Group Containers/XWZC8G497C.com.steipete.codexbar/widget-snapshot.json`. Procedure and Debug-screen/log notes: `docs/codexbar-debugging.md`. Backups: `/Applications/CodexBar-0.84.1.app.bak`, `/Applications/CodexBar-0.85.0-adhoc.app.bak`.
- Upstream (steipete/CodexBar), all OPEN at last check: PR 5142 (Debug pane provider lists sorted), PR 5146 (googledeveloper unapplied credit), issue 5144 (antigravity placeholder resetsAt), issue 5145 (googledeveloper parse failure). CI on both PRs green.
- Tier 1 pointer refreshed (`~/.local/state/handoffs/chains/standalone-b7e2/SESSION_LOG.md`, head_sha ecd583b).
- Files changed this session: `src/aiuse/analysis/{selfdescribe,floors,argon_evidence,pace,frozen_quota,quota_refusals}.py`, `src/aiuse/{config,cli,history,serve}.py`, `src/aiuse/collectors/throttle.py`, new tests (`test_unavailable_pools`, `test_floors`, `test_argon_evidence`, `test_available_at_latest`, `test_refresh_gate_state`, others), `config/config.example.toml`, README, `docs/{json-contract,antigravity-pools,codexbar-debugging,current-status,agent-doc-map,index}.md`, CHANGELOG `[Unreleased]`.

## What We Tried

- Stale CodexBar widget snapshot: the 10 s I/O circuit-breaker hypothesis was wrong (no warning logged); Trace logging adds nothing on that path. Real cause: the ad-hoc fork build's team-prefixed `application-groups` entitlement is ignored without a valid team signature and the kernel sandbox denies the writes (find with `log show` on process kernel). First re-sign used the id from the certificate name (R7NDWQV585); the right id is the `TeamIdentifier` from `codesign -dvv` (XWZC8G497C).
- `rm -rf` of an old signing directory was refused by a safety check; a fresh directory was used and nothing was deleted (the wrong-team copy `/Users/djbclark/src/CodexBar-fork-build/resign-R7NDWQV585/` is unused and still there).
- First 1.8 build was blocked by the adversary three times: unknown-reset windows made `available_at` name a time the pool was still out; the Argon fold keyed on one reading's missing window; refresh-gate fields ignored the throttle; the evidence store crashed on corrupt records and was written from read paths. All fixed, tests fail on the pre-fix source.

## Key Decisions

Floor formula `clamp(reserve_usd / plan_price * 100, 2, 20)` with reserve 10 (a $199.99 plan gives 5.0, a $10 plan 20.0, no price 10.0 "default"); floors are advisory and never change `headroom_percent`, `usable_now` or the 5% near-empty rule. `binding_window` keeps its name though it means the latest-resetting depleted window in `unavailable[]` and the least-headroom window elsewhere (documented, not renamed, to avoid breaking a day-old contract). After upgrade an existing Ultra account folds nothing for at least 6 h and 3 readings and an Argon refusal then leaves the shared gemini pool usable (fails open); the unnamed Antigravity account re-keys once, restarting its clock.

## Decisions

- [decision] floors default reserve 10 USD clamped 2..20 percent, advisory only; rejected: changing headroom_percent or usable_now by the floor; because consumers (ADE router) own the routing policy and the raw number must stay stable
- [decision] Argon folds into the shared gemini family only on per-account evidence (3+ collected readings over 6h+ with a shared Gemini window and no Argon window); rejected: folding whenever a reading lacks an Argon window; because hidden or unreadable windows turned Argon notes into false Flash exhaustion
- [decision] evidence store written only on the collection path; rejected: recording on every enrichment; because read paths must not write durable state and re-reading one cache must not advance evidence
- [decision] openai_api prepaid pool gets its own floor default via [floors.pool_families]; rejected: borrowing the Codex plan price; because prepaid credit is not part of the plan
- [decision] keep the key name binding_window and document its two meanings; rejected: renaming to blocking_window; because 1.8 was unreleased only for hours and consumers were already told the names
- [ruling] "Do both now." (djbclark, 2026-11-21)
- [ruling] "Make sure this is all documented, including by our example toml file, and make sure what all the new floor_percent etc. are is covered in our toml config comments. Go ahead and do the advesarial review first" (djbclark, 2026-11-21)
- [ruling] "1. Per-pool-family floor override (Recommended)" chosen for the Codex prepaid floor (djbclark, 2026-11-21)
- decided_in [[HANDOFF_standalone-b7e2_schema-1-8-shipped-unreleased_2026-11-21_8490]]

## Relations

- continues [[HANDOFF_standalone-b7e2_antigravity-ultra-floors-codexbar_2026-11-21_b7e2]]

## Evidence & Data

- Real `unavailable[]` item (account elided): `{"provider":"opencode-go","pool_id":"opencode:go","reason":"exhausted","binding_window":"OpenCode Go weekly","available_at":"2026-11-23T00:00:00+00:00","headroom_percent":0,"floor_percent":20,"floor_basis":"reserve_usd","headroom_above_floor_percent":-20}`.
- Live floors: antigravity 5.0 ($199.99), claude 5.0, codex default pool 5.0, codex openai_api 10.0 default, clinepass/opencode-go/grok 20.0 (cap), omp 10.0 default.
- Adversary round 3: 25 new tests fail on the 1cb4793 source, 250 passed on HEAD; fold probe: Ultra shape folds on the third collected reading over 6.1 h, Pro with an Argon window or hidden Argon never folds.

## Operator Feedback

The operator wanted the schema work done now with an adversarial review first, everything documented including every new field in the example TOML comments, and the Codex prepaid floor fixed with a per-pool-family override. The operator asked to be re-asked unanswered questions after the session was interrupted.

## Where We're Going

1. Release 1.8 only when the operator asks: `just release X.Y.Z` (minor bump judgment: new schema = minor; see `docs/packaging.md`); CHANGELOG `[Unreleased]` already lists every item.
2. Google Cloud: operator must create a billing account (card), then claim the $100 Developer Program credit, create an AI Studio key, store it via sudo-secretspec (planned name `GEMINI_API_KEY_DJBCLARK_ULTRA`), then add a `googledeveloper` mapping in `collectors/codexbar.py` once real data exists. Blocked on the operator.
3. Watch CodexBar PRs 5142 and 5146 and issues 5144/5145 upstream (`gh pr view`); answer maintainer comments; consider enrolling CodexBar in `forks.toml`.
4. Diagnose the Claude monthly row quirk in the CodexBar UI (CLI row identical in 0.84.1 and 0.85.0; try Debug > Fetch strategy attempts for Claude); confirm the widget on screen; keychain prompts or Full Disk Access (Cursor cookies) may need re-granting for the new signature.
5. Check on the Ultra account's Argon evidence after about 6 h of collection (`jq` on the store shows counts, never print keys): confirm the fold turns on.
6. Bead `aiuse-mpv` (Pi OpenAI grant refresh) is in progress and belongs to another session; leave it.

## Detached jobs

none (no old-shim job records known; all sub-agents of this session completed and were read; the bigteam job directory could not be globbed because the secret-read gate fails closed on that glob).

## Quick Start

```
git -C /Users/djbclark/src/aiuse fetch origin && git -C /Users/djbclark/src/aiuse pull --ff-only
cd /Users/djbclark/src/aiuse && AIUSE_SKIP_HOMEBREW_FORMULA=1 ./aiuse-test.sh --available --json   # schema 1.8, unavailable[] present
.venv/bin/python -m pytest -q      # via ~/ops/site-private-djbclark/bin/bgrun
gh pr view 5142 --repo steipete/CodexBar --json state,comments
gh pr view 5146 --repo steipete/CodexBar --json state,comments
```
