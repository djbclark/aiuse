---
schema_version: 1
handoff_id: de16
parent_handoff_ids: []
lineage: none
chain: [aiuse-8tz]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 7025dc9b97324bc74a9fe788b72a388fa4ebb1c7
created_at: 2026-10-15T08:40:00-04:00
writer: claude-code
---

# Handoff — omp row (Claude cloud-session credits), OTHER column, operator gate, release 3.7.1

## The Goal

djbclark saw an expiring "Cloud session credits" balance on claude.ai
Settings → Usage ($249 of $250, expires Nov 5) that no aiuse line showed. The
session added it as a new provider `omp` (the omp TUI runs Claude through the API
and spends these credits), then reshaped the matrix around it, gated it against AI
use, and shipped it.

## Where We Are

Done and released. aiuse **3.7.0** (cut by another session in this checkout at
12:13Z) and **3.7.1** (cut here at 12:25Z) are on GitHub, PyPI and Homebrew; the
tree is clean and in sync with `origin/main`. Full suite 1203 passed at 3.7.1;
`just ci` green at HEAD.

1. **Source of the data:** CodexBar already reads it. `codexbar usage --provider
claude` carries a `claude-cloud-credits` row under `usage.details`
   (upstream field `iguana_necktie`). `collectors/codexbar.py:_omp_credits_from_row`
   derives a separate `omp` account from the Claude row (same pattern as OpenCode
   Zen from the Go row): one `credits` window whose `resets_at` is the expiry,
   `balance_usd` = left, new `spent_usd` = spent, `refill_capacity` = grant size.
2. **Matrix:** SCOPE `claude` (new independent pool `omp_claude` in
   `analysis/pace.py`). Column order is now `## SERVICE ACCT SCOPE PACE 5H WEEK
MONTH OTHER`. OTHER (last, left-aligned, only when a row has a note) carries omp
   `$0.73/$249.27` (spent/left), `Fable-cap`, and wallets (grok `$1.64`; the
   " (counts down)" suffix is dropped in the matrix only).
3. **AI line placement:** the "AI: Use aiuse --json for machine-readable output"
   line is line 3 of the watch screen and the first line of the stderr meta in
   plain output (the third line overall); under `-q` it stays at the bottom of
   the legend. `aiuse watch` now puts the legend and the "Collected at" and
   "Detail" lines above the table, nothing below it
   (`report.py:render_clock_matrix_parts`, `watch.py:render_watch_board`).
4. **Operator gate:** the omp account, its alerts and `--available` entries carry
   `requires_operator_approval: true` and `ai_notice` ("AI: Do not use without
   asking operator"); the matrix shows `(AI: Do not use without asking operator)`
   centered across the empty 5H/WEEK cells (short fallback `(AI: ask operator)` on
   narrow terminals); `--for-chat` adds a `↳` line; alert messages carry the
   suffix. The single text constant is `models.OPERATOR_APPROVAL_NOTICE`.
   **Enforcement is not in aiuse:** `acp-run` and `acp-dispatch` (djbclark-ade
   `171980a`, test `f3ffe98`) refuse an omp slice unless `ACP_OPERATOR_APPROVED=omp`
   is set for that call; `bigteam`, `model-routing` and `home-agents.md` item 26
   carry the rule. Docs say so (commit `b14789f`).
5. **JSON/contract/docs:** `spent_usd`, `requires_operator_approval`, `ai_notice`,
   omp `pool_family: "claude"`, `--available` entries and summary lines; schema
   stays **1.3** (additive entries dated 2026-10-15 in `docs/json-contract.md`).
   New docs: `docs/omp-cloud-credits.md`; edits to `provider-identity.md`,
   `watch-mode.md`, README demo (generator now prints the AI line first),
   `current-status.md`, AGENTS.md (omp gate bullet, `typos`-hook trap note).
6. **Upstream:** CodexBar's widget snapshot omits the credit rows, so on cached
   runs aiuse makes one live `codexbar usage --provider claude`
   (`_live_omp_supplement`, through the query gate). Filed
   **steipete/CodexBar#4546** (searched first: nearest threads #4194, #4214,
   #3920, #4518; none on this). Bead **`aiuse-8tz`** tracks removing the live call
   when #4546 ships.
7. **Side work across repos (all pushed):** the `gsed`-never-bare-`sed` rule in
   `~/ops/site-djbclark/home-agents.md` item 1 and `cursor/home-agents.mdc`
   (`c679269`), site-private memory/skills (`d8795d7`), djbclark-ade skills/docs
   (`975fedd`, `a2cf820`), `hermes-ping` and `op-sync-grok` now use
   gsed/ghead/ggrep with a plain-tool fallback (site-djbclark `0034735`); item 7
   of `home-agents.md` now names the free `mcp__firecrawl__*` vs the paid
   `mcp__claude_ai_Firecrawl__*`.

## What We Tried

1. **Putting the grant in `usage_credits`:** rejected before building. That field
   means an overage wallet and feeds overage logic (`use_or_lose.py`, the wallet
   fragment, selfdescribe), so omp would have been treated as having an overage
   wallet. Added `AccountUsage.spent_usd` instead.
2. **Reading the grant from CodexBar's cached widget snapshot:** the Claude entry
   there has only Session/Weekly rows (zero occurrences of "cloud"), so cached
   runs produced no omp row until the live supplement existed.
3. **Searching with `mcp__claude_ai_Firecrawl__*`:** failed, the paid cloud
   account was out of credits. Use `mcp__firecrawl__*` (global rule 7).
4. **BSD `sed -i` in my own command:** `undefined label` error. Led to the
   `gsed` rule and the sweeps in item 7 above.
5. **`typos` pre-commit hook** rewrote a deliberately clipped test literal (the
   first commit failed and the assertion would have gone vacuous). Never assert on
   a misspelled string; documented in AGENTS.md. A missing `_Style` import in a
   new test and a bad `git pull --autostash=false` flag were my own slips.
6. **`aiuse --available --json` live check:** another process (the installed
   3.6.1, before omp existed) overwrote `~/.cache/aiuse/snapshots/latest.json`, so
   omp was absent; verified in-process instead.
7. **Serve cache loader** (`serve.py:_snapshot_from_accounts_dict`) silently
   dropped `usage_credits`, `refill_capacity`, `refill_capacity_unit` and
   `window_id`; found twice (omp's `of $250.00`, grok's `$1.64`), fixed.

## Key Decisions

1. **omp is its own provider and pool** (`omp_claude`, SCOPE `claude`), not a Claude
   window. Rejected: a Claude sub-window (it is not the Claude plan).
2. **Credit modeled as an expiring window** (`resets_at` = expiry, `window_minutes`
   unknown, so no pace figure and the row scores 50). Rejected: guessing a window
   length; the grant date is not reported.
3. **Gate is fixed on omp, informational in aiuse, enforced by the toolchain.**
   Rejected: making it configurable now (docs say the operator may later).
4. **Notice replaces 5H/WEEK cells only when both are empty;** data in either wins.
   JSON, chat and summary lines still carry the gate.
5. **Machine-readable `semantics` text does not name `ACP_OPERATOR_APPROVED`**
   (do not hand agents the bypass); the human docs and AGENTS.md do.
6. **Schema stays 1.3; version 3.7.1 (patch).** The 3.7.1 delta over 3.7.0 is two
   text strings plus docs; djbclark asked for the release explicitly.
7. **Matrix OTHER is never shed on narrow terminals;** PACE drops sooner than
   before. Rejected: dropping OTHER first.
8. **Left alone on purpose:** bare `grep`/`xargs`/`date` in prose or portable files
   flagged by `gnu-tools-check` (classified in the sweep report; only three run
   snippets in djbclark-ade changed).

## Evidence & Data

1. Live values at the time: $249.269521 left, $0.730479 spent, grant $250.00,
   expires 2026-11-05T07:59:00Z (2:59 AM EST), 20d21h left.
2. Releases: `v3.7.0` 2026-10-15T12:13:44Z (other session), `v3.7.1`
   2026-10-15T12:25:58Z; PyPI latest 3.7.1; Homebrew 3.7.1 passes `brew test`.
3. Commits on aiuse `main` this session (oldest first): `2959c37` gate, `853db42`,
   `031edb3`, `5a9bfa5` short wording, `646f4bb`, `9a0a691`, `a8908ba`, `918f081`,
   `b14789f` enforcement wording, `7025dc9` status; earlier in the session
   `02c0eb8`, `ecefb60`, `bb7db17` (omp row, SCOPE, OTHER/PACE/spent_usd). Release
   commits `ed04f9b`, `889b114`, `969ccca`, `ff35513`, `0b41436`.
4. Tests: `.venv/bin/python -m pytest -q` via `~/ops/site-private/bin/bg`: 1203
   passed. New files: `tests/test_codexbar_omp.py`, `test_omp_chat_json.py`,
   `test_report_layout.py`, `test_omp_operator_gate.py`.
5. Key paths: `src/aiuse/collectors/codexbar.py` (`_omp_credits_from_row`,
   `_live_omp_supplement`), `src/aiuse/report.py` (OTHER, gate cell,
   `render_clock_matrix_parts`), `src/aiuse/watch.py`, `src/aiuse/models.py`
   (`spent_usd`, `OPERATOR_APPROVAL_NOTICE`), `src/aiuse/analysis/selfdescribe.py`,
   `src/aiuse/analysis/use_or_lose.py` (`_mark_operator_gate`), `serve.py`,
   `chat_format.py`.
6. Sub-agent reports (layout, data, gate, gsed, gnu-sweep) were all delivered and
   read; none ended `BLOCKED:`.

## Operator Feedback

1. "make the SCOPE of the line 'claude'".
2. "the line should not show an infinity symbol ... I do not think that money
   expires" then "Nevermind re: the infinity symbol, that was based on my incorrect
   statement that there was no time limit." (The credit does expire.)
3. "We don't need to include the date as 20d21h is the same information. For the
   dollar values, just do $0.73/$249.27 ... make the final column a proper column,
   with the heading OTHER" and "move the PACE column to be after the SCOPE column".
4. "Move the AI: Use `aiuse --json` ... line to be the third line down, as I think
   that will make AI processes it sooner."
5. "put a comment centered between the 5H and WEEK lines, (AI: Do not use without
   asking operator) ... you can remove the arrows" for the omp line.
6. "Update skills etc to always use gsed instead of sed unless BSD sed is
   specifically required."
7. FYI relayed from the herdr-layout session: omp is now enforced by `acp-run` /
   `acp-dispatch`; point docs there instead of "nothing enforces it" (done).
8. "you are allowed to dispatch omp now" (2026-10-15, with "then do a full
   release"). omp was not needed and was not dispatched.

## Where We're Going

1. **Open request, not done:** djbclark wrote "Re-send what you tried to send before
   to herdr-layout-t herdr tab". No tab with that label exists (`herdr tab list`
   showed `aiuse-watch-t`, `aiuse-release-t`, `aiuse-omp-credits-t`,
   `shizukutendcf-shell-t`, another project's `-shell-t` tab, `htop-shell-t`,
   `plektra-rebase-acp-t`), and this session never sent anything to a herdr tab.
   Ask djbclark which tab and what message; send with `herdr pane run <pane>
"<text>"` (see `~/src/djbclark-ade/bin/self-slash`). He rejected one chooser
   prompt about it, so ask in plain prose.
2. **Use the omp credit before it expires (2026-11-05):** about $249 unspent. omp
   needs djbclark's explicit yes per run (`ACP_OPERATOR_APPROVED=omp` for that
   call; `bigteam` skill step). His 2026-10-15 "allowed to dispatch omp now" was a
   permission for that moment, not a standing grant; confirm before spending.
3. **Bead `aiuse-8tz`:** when CodexBar#4546 ships the credit rows in
   `widget-snapshot.json` (check `gh issue view 4546 --repo steipete/CodexBar`),
   remove `_live_omp_supplement` and its tests and update the "Cache caveat" in
   `docs/omp-cloud-credits.md`.
4. **Possible, not requested:** make the operator gate configurable per provider
   (docs say "may later"). Do not build without being asked.
5. **Not mine, noticed:** djbclark-ade `skills/autorename/relocate-pane.sh:18` uses a
   bare `seq` (`gnu-tools-check` says `gseq`); that skill had another session's
   uncommitted edits, so it was left alone. Other sessions also had uncommitted
   files in djbclark-ade (`docs/queue.md`, `skills/helm/*`, `skills/bigteam/SKILL.md`,
   ...), untouched.

## Detached jobs

none. No bigteam job records for this session; all Agent-tool sub-agents finished
and wrote their reports. Nothing here relies on a background task or waiter
surviving the session.

## Quick Start

```bash
cd ~/src/aiuse && git fetch origin && git pull --ff-only
git log --oneline -6 && git branch -r --no-merged origin/main
bd show aiuse-8tz
AIUSE_SKIP_HOMEBREW_FORMULA=1 .venv/bin/aiuse        # the matrix with the omp row
AIUSE_SKIP_HOMEBREW_FORMULA=1 .venv/bin/aiuse --json | jq '.accounts[] | select(.provider=="omp") | del(.raw)'
~/ops/site-private/bin/bg .venv/bin/python -m pytest -q   # 1203 expected
gh issue view 4546 --repo steipete/CodexBar --json state,comments --jq '.state'
```
