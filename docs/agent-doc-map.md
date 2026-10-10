# Agent doc map

Moved out of `AGENTS.md` to keep that file small (it is loaded into every agent
session). Each entry is: path — what it is — when to read it. For a human-oriented
grouping see [`index.md`](index.md).

- `README.md` — Project overview: install, usage, CLI flags, config, output format. **Read when:** First, for "what does this tool do / how do I run it."
- `AGENTS.md` (repo root) — Agent orientation, doc map, persistence policy, **active priorities**. **Read when:** First, for "where is everything / what next."
- `docs/handoffs/` — Per-session Tier 2 handoffs (DAG-linked front matter). **Newest file is the resume point.** **Read when:** First stop after this file when resuming.
- `docs/ralph-orchestrator-phase1-pilot.md` — PRD for the parked ralph-orchestrator Phase 1 pilot (beads `aiuse-juk`). **Read when:** When resuming the ralph/judge.sh pilot.
- `docs/handoff.md` — Archive: one accreting file, releases 2.1.16–3.0.12. Superseded by `docs/handoffs/`. **Read when:** Per-release forensics (workflow run IDs, tap SHAs) not recorded anywhere else.
- `docs/fix-implementation-plan.md` — Review-derived task list (Steps 1–32 + Phase 7 optional 33–35). **1–32 and 34 done.** **Read when:** Historical scope / remaining optional steps only.
- `docs/json-contract.md` — Stable `aiuse --json` fields and exit codes for scripts. **Read when:** Cron / automation consumers.
- `docs/provider-identity.md` — Canonical provider id vs config key; window identity across collectors. **Read when:** Any change touching provider names, history keys, or display.
- `docs/companion-stack.md` — Ambient menu-bar tools + `aiuse status` / `prompt` one-liner. **Read when:** Shell prompt / status bar integration.
- `docs/agent-api.md` — Loopback HTTP for agents (`aiuse serve`). **Read when:** Agent/MCP-style consumers without full MCP yet.
- `docs/scheduling.md` — macOS LaunchAgent hourly (`persist_snapshots`). **Read when:** Installing scheduled collection.
- `docs/attribution.md` — Token ledgers (tokscale, LiteLLM), adaptive sampling (`aiuse sample`), `aiuse attribute`. **Read when:** Asking which client spent a quota window; changing sampling cadence.
- `docs/history-learning.md` — Snapshot persist vs `learn_from_history`; `--full` history line. **Read when:** Enabling / debugging history insights.
- `docs/collector-concurrency.md` — How collectors run in parallel and timeout (45s; cswap and openusage_sh 90s). **Read when:** Perf / hang questions.
- `completions/` — bash/zsh completion scripts. **Read when:** Shell UX.
- `https://github.com/djbclark/aiuse/issues/1` — Tracks consuming cswap#170 last-good JSON (Step 33). **Read when:** When #170 merges or when checking upstream status.
- `docs/cswap-reliability.md` — Claude/cswap reliability: decision-stale JSON, cache hydration, fallbacks. **Read when:** When Claude rows go missing or multi-account looks wrong.
- `docs/opencode-go-quota.md` — OpenCode Go: web vs local estimate; shared allotment; **Go ≠ Zen**. **Read when:** When Go % disagrees with the OpenCode TUI / short windows look open.
- `docs/opencode-zen-balance.md` — OpenCode Zen prepaid wallet (separate billing from Go). **Read when:** Zen balance / credential refresh / empty Zen.
- `docs/cursor-quota.md` — Cursor Included/Auto/Other Models + on-demand vs CodexBar slots. **Read when:** When Cursor % or CONSERVE disagrees with the Cursor usage UI.
- `docs/qwencloud-quota.md` — QwenCloud + sibling Alibaba Cloud (Bailian) plans via `qwencloud` / `bl` CLIs. **Read when:** Qwen/alibaba rows missing; qwencloud / bl CLI auth setup.
- `docs/antigravity-pools.md` — Antigravity Gemini vs Claude/GPT independent pools (score + ladder rows). **Read when:** When Antigravity is listed only once or pools look merged.
- `docs/pretty-display.md` — Rich vs Textual for long scrollback-safe reports. **Read when:** When changing pretty/TTY display.
- `docs/watch-mode.md` — Design: opt-in full-screen `aiuse watch` monitor (q/esc quit, default 10m). **Read when:** When implementing or refining the watch feature.
- `docs/packaging.md` — pipx / PyPI / Homebrew; **OIDC Trusted Publishing** release flow. **Read when:** When releasing or changing install UX.
- `aiuse-test.sh` (repo root) — Runs `aiuse` from this source tree (unreleased code, any directory, args pass through). **Read when:** Trying a change before `just release`.
- `docs/competitive-landscape.md` — Peers (CodexBar, quotabot, onWatch, …); ranking vs monitor; post-#2–#9 positioning. **Read when:** Positioning / “what pool next?” / remaining gaps.
- `docs/next-options.md` — Recommended next actions + effort map for remaining gaps; open issue index. **Read when:** Open-ended “what next?” / whether to chase a competitive gap.
- `docs/shared-quota-semantics.md` — Design for language-neutral ranking semantics. **Read when:** Background for the package.
- `docs/shared-quota-semantics/` — **v0.1 package**: schemas, enums, formulas, golden fixtures (+ pytest dogfood). **Read when:** Contract tests / peer interop.
- Issues [#2](https://github.com/djbclark/aiuse/issues/2)–[#8](https://github.com/djbclark/aiuse/issues/8) — **Done** (2.1.9): suggest, forecast, status/prompt, serve, History, local note, health_path. **Read when:** Historical competitive-strategy pull; see [`competitive-landscape.md`](competitive-landscape.md).
- [Issue #9](https://github.com/djbclark/aiuse/issues/9) — **Done** (2.1.10): shared quota-semantics v0.1 + pytest dogfood. **Read when:** Contract tests / peer interop.
- [Issue #10](https://github.com/djbclark/aiuse/issues/10) — Open · operator: public announce (venues + draft). **Do not auto-post.** **Read when:** Distribution.
- Issues [#11](https://github.com/djbclark/aiuse/issues/11)–[#15](https://github.com/djbclark/aiuse/issues/15) — Open · optional polish (MCP, peer outreach, History, watch, fixtures). **Read when:** Only if concrete pain; see [`next-options.md`](next-options.md).
- `docs/collectors-caut-openusage.md` — caut + OpenUsage install, config, multi-source cross-check priority. **Read when:** New collectors / doctor PATH / site install.
- `docs/macos-keychain-trust.md` — Operator guide: `aiuse trust` — stable codesign for caut, Keychain Always Allow. **Read when:** Keychain dialogs / cargo reinstall of caut.
- `docs/macos-keychain-trust-plan.md` — Implementation plan for `aiuse trust` (shipped). **Read when:** Historical design notes.
- `docs/claude-local-usage.md` — Local `stats-cache` / JSONL / ccusage vs subscription 5h/7d %. **Read when:** When someone proposes parsing `~/.claude` instead of cswap.
- `docs/code-review-2026-07-23.html` — Adversarial code review (45 findings) that the plan was derived from. Open in a browser. **Read when:** For the _why_ behind a plan step.
- `docs/consumption-flexibility-plan.md` — Original scoring design. **Superseded** by pace-based scoring in the fix plan Phase 2. **Read when:** Historical context only.
- `docs/review-workflow.js` — Workflow script that generated the review. **Read when:** Methodology / re-run.
- `docs/memory/` — Thin Claude memory symlink target for this project (`MEMORY.md` index). **Read when:** Rarely — prefer this file and `docs/` prose.
- `src/aiuse/` — Source: collectors, analysis, report, cli, config, models. **Read when:** When implementing.
- `tests/` — Pytest suite. **Read when:** Run `.venv/bin/python -m pytest -q` before and after any change.
- `config/config.example.toml` — Canonical example user config. **Read when:** Keep in sync with `config.py`'s `DEFAULT_CONFIG`.
