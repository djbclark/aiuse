---
schema_version: 1
handoff_id: b7e2
parent_handoff_ids: []
lineage: none
chain: [standalone-b7e2]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 8b413cdf6d5a8930abe7b1dfdcd91f455ccae6ed
created_at: 2026-11-21T09:40:00-05:00
writer: claude-code
---

# Handoff — Ultra plan, antigravity pools, per-service floors, CodexBar

Lineage: none. Checked `docs/handoffs/`: the newest chain (release 4.1.1, 2026-11-12) is unrelated. Session id 53cbec7a-2384-40be-8891-5461934c5e24 (local Claude Code, no Paseo id, so Step 9 cannot run and this is document-only).

## The Goal

Keep aiuse's picture of the operator's quotas true and usable: 5.3.0/5.3.1 fixes (placeholder resets, near-empty usability, antigravity capacity signal) are shipped; remaining work is the live-path placeholder gap, per-service usability floors, the CodexBar upstream item, and the Google Cloud key.

## Where We Are

- 5.3.1 is released everywhere (tag, GitHub release, PyPI, Homebrew, installed `aiuse --version`). `main` is clean and equals `origin/main` at 8b413cd (docs only after the release).
- Operator moved to Google AI Ultra 20x, $199.99/month (2026-11-21). `[plans.gemini] monthly_price = 199.99` is set in `~/.config/aiuse/config.toml` (never print that file: it holds a token). Example config and `config.py` text updated and pushed. Live data shows one antigravity account at $199.99; no vendor shows a duplicate plan.
- z.ai is in `[disabled_services]`.
- Antigravity on Ultra: after one `agy --model gemini-4-{carbon,neon,hydrogen,argon} -p=...` call each, only the shared Gemini 5-hour (+1.97) and weekly (+0.68) windows moved; CodexBar reports no per-model window now, so Carbon is not special. Details in `docs/antigravity-pools.md`.
- Google Cloud: the operator is now enrolled in the Google Developer Program as djbclark@gmail.com (private profile, newsletter off, name Daniel Clark). The Benefits page shows Premium and a $100 monthly Gen AI and Cloud credit (expires 2027-11-21) that needs a Cloud billing account first; none exists on that account, and creating one needs a card. The billing console tab is left open in Chrome.
- The stored sudo-secretspec `GEMINI_API_KEY` ends "bwDg" = djbclark's `stayturgid-api-key` on a free-tier project (not technoeuphora's). Description still says only "for the LiteLLM proxy".

## What We Tried

- Capacity scanner (5.3.0) required `lastStatus == "error"`; real Paseo files read `closed`. Fixed in 5.3.1 with `any_status=True` for that scan only.
- The 5.3.0 placeholder-reset drop covers only the CodexBar widget-snapshot path. The live `codexbar usage` path still shows a 0%-used window with reset = snapshot time + window (14:32Z read: Claude/GPT 5-hour reset 19:32:13Z). Not fixed.
- Developer Program signup: a 1Password frame blocked chrome-use after the Name field was filled; reopening in a new tab and using `--clear-extension-frames` worked.
- A first release attempt of 5.3.1 failed on a flaky `test_serve_state_live_collect` (minute rollover); fixed by pinning `aiuse.serve._clock`.

## Key Decisions

- Keep the 5% near-empty threshold for now. The operator told me to stop asking about it, and separately said the percentage floors must vary per service by plan value (1% of $10 is not 1% of $200).
- The floor lives in aiuse so every consumer gets one verdict; the ADE lead agreed and asked for `floor_percent` and `floor_basis` fields, raw `headroom_percent` unchanged, and a per-service config override.
- Joined the Developer Program with a private profile and no newsletter, the minimum for the credits.

## Decisions

- [decision] Per-service floors are amount-based, converted per plan price, with a config override, exposed as floor_percent and floor_basis; rejected: one flat percent for every service; because 1% of a $10 plan is far less than 1% of a $200 plan.
- [decision] Keep headroom_percent as the raw remaining percent; rejected: subtracting the floor from it; because ADE thresholds would shift silently.
- [decision] Antigravity Carbon needs no special family while CodexBar reports no per-model windows; rejected: adding a gemini_carbon family now; because no window exists to key it.
- [ruling] "they need to be variable per service based on how much a percent of that service is eg 1% of a $10 service is way less than 1% of a $200 service." (djbclark, 2026-11-21)
- [ruling] "stop on the percentage question" (djbclark, 2026-11-21)
- [ruling] "We have only the $199.99/monyh plan; we no longer have the $20 plan." (djbclark, 2026-11-21)
- decided_in [[HANDOFF_standalone-b7e2_antigravity-ultra-floors-codexbar_2026-11-21_b7e2]]

## Relations

- continues nothing (first handoff of this chain).

## Evidence & Data

- ADE lead 3b88cf33-1ddf-46ec-8ab6-594db3449c40 reply: ~/.local/state/agent-reports/53cbec7a-2384-40be-8891-5461934c5e24/inbox/ack-3b88cf33-floors.txt. ADE floors today: pool_fits 15% flat (route_agent.py ~L2573), frontier1_min_left 15, ade-net-resume --transition-below; it reads usable_now, headroom_percent, binding_window, available_at, state, requires_operator_approval, windows[]; not headroom_usd or monthly_price. Tell it the release that carries the floors (file in ~/.local/state/agent-reports/3b88cf33-1ddf-46ec-8ab6-594db3449c40/inbox/ or paseo send 3b88cf33-1ddf-46ec-8ab6-594db3449c40).
- Google plan table: Pro $19.99 ($10 Cloud credit), Ultra 5x $99.99 ($40), Ultra 20x $199.99 ($100). Antigravity AI credits are pay-as-you-go top-ups, not included.
- CodexBar: installed app 0.84.1 (cask says 0.84.0); latest release v0.84.0 (2026-11-07), upstream main is 83 commits ahead with "prepare 0.85.0" and today's release dry run green; no prerelease or beta channel found. Fix for agy bundled with Gemini.app (issue 5113) is in main. Our fork: https://github.com/frdminc/CodexBar. ADE fork tooling: ~/src/djbclark-ade/docs/fork-controller.md, fork-inventory.md, config/forks.toml (CodexBar not enrolled).
- CodexBar upstream issue for the invented resetsAt: searched steipete/CodexBar issues and PRs for antigravity, resetsAt, placeholder reset; nearest threads 4724, 4917, 3789, 3662, none about it. Not filed; operator asked to review a draft first.
- Durable test report: /Users/djbclark/.local/state/agent-reports/53cbec7a-2384-40be-8891-5461934c5e24/live-usability-test-calls-report.md.

## Operator Feedback

- Floors must scale with plan value per service.
- Do not pose the percentage question again.
- Use our fork system and a local build, or an upstream nightly or beta, if CodexBar needs updating.
- Get a Google Cloud API key for djbclark@gmail.com via chrome-use and store it in sudo-secretspec; mark which account the existing key belongs to.

## Where We're Going

1. DONE 2026-11-21 (operator picked "Fix it and release 5.3.2"): the live-path placeholder reset is fixed and released as 5.3.2 (commit f7e05d8, PyPI, GitHub, Homebrew). A 14:47Z live read showed both Claude/GPT windows with placeholder clocks; they now read without a reset.
2. Build per-service floors: amount-based, converted to a percent per service from `monthly_price` and window size, per-service override in config, `floor_percent` and `floor_basis` ("usd" | "tokens" | "override" | "default") per row, schema bump, doc in `aiuse schema` that headroom_percent stays raw. Then tell the ADE lead which release carries it.
3. Cloud billing: the operator adds a Google Cloud billing account (card) at the open billing console tab; then claim the $100 credit at https://me.developers.google.com/benefits, create an API key in AI Studio on that project, and store it with the sudo-secretspec skill as a new name (for example GEMINI_API_KEY_DJBCLARK_ULTRA), declared first. Also record which account the existing GEMINI_API_KEY belongs to (djbclark, free tier): its description cannot be edited in place, so either add a note name or undeclare and re-add with the same value moved in one process.
4. CodexBar: wait for 0.85.0 or build `main` on the frdminc fork through CI (not locally first). Draft the upstream issue for the invented resetsAt and show it to the operator before posting; use the reader snippet in the loose skill, target steipete/CodexBar.
5. Peer request (src-00 / session 264ce667-fc0c-4a70-bd3b-73867418030c, 2026-11-21): own the aiuse side of dated operator-entered changes (for example Carbon's price moving from $2/$10 to $4/$20 per 1M tokens on 2026-12-01) so rankings react, only where a model is truly a separate pool. Design first, no live change without the operator's yes. The djbclark-ade lead is 3152a605-8022-42fe-b816-21433ee8d163 (worker fff32fd0 designs dated quota overrides; cd1c1310 researches Neon vs Carbon; d4a88209 fixes the stale antigravity reading). Finding for it: on Ultra, CodexBar reports no per-model window, so Carbon/Neon/Hydrogen/Argon draw the shared Gemini pool (price is per model, quota is not).
6. Optional: one call per model with a read between each (15-minute gate) to confirm Carbon and Neon draw the shared Gemini pool; confirm the Grok plan tier ($30 is a built-in default); `[Unreleased]` in CHANGELOG still holds older mixed items.

## Update 2026-11-21 (later in the same session)

- 5.3.2 released (live placeholder reset). Operator ruled the stale-reading fix: keep the 10-minute fresh limit and add machine-readable gate fields (option 1). Implemented in the working tree, not yet committed unless the git log shows it: `refreshable_at`, `refresh_blocked_by_gate`, `gate_remaining_seconds` on stale gated readings, copied into pool entries and `stale_pools` rows (selfdescribe.py `_apply_refresh_gate`, tests in tests/test_fresh_per_pool.py, docs/json-contract.md). It ships with schema 1.8 together with the `unavailable` array promised to the ADE lead 3d249ac0 (item 88: exhausted pools such as the three OpenCode Go accounts appear in neither `available` nor `excluded`) and the per-service floors. No release yet; the version and schema bump happen with that release.
- Open possible bug: t-e OpenCode Go has monthly 100% used (resets 2026-11-27) but `available_at` reads 2026-11-23 (weekly reset); the entry's binding window is monthly. Check how `available_at` picks among several depleted windows (it takes the earliest reset, not the latest).
- GEMINI_API_KEY relabelled in sudo-secretspec (description now names djbclark@gmail.com, project gemini-project-littlestuff, free tier). Grok plan stays $30 (operator confirmed the default).
- CodexBar: operator chose "Build upstream main on the frdminc fork via CI". Sub-agent codexbar-fork-build was dispatched (report /Users/djbclark/.local/state/agent-reports/53cbec7a-2384-40be-8891-5461934c5e24/codexbar-fork-build-report.md); it must not install anything. Installing the built app replaces the running CodexBar and needs the operator's yes. The upstream issue draft stays blocked: the sandboxed format reader returned operator_review_required for steipete/CodexBar, so the operator files it with the facts given in chat (14:47Z live read, +300/+10080 minutes less one second).
- ADE lead is now 3d249ac0-627a-41e5-801a-3674baf588e1 (session djbclark-ade-2e). Reports from the previous lead: /Users/djbclark/.local/state/agent-reports/3152a605-8022-42fe-b816-21433ee8d163/ (dated-quota-overrides-report.md has the aiuse schema proposal for dated entries and floors; not yet read by aiuse). ADE PR 864 (stale handling in route_agent), 861 (docs), 866 (supervisor retry).

## Update 2026-11-21 ~11:20 local: CodexBar 0.85.0 installed

- Operator chose "Install now, check aiuse, roll back if broken". Installed the fork-built, ad-hoc-signed 0.85.0 (CI run 44919280321, artifact /Users/djbclark/src/CodexBar-fork-build/44919280321/) into /Applications/CodexBar.app; the old 0.84.1 is kept at /Applications/CodexBar-0.84.1.app.bak. /opt/homebrew/bin/codexbar is a symlink into the app's Helpers, so the CLI is 0.85.0 too. Rollback: quit CodexBar, rm -rf /Applications/CodexBar.app, mv /Applications/CodexBar-0.84.1.app.bak /Applications/CodexBar.app, open it.
- Checks at 16:20Z: HTTP server /health 200; `codexbar usage --provider antigravity` works and now carries real windows (Claude/GPT unused windows still show the placeholder reset, which aiuse 5.3.2 drops); `aiuse --json` collected 14 accounts, no errors, no collector_errors; the Claude CodexBar row is source admin-api with no plan windows in BOTH 0.84.1 and 0.85.0 (so not a regression; aiuse's Claude windows come from cswap).
- Open: the 0.85.0 app had not rewritten widget-snapshot.json or usage-history.jsonl 7 minutes after launch (refreshFrequency fiveMinutes; the old app wrote both at 11:12:20). The app is alive and its app-group lookups succeed. Possible cause: a Keychain or permission prompt waiting on screen after the ad-hoc signature change, or a slow first refresh. aiuse does not depend on the snapshot file today (rows come from the CLI/HTTP). Operator is checking the UI (reported claude monthly missing). If it is still stale after a prompt is cleared, roll back.

## Update 2026-11-21 (schema 1.8 implementation, unreleased)

Shipped on `main`, pushed, **not released** (no version bump beyond `SCHEMA_VERSION = "1.8"`; the operator releases with `just release`):

- 720cf95 refresh-gate fields on stale gated readings (`refreshable_at`, `refresh_blocked_by_gate`, `gate_remaining_seconds`).
- 36c76ef `available_at` is the LATEST reset among depleted windows (was the earliest): accounts, routing entries, Cursor siblings; omp gate and Cursor ceiling combine by the same rule. Tests: `tests/test_available_at_latest.py`.
- 23cb0d2 Argon model-to-pool map derived from the reported windows (`pool_family_for_acp_model(provider, model, reported_families)`; `nominal_pool_family_for_acp_model` for discovery sites; `collapse_gemini_family` in `analysis/pace.py` folds an unreported `gemini_<name>` into `gemini` when applying notes and frozen-pool activity). Ultra has no per-model window, so Argon maps to `gemini`.
- d799b44 `unavailable[]` in the `--available` envelope (`unavailable_pools`, reasons `exhausted`, `near_empty`, `rate_gated`, `no_reading`), schema 1.8. Tests: `tests/test_unavailable_pools.py`.
- 73d38ce per-service floors: `analysis/floors.py`, `[floors]` config (`reserve_usd` 10, `min_percent` 2, `max_percent` 20, `default_percent` 10, per-provider `percent` or `reserve_usd`), fields `floor_percent`, `floor_basis`, `headroom_above_floor_percent` on every `--available` entry. Tests: `tests/test_floors.py` (includes a check that `config/config.example.toml` documents every floors key). `headroom_percent`, `usable_now` and the 5% rule are unchanged.

Docs: `docs/json-contract.md` ("What's new in schema 1.8", "`unavailable[]`", "Headroom floors", Changelog), `README.md` (config section, flag table), `config/config.example.toml` (`[floors]` comments), `config.py` DEFAULT_CONFIG comment, `semantics` map in `selfdescribe.py`, `docs/antigravity-pools.md`, `CHANGELOG.md` [Unreleased].

Open: tell the ADE lead (3b88cf33-1ddf-46ec-8ab6-594db3449c40) the release that carries the floors once the operator cuts it. Floors on the `codex/openai_api` pool (prepaid API credits) use the codex plan price, which is arguably wrong (live: 5.0 on a $200 plan); a per-pool-family override would fix it if it matters. Live `--available --json` after the changes: OpenCode Go account t-e now reads `available_at` 2026-11-27 (monthly) instead of 2026-11-23.

## Detached jobs

none

## Quick Start

```bash
cd ~/src/aiuse && git fetch origin && git pull --ff-only && git log --oneline -6
.venv/bin/python -m pytest -q -p no:cacheprovider      # via /Users/djbclark/ops/site-private-djbclark/bin/bgrun
AIUSE_SKIP_HOMEBREW_FORMULA=1 ./aiuse-test.sh watch --once
rg -n "_without_placeholder_reset|def _window" src/aiuse/collectors/codexbar.py
```
