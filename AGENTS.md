# Agent entry point

This file is where an AI agent working in this repository should start. It
exists specifically so a fresh agent session — with no prior context — can
find what it needs in one hop instead of re-discovering the repo's shape.

**Mutual links:** this file, [`README.md`](README.md), and
[`docs/fix-implementation-plan.md`](docs/fix-implementation-plan.md) all link
to each other, each near the top of the file, so landing on any one of the
three gets you to the other two immediately.

## Active priorities (what to do next)

**Status (2026-08-14):** Package/CLI **`aiuse`**. Fix-plan Steps **1–34**
done. Product issues **#1–#9** done. Packaging **3.0.17**
(PyPI/GitHub/Homebrew); **3.0.0** was the first advertised release (PyPI trove
classifier moved to Production/Stable). 3.0.1 adds the documented optional
OpenCode Zen browser credential refresh: a validated Chrome session is stored
through SecretSpec at `~/.config/aiuse/secretspec.toml`, so installed CLI runs
do not need Chrome or Keychain access. 3.0.2 classifies zero/negative prepaid
balances as `empty` while keeping positive non-expiring balances `n/a`. It
includes **`aiuse trust`** (caut
stable codesign + CodexBar#679 cache ACL repair) and release wait-race fix.
Five collectors; prepaid/`n/a` band; hourly LaunchAgent; history learning
`auto`. Normal CLI **always live-collects** (hourly snapshots densify History
only). README's first-impression section rewritten with a colored real-output
demo ([`docs/generate-readme-demo.py`](docs/generate-readme-demo.py)); TUI
display gate now honors `FORCE_COLOR`/`TTY_COMPATIBLE`. **No mandatory numbered
step.** Open-ended “what next?” → [`docs/next-options.md`](docs/next-options.md) +
[`docs/handoffs/`](docs/handoffs/) — **do not restart at Step 1**.

1. **Session handoff:** [`docs/handoffs/`](docs/handoffs/) — newest file wins.
   [`docs/handoff.md`](docs/handoff.md) (singular) is the pre-3.0.13 archive.
2. **What next / gap map:** [`docs/next-options.md`](docs/next-options.md)
   (announce → densify history; optional #11–#15 only if pain).
3. **Operator-only:** announce 3.0.0 via [#10](https://github.com/djbclark/aiuse/issues/10)
   when ready; leave hourly agent collecting; optional OpenUsage CLI install.
4. **Optional expansion / polish (not default):** [#16](https://github.com/djbclark/aiuse/issues/16)
   DeepSeek second source, [#17](https://github.com/djbclark/aiuse/issues/17)
   OpenRouter second source, [#18](https://github.com/djbclark/aiuse/issues/18)
   two Groq client sources; then [#11](https://github.com/djbclark/aiuse/issues/11)
   MCP · [#13](https://github.com/djbclark/aiuse/issues/13) History ·
   [#14](https://github.com/djbclark/aiuse/issues/14) watch ·
   [#15](https://github.com/djbclark/aiuse/issues/15) fixtures ·
   [#12](https://github.com/djbclark/aiuse/issues/12) peer outreach (last).
5. **Parked:** Step **35** (ccusage ≠ plan %) —
   [`docs/claude-local-usage.md`](docs/claude-local-usage.md).
6. **Historical:** [`docs/fix-implementation-plan.md`](docs/fix-implementation-plan.md),
   [`docs/code-review-2026-07-23.html`](docs/code-review-2026-07-23.html).

## Persistence policy: durable project knowledge goes in this git repo

**If you are an AI agent — any tool, not just Claude Code — and you produce
something about this project that a _future_ agent session or a _different_
tool should be able to find, put it under version control here, not in your
own tool's private local state.** That means not Claude Code's per-machine
memory store, not `.cursor/`, not `.aider.chat.history.md`, not `.copilot/`,
not any other tool-specific cache/history/rules directory. Concretely:

- Findings, designs, plans, decisions → a file under `docs/`, linked from
  this file and from `README.md`'s "Related reading".
- A reusable script/tool config that produced a checked-in doc → check the
  script in next to what it produced (see `docs/review-workflow.js`).
- Claude / vendor memory is fine as a _working_ scratchpad inside one session;
  before ending a task, promote anything durable into a repo-tracked file.
  Prefer **not** duplicating long essays under `docs/memory/` when
  `AGENTS.md` or another doc already states the rule (token cost for agents
  that load both).

### Claude memory symlink (this project)

`~/.claude/projects/-Users-djbclark-src-aiuse/memory` is a **symlink** to
[`docs/memory/`](docs/memory/) in this repo (older `-src-ai` path may still
exist as a leftover). Keep that directory thin
([`MEMORY.md`](docs/memory/MEMORY.md) index only unless a short pointer is
truly needed). Writing a Claude memory for this project _is_ writing into this
git tree — commit it if it should persist.

### Generic / private memory (sibling ops repo — not a symlink from here)

Cross-project private notes live in `~/ops/site-private` (Claude home-scoped
memory symlinks there). From this repo, **document** both forms — do not add
an in-repo symlink:

- Filesystem: `~/ops/site-private/memory/` and
  `~/ops/site-private/AGENTS.md`
- HTTPS:
  [memory/MEMORY.md](https://github.com/djbclark/site-private/blob/master/memory/MEMORY.md),
  [AGENTS.md](https://github.com/djbclark/site-private/blob/master/AGENTS.md)

Broader three-way ops policy (stayturgid / site-`<name>` / site-private) starts
at
[stayturgid AGENTS.md](https://github.com/djbclark/stayturgid/blob/master/AGENTS.md)
(`~/ops/stayturgid/AGENTS.md`). Independent projects like this one keep
project knowledge in **their own** repo.

**Never commit passwords or secrets.** IPs/hostnames are fine.

## What this project is

`aiuse` is a CLI that aggregates live AI-subscription quota data (Claude, Codex,
Copilot, Grok, Gemini/Antigravity, OpenCode Go, prepaid balances, …) from
**five external data sources** (`cswap`, `CodexBar`, `caut`, `OpenUsage`,
`tokscale` — PATH tools and/or OpenUsage loopback HTTP), then tells the user
what to burn before it resets unused. See `README.md` for the full description,
install steps, CLI flags, and config. Install helpers:
`packaging/install-deps.sh` and site `just install-aiuse-deps`.

## Where things live

| Path                                                                                                         | What it is                                                                                   | When to read it                                                                                        |
| ------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `README.md`                                                                                                  | Project overview: install, usage, CLI flags, config, output format.                          | First, for "what does this tool do / how do I run it."                                                 |
| `AGENTS.md` (this file)                                                                                      | Agent orientation, doc map, persistence policy, **active priorities**.                       | First, for "where is everything / what next."                                                          |
| `docs/handoffs/`                                                                                             | Per-session Tier 2 handoffs (DAG-linked front matter). **Newest file is the resume point.**  | First stop after this file when resuming.                                                              |
| `docs/ralph-orchestrator-phase1-pilot.md`                                                                    | PRD for the parked ralph-orchestrator Phase 1 pilot (beads `aiuse-juk`).                     | When resuming the ralph/judge.sh pilot.                                                                |
| `docs/handoff.md`                                                                                            | Archive: one accreting file, releases 2.1.16–3.0.12. Superseded by `docs/handoffs/`.         | Per-release forensics (workflow run IDs, tap SHAs) not recorded anywhere else.                         |
| `docs/fix-implementation-plan.md`                                                                            | Review-derived task list (Steps 1–32 + Phase 7 optional 33–35). **1–32 and 34 done.**        | Historical scope / remaining optional steps only.                                                      |
| `docs/json-contract.md`                                                                                      | Stable `aiuse --json` fields and exit codes for scripts.                                     | Cron / automation consumers.                                                                           |
| `docs/provider-identity.md`                                                                                  | Canonical provider id vs config key; window identity across collectors.                      | Any change touching provider names, history keys, or display.                                          |
| `docs/companion-stack.md`                                                                                    | Ambient menu-bar tools + `aiuse status` / `prompt` one-liner.                                | Shell prompt / status bar integration.                                                                 |
| `docs/agent-api.md`                                                                                          | Loopback HTTP for agents (`aiuse serve`).                                                    | Agent/MCP-style consumers without full MCP yet.                                                        |
| `docs/scheduling.md`                                                                                         | macOS LaunchAgent hourly (`persist_snapshots`).                                              | Installing scheduled collection.                                                                       |
| `docs/history-learning.md`                                                                                   | Snapshot persist vs `learn_from_history`; `--full` history line.                             | Enabling / debugging history insights.                                                                 |
| `docs/collector-concurrency.md`                                                                              | How collectors run in parallel and timeout (45s).                                            | Perf / hang questions.                                                                                 |
| `completions/`                                                                                               | bash/zsh completion scripts.                                                                 | Shell UX.                                                                                              |
| `https://github.com/djbclark/aiuse/issues/1`                                                                 | Tracks consuming cswap#170 last-good JSON (Step 33).                                         | When #170 merges or when checking upstream status.                                                     |
| `docs/cswap-reliability.md`                                                                                  | Claude/cswap reliability: decision-stale JSON, cache hydration, fallbacks.                   | When Claude rows go missing or multi-account looks wrong.                                              |
| `docs/opencode-go-quota.md`                                                                                  | OpenCode Go: web vs local estimate; shared allotment; **Go ≠ Zen**.                          | When Go % disagrees with the OpenCode TUI / short windows look open.                                   |
| `docs/opencode-zen-balance.md`                                                                               | OpenCode Zen prepaid wallet (separate billing from Go).                                      | Zen balance / credential refresh / empty Zen.                                                          |
| `docs/cursor-quota.md`                                                                                       | Cursor Included/Auto/Other Models + on-demand vs CodexBar slots.                             | When Cursor % or CONSERVE disagrees with the Cursor usage UI.                                          |
| `docs/qwencloud-quota.md`                                                                                    | QwenCloud coding/token plan + PAYG limit via the `qwencloud` CLI (OAuth; no API key).        | Qwen rows missing / `qwencloud auth login` setup.                                                      |
| `docs/antigravity-pools.md`                                                                                  | Antigravity Gemini vs Claude/GPT independent pools (score + ladder rows).                    | When Antigravity is listed only once or pools look merged.                                             |
| `docs/pretty-display.md`                                                                                     | Rich vs Textual for long scrollback-safe reports.                                            | When changing pretty/TTY display.                                                                      |
| `docs/watch-mode.md`                                                                                         | Design: opt-in full-screen `aiuse watch` monitor (q/esc quit, default 10m).                  | When implementing or refining the watch feature.                                                       |
| `docs/packaging.md`                                                                                          | pipx / PyPI / Homebrew; **OIDC Trusted Publishing** release flow.                            | When releasing or changing install UX.                                                                 |
| `docs/competitive-landscape.md`                                                                              | Peers (CodexBar, quotabot, onWatch, …); ranking vs monitor; post-#2–#9 positioning.          | Positioning / “what pool next?” / remaining gaps.                                                      |
| `docs/next-options.md`                                                                                       | Recommended next actions + effort map for remaining gaps; open issue index.                  | Open-ended “what next?” / whether to chase a competitive gap.                                          |
| `docs/shared-quota-semantics.md`                                                                             | Design for language-neutral ranking semantics.                                               | Background for the package.                                                                            |
| `docs/shared-quota-semantics/`                                                                               | **v0.1 package**: schemas, enums, formulas, golden fixtures (+ pytest dogfood).              | Contract tests / peer interop.                                                                         |
| Issues [#2](https://github.com/djbclark/aiuse/issues/2)–[#8](https://github.com/djbclark/aiuse/issues/8)     | **Done** (2.1.9): suggest, forecast, status/prompt, serve, History, local note, health_path. | Historical competitive-strategy pull; see [`competitive-landscape.md`](docs/competitive-landscape.md). |
| [Issue #9](https://github.com/djbclark/aiuse/issues/9)                                                       | **Done** (2.1.10): shared quota-semantics v0.1 + pytest dogfood.                             | Contract tests / peer interop.                                                                         |
| [Issue #10](https://github.com/djbclark/aiuse/issues/10)                                                     | Open · operator: public announce (venues + draft). **Do not auto-post.**                     | Distribution.                                                                                          |
| Issues [#11](https://github.com/djbclark/aiuse/issues/11)–[#15](https://github.com/djbclark/aiuse/issues/15) | Open · optional polish (MCP, peer outreach, History, watch, fixtures).                       | Only if concrete pain; see [`next-options.md`](docs/next-options.md).                                  |
| `docs/collectors-caut-openusage.md`                                                                          | caut + OpenUsage install, config, multi-source cross-check priority.                         | New collectors / doctor PATH / site install.                                                           |
| `docs/macos-keychain-trust.md`                                                                               | Operator guide: `aiuse trust` — stable codesign for caut, Keychain Always Allow.             | Keychain dialogs / cargo reinstall of caut.                                                            |
| `docs/macos-keychain-trust-plan.md`                                                                          | Implementation plan for `aiuse trust` (shipped).                                             | Historical design notes.                                                                               |
| `docs/claude-local-usage.md`                                                                                 | Local `stats-cache` / JSONL / ccusage vs subscription 5h/7d %.                               | When someone proposes parsing `~/.claude` instead of cswap.                                            |
| `docs/code-review-2026-07-23.html`                                                                           | Adversarial code review (45 findings) that the plan was derived from. Open in a browser.     | For the _why_ behind a plan step.                                                                      |
| `docs/consumption-flexibility-plan.md`                                                                       | Original scoring design. **Superseded** by pace-based scoring in the fix plan Phase 2.       | Historical context only.                                                                               |
| `docs/review-workflow.js`                                                                                    | Workflow script that generated the review.                                                   | Methodology / re-run.                                                                                  |
| `docs/memory/`                                                                                               | Thin Claude memory symlink target for this project (`MEMORY.md` index).                      | Rarely — prefer this file and `docs/` prose.                                                           |
| `src/aiuse/`                                                                                                 | Source: collectors, analysis, report, cli, config, models.                                   | When implementing.                                                                                     |
| `tests/`                                                                                                     | Pytest suite.                                                                                | Run `.venv/bin/python -m pytest -q` before and after any change.                                       |
| `config/config.example.toml`                                                                                 | Canonical example user config.                                                               | Keep in sync with `config.py`'s `DEFAULT_CONFIG`.                                                      |

## If you were asked to fix a bug or implement a feature here

1. Check **Active priorities** and the newest file in [`docs/handoffs/`](docs/handoffs/).
   Open-ended “what next?” → summarize status and offer choices (Step 33 when
   unblocked, operator-picked polish, or parked Step 35). Do **not** restart
   completed Steps 1–32.
2. For remaining optional plan work (33, 35), read the matching section in
   `docs/fix-implementation-plan.md` and any linked issue (ai#1 for 33).
3. If the task is not in the plan, check `docs/code-review-2026-07-23.html` and
   existing `docs/` before starting fresh analysis.
4. When implementing: full pytest before and after (`.venv/bin/python -m pytest
-q`); one coherent change at a time; commit early and push (see
   Conventions).

## Conventions

- Python 3.14, `src/` layout, dependencies via `pyproject.toml` + `.venv`.
- Run tests with `.venv/bin/python -m pytest -q`; before pushing, run `just ci`
  (the same all-files quality gate as GitHub Actions). `pre-commit install --install-hooks`
  installs this check as a pre-push hook.
- This repo shells out to external tools that must already be
  installed/authenticated (`cswap`, `codexbar`, `caut`, `openusage` and/or
  OpenUsage.app, `tokscale`) — do not attempt to install, configure, or
  authenticate them as part of a normal feature change. Operators install via
  `packaging/install-deps.sh` or site `just install-aiuse-deps`.
- **Commit early and often; push after every commit.** Prefer a commit at any
  opportune moment (green tests after a coherent change, end of a plan step,
  finished investigation docs) over holding a large uncommitted pile. More
  commits are better than fewer. After each commit, `git push` to the remote
  unless there is a concrete reason not to (e.g. the operator said not to, or
  the branch is deliberately local-only). Do not wait for separate push
  authorization.
- **Full releases (PyPI + Homebrew) only when the operator explicitly asks.**
  Do not cut a “ship everywhere” release for routine doc/collector work.
- Release versions are plain numeric `X.Y.Z`; never use a Homebrew `revision`
  or underscore suffix to ship a change. Increment exactly one component,
  normally patch (`Z`). Minor (`Y`) is an agent judgment call; major (`X`)
  requires explicit operator approval. The deterministic release script
  enforces this policy.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:970c3bf2 -->

## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**

- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.

<!-- END BEADS INTEGRATION -->

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->

## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking in repositories that include it. Use the `beads` skill at `.agents/skills/beads/SKILL.md` (project install) or `~/.agents/skills/beads/SKILL.md` (global install) for Beads workflow guidance, then use the `bd` CLI for issue operations.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
bd prime                # Refresh Beads context
```

### Rules

- Use `bd` for all task tracking; do not create markdown TODO lists.
- Run `bd prime` when Beads context is missing or stale. Codex 0.129.0+ can load Beads context automatically through native hooks; use `/hooks` to inspect or toggle them.
- Keep persistent project memory in Beads via `bd remember`; do not create ad hoc memory files.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.
<!-- END BEADS CODEX SETUP -->
