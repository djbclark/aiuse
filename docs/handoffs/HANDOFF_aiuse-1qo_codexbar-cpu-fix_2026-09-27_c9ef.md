---
schema_version: 1
handoff_id: c9ef
parent_handoff_ids: []
lineage: none
chain: [aiuse-1qo, aiuse-e9d, aiuse-oja, aiuse-al9]
repo: aiuse
workspace: aiuse
branch: main
head_sha: d1717b4e4766caf36a4601441499b79838f5fce9
created_at: 2026-09-27T07:28:26-04:00
writer: Codex
---

# Handoff — CodexBar cost-scan CPU fix, LiteLLM spend DB, remaining error rows

## The Goal

Started from a narrow aiuse question — `aiuse watch` displayed collector
errors that plain `aiuse` did not — and expanded into clearing every
`error`-band row on the aiuse board, then into fixing the machine-wide
performance collapse that surfaced mid-session.

Three threads, in the order the operator prioritized them:

1. Make CodexBar stop burning ~139% CPU (it was degrading the whole machine:
   herdr freezing, typing lag).
2. Clear the three persistent `error` rows on the aiuse board (`alibaba`,
   `alibabatokenplan`, `litellm`), all sourced from CodexBar.
3. Finish the LiteLLM auth rollout's loose ends (Goose role apply), leave
   Open WebUI disabled.

## Where We Are

**CodexBar CPU: fixed, verified, shipped upstream.** Root cause found,
patched, tested, packaged, running. PR
[steipete/CodexBar#4053](https://github.com/steipete/CodexBar/pull/4053) is
open and mergeable.

**LiteLLM: done** (previous checkpoint). The `litellm` row moved from
`error` to `mid 66`. Postgres 18 on :5433 + master key + virtual key.
Committed as `0ec46ac` in `ops/site-djbclark` and pushed.

**Alibaba rows: diagnosed, not fixed.** Confirmed they cannot be fixed by
`bl auth` — see Key Decisions.

**Goose role apply: not started.** Filed as `aiuse-al9`.

Repo states:

- `aiuse` — branch `main`, HEAD `d1717b4`, **clean tree**. No code changes
  this session; all aiuse work was beads + verification.
- `CodexBar` — branch `fix-claude-artifact-redecode`, HEAD `741f5cbe1`,
  based on `origin/main`. Untracked only: `.ignore`, `graft/`. The older
  `fix-infinite-loop-daykeys` branch (PR #4045) still exists locally.

## What We Tried

Chronological, including the dead ends — these are the expensive ones.

**1. Assumed the `aiuse watch` errors were a watch-specific bug.** They were
not. Plain `aiuse` prints collector errors in a different screen location;
`watch` surfaces them. Display-location artifact, not a logic difference.
The _underlying_ OpenCode breakage it exposed was real and got fixed in
3.0.39 (previous checkpoint).

**2. Hunted for an existing LiteLLM credential across five stores.** Failed,
and this consumed significant time: `sudo-secretspec` (50 secrets),
`~/.hermes/.env` (20 vars), `~/ops/*/.env`, macOS Keychain, `hermes vault` /
`hermes secrets`. Nothing. The vendor keys described as "for the litellm
proxy" are _upstream_ provider keys — the opposite direction from what
CodexBar needs. The value Hermes was sending turned out to be a **placeholder**, not a
credential, existing only to satisfy OpenAI-client shape checks. Confirmed
independently via headless `claude -p`. Conclusion: no credential existed
because the proxy had **no auth at all** — security was purely the loopback
bind. (That same placeholder string has since become the proxy's real
master key, so treat it as a credential going forward; it lives in the
site-djbclark `roles/litellm` defaults.)

**3. Tried to share Hindsight's Postgres cluster.** Rejected. Homebrew
`postgresql@18` had silently failed to start because Hindsight's embedded
pg0 cluster already held :5432 — visible _only_ in
`/opt/homebrew/var/log/postgresql@18.log`; `brew services list` still
reported it as started. Moved to :5433 as an independent cluster, because
LiteLLM refuses to boot on an unreachable `database_url`, so sharing would
let a Hindsight outage take down every LLM client.

**4. Minted a CodexBar virtual key without `user_id`.** Failed:
CodexBar's bundled `litellm.js` requires key info to carry a `user_id` or
`team_id`. Regenerated with `user_id` and created a `codexbar` user
(`/user/new`, $50/30d), or `/user/info` 404s.

**5. Blamed the new Postgres for the machine slowdown.** Ruled out — 0.0%
CPU, idle. `top -l 3` then fingered CodexBar at 139% CPU / 617 MB.

**6. Assumed the CPU burn was the `dayKeys` infinite loop I had already
fixed.** Wrong. Checked build timestamps: the running bundle was built
23:41, 19 minutes _after_ commit `77f703fb6` (23:22), so it already
contained that fix. This was a **new, unrelated** hot path.

**7. Tried to symbolicate the sampled frames with `atos`.** Failed — the
packaged binary is stripped, so every CodexBar frame came back as a bare
offset. Had to localize by grepping the dispatch-queue label strings
instead.

**8. Seeded the artifact memo inside `save()`.** Built and ran; broke 18
assertions across `CostUsageScannerClaudeMemoTests` and
`CostUsageClaudeKimiAliasTests`, all of the form `cacheDecodes == 1`,
because the memo eliminated the decode those tests deliberately pin.
Reverted the `save()` seeding. The steady-state win comes entirely from the
unchanged-artifact read path, so dropping the seed cost nothing and
preserved the existing "one decode per scan" contract.

**9. Set memo capacity to 2.** Reconsidered before committing: the app can
hold four artifacts at once (Claude and Vertex x regular and
spend-dashboard), so 2 would thrash in steady state and make the new test
order-dependent. Raised to 4.

**10. Initial `gh pr create` with a heredoc body.** Failed on shell quoting
(an apostrophe in the body). Wrote the body to a file and used
`--body-file`.

## Key Decisions

**Memoize the Claude artifact against its file stamp** (chosen).
Rejected alternatives:

- _Switch to `JSONSerialization` + hand-rolled mapping_ — would keep the
  190 ms parse but throw away type safety across a 17-field row struct, and
  still re-parse every scan.
- _Make the decode incremental / streaming_ — far larger change to a
  vendored file, and unnecessary: the artifact only changes when the
  scanner itself rewrites it.
- _Lengthen the scan interval_ — treats the symptom, and upstream already
  has an open feature request for exactly that (#3770); does not stop the
  redundant decode.

**Do not seed the memo on `save()`** — see What We Tried #8. Identical-content
saves are already skipped upstream (#3909), so the stamp is unchanged and the
_next_ scan hits the memo anyway. Keeping the seed out preserved 18 existing
assertions.

**Do not memoize a read whose stamp changed between the `stat` and the
read** — guards the concurrent-rewrite race; that read falls through to a
fresh decode next time.

**Branch off `origin/main`, not off `fix-infinite-loop-daykeys`** — keeps
the CPU fix independently reviewable and mergeable, since PR #4045 is still
open and has unaddressed review feedback.

**The alibaba rows cannot be fixed by `bl auth`** — they come from
CodexBar's _cookie-based_ Alibaba providers, not the `bl` CLI. Verified:
after the operator's `bl auth`, `bl usage token-plan --output json` returns
`{}` (no longer "not logged in"), so `bl` auth genuinely succeeded — yet
both rows still error. Options recorded in `aiuse-oja`; no action taken
because both have real cost (suppressing hides a genuine gap; re-sourcing
from `bl` is blocked on `bl` exposing usable data — `token-plan` is `{}`
and `quota list` 404s).

**Open WebUI left disabled** — explicit operator instruction. Its plist
already carries the correct key, so it needs no change when re-enabled.

## Evidence & Data

**Root cause.** Claude cost scans fully re-decoded
`~/Library/Caches/CodexBar/cost-usage/claude-history-v6.json` on every run.

- Artifact: **9,275,124 bytes**, 318 files, 31 days, **24,135** nested row
  elements (max 797 per file).
- Row type `CostUsageScanner.ClaudeUsageRow` has **17 fields**
  (`CostUsageScanner.swift:1992`) — roughly 410k field decodes per pass.
- Measured directly with a standalone Swift binary: raw
  `JSONSerialization` parse = **190 ms**. `Codable` decode of the same
  bytes = seconds. **~25x overhead.**

**`sample <pid> 5` of the pathological process:**

- 888 / 892 samples on `com.steipete.codexbar.cost-usage-store` were inside
  a _blocking_ `dispatch_sync`.
- 786 of those inside `JSONDecoder.decode`, dominated by `_arrayForceCast`
  / `swift_dynamicCast` / `_DictionaryCodingKey` — Swift's slow keyed-decode
  path.
- `com.steipete.codexbar.cost-usage-scan` (150 samples) was merely blocked
  behind it.
- **Main thread was idle** (`mach_msg`) the whole time.

**Before / after, same machine:**

| Metric                          | Before        | After                       |
| ------------------------------- | ------------- | --------------------------- |
| CodexBar CPU                    | ~139%         | 0–2.7% (1.9% at last check) |
| CodexBar RSS                    | ~617 MB       | ~61–100 MB                  |
| System load avg                 | 292 (8 cores) | 6.8                         |
| `cost-usage` queues in `sample` | saturated     | absent                      |

Other system state at the worst point: 879 processes, 20 zombies, 67
runnable, swap 6.1G/7.2G, 16 `mdworker`.

**Files changed (CodexBar, commit `741f5cbe1`):**

- `Sources/CodexBarCore/Vendored/CostUsage/CostUsageClaudeCache.swift` —
  added `ArtifactMemo` (stamp-keyed, `NSLock`, capacity 4, LRU by
  generation), `memoKey(for:)`, `validated(_:calendar:)`, and
  `evictArtifactMemoForTesting()` under `#if DEBUG`; rewired `load()`.
- `Tests/CodexBarTests/CostUsageClaudeWriteAmplificationTests.swift` — new
  test `unchanged cache artifacts decode once and rewrites invalidate the
memo`.

**Tests run:**

- `swift test --filter 'CostUsageClaude|CostUsageScannerClaude|SpendDashboardFreshness'`
  → **109 tests / 17 suites passed**, including every pre-existing
  `cacheDecodes == 1` assertion.
- `swift test --filter CostUsage` → 849 tests, 7 issues. **All pre-existing.**
  Proved it: stashed the change, re-ran
  `CostUsageBoundedProgress|CostUsageScannerCodexPriorityCursor|CostUsagePerformanceGate`
  on a clean tree → identical 4 `CostUsagePerformanceGateTests` failures
  (lines 2075/2076). The other 3 were parallelism flakes that did not
  reproduce when the suites ran narrowed.
- `swiftformat Sources Tests` → 0/2675 files changed. `swiftlint --strict`
  → exit 0.
- `Scripts/package_app.sh` → launch smoke check OK; relaunched and sampled
  over ~5 minutes.

**aiuse board after the fix** (39.9s run, 18 accounts): OpenCode Go/Zen
errors gone (3.0.39 holds), `Litellm` at `mid 66`, `devin` returning data
rather than burning the 45s timeout. Only the two alibaba rows still error.

**Upstream activity:**

- Opened PR #4053 — mergeable, GitGuardian green.
- Commented on issue #3882 — that reporter's `dispatch_sync` stack and
  "steady ~10.5 MB per step" match this artifact exactly. Noted this is
  **distinct** from the re-decode fixed in #3840 (which did not cover the
  Claude/Vertex JSON path in `CostUsageClaudeCacheIO`), and that the RSS
  growth in #3247 / #3323 may share this cause.
- PR #4045 (dayKeys clamp) still OPEN, with ClawSweeper feedback: _"needs
  real behavior proof before merge."_ Unaddressed.

**Beads:** closed `aiuse-c77`, `aiuse-g18`. Filed `aiuse-e9d`,
`aiuse-al9`, `aiuse-1qo`. Commented on `aiuse-e9d`, `aiuse-oja`. Synced via
`bd dolt pull` / `bd dolt push`. **31 total: 10 open → 12 open** after this
session's two new beads.

## Operator Feedback

- "Do everything you can to the maximum extent possible" — on clearing the
  error rows; chose to _configure_ LiteLLM rather than suppress it, which is
  what escalated into standing up Postgres.
- Authorized working in `~/src/CodexBar` directly: "You have auth to work on
  that repo."
- "codexbar is having insane cpu use again" — the word _again_ was the cue
  that sent me to check git log and the upstream tracker, which is how the
  distinction from #3840 and from my own #4045 fix got established.
- "Leave Open WebUI disabled."
- "After you fix the newest CPU issue be sure to update any issues or PRs."
- "yes file them" — authorizing the two new beads.

## Where We're Going

1. **Address ClawSweeper review feedback on PR #4053** once it posts, and
   separately give PR #4045 the "real behavior proof" its review asked for.
   These are the only things blocking two shipped fixes from landing.
2. **`aiuse-al9` — Goose role re-adopt.** Operator already authorized the
   bypass. Back up `~/.config/goose/config.yaml`, merge any hand-edits into
   the role template, add the managed marker (or delete the file), re-run
   `just goose-apply`, and **prove Goose auth end-to-end** — it is currently
   unverified because the live config and keyring entry were set by hand.
3. **`aiuse-e9d` — re-measure collector timings** now that CodexBar is no
   longer CPU-saturated. Decide whether `alibabatokenplan`/`devin` still
   need suppression or a lower per-provider timeout.
4. **`aiuse-oja` — decide the alibaba path**: suppress the two providers via
   `[collectors.codexbar] providers`, or wait for `bl` to expose usable plan
   data. Needs an operator call; both options have real cost.
5. Once #4053 merges, reinstall CodexBar from a release rather than the
   local packaged bundle (currently running a locally-built app).

## Quick Start

```bash
# CodexBar — the fix, and its PR
cd ~/src/CodexBar && git branch --show-current   # fix-claude-artifact-redecode
gh pr view 4053 --repo steipete/CodexBar --json state,mergeable,comments
gh pr view 4045 --repo steipete/CodexBar --json comments   # needs behavior proof

# Re-run the suites that matter (fast; full CostUsage has 4 PRE-EXISTING failures)
swift test --filter 'CostUsageClaude|CostUsageScannerClaude|SpendDashboardFreshness'
swiftformat Sources Tests && swiftlint --strict

# Confirm CodexBar is still calm
ps -Ao pid,%cpu,rss,comm | grep 'CodexBar.app/Contents/MacOS/CodexBar' | grep -v grep
uptime

# aiuse state
cd ~/src/aiuse && bd list --status open
bd show aiuse-al9    # Goose re-adopt (next real work)
bd show aiuse-1qo    # CodexBar PR tracking
aiuse                # ~40s; expect only the 2 alibaba error rows

# Goose blocker
grep -n 'site-djbclark managed' ~/.config/goose/config.yaml   # expect: no match
sed -n '80,95p' ~/ops/site-djbclark/roles/goose/tasks/main.yml  # the guard
```

Note: `~/src/CodexBar` builds are slow (release package ~10 min, test target
link ~15 min). Budget for it.
