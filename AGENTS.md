# Agent entry point

This file is where an AI agent working in this repository should start. It
exists specifically so a fresh agent session — with no prior context — can
find what it needs in one hop instead of re-discovering the repo's shape.

**Mutual links:** this file, [`README.md`](./README.md), and
[`docs/fix-implementation-plan.md`](docs/fix-implementation-plan.md) all link
to each other, each near the top of the file, so landing on any one of the
three gets you to the other two immediately.

## Active priorities (what to do next)

**Status (2026-10-09):** Package/CLI **`aiuse`**, packaging **3.3.x**
(PyPI/GitHub/Homebrew). Fix-plan Steps **1–34** and product issues **#1–#9**
done; JSON schema **1.1** (self-describing output, `aiuse --available`,
`aiuse note-exhausted`); 15 registered collectors; scheduled sampling +
`aiuse attribute`. **No mandatory numbered step.** Open-ended "what next?" →
[`docs/next-options.md`](docs/next-options.md) +
[`docs/handoffs/`](docs/handoffs/) — **do not restart at Step 1**. Full status
(collectors, schema 1.1 details, attribution, watch/live-collect rules):
[`docs/current-status.md`](docs/current-status.md).

1. **Session handoff:** [`docs/handoffs/`](docs/handoffs/) — newest file wins.
   [`docs/handoff.md`](docs/handoff.md) (singular) is the pre-3.0.13 archive.
2. **What next / gap map:** [`docs/next-options.md`](docs/next-options.md)
   (announce → densify history; optional #11–#15 only if pain).
3. **Operator-only:** announce 3.0.0 via [#10](https://github.com/djbclark/aiuse/issues/10)
   when ready; leave the sampling agent collecting; optional OpenUsage CLI install.
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

Full path-by-path map with "read when" hints:
[`docs/agent-doc-map.md`](docs/agent-doc-map.md); human grouping:
[`docs/index.md`](docs/index.md). The ones you will need most:

1. `README.md` — install, usage, CLI flags, config, output format.
2. `docs/handoffs/` — per-session handoffs; **newest file is the resume point**.
3. `docs/next-options.md` — what to do next; open issue index.
4. `docs/json-contract.md` — stable `--json` fields and exit codes.
5. `docs/provider-identity.md` — provider ids, history keys, display names.
6. `src/aiuse/` source; `tests/` pytest suite (`.venv/bin/python -m pytest -q`
   before and after any change); `config/config.example.toml` stays in sync
   with `config.py`'s `DEFAULT_CONFIG`.
7. `docs/<provider>-quota.md` — one note per provider's quota quirks.

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

- **`CLAUDE.md` is a symlink to this file** — edit `AGENTS.md` only. Never
  recreate `CLAUDE.md` as a regular file (tools that "initialise" one, e.g.
  `bd setup claude`, `/init`, must be re-pointed at `AGENTS.md`).
- Python 3.14, `src/` layout, dependencies via `pyproject.toml` + `.venv`.
- Run tests with `.venv/bin/python -m pytest -q`; before pushing, run `just ci`
  (the same all-files quality gate as GitHub Actions). `pre-commit install --install-hooks`
  installs this check as a pre-push hook.
- This repo shells out to external tools that must already be
  installed/authenticated (`cswap`, `codexbar`, `caut`, `openusage` and/or
  OpenUsage.app, `tokscale`) — do not attempt to install, configure, or
  authenticate them as part of a normal feature change. Operators install via
  `packaging/install-deps.sh` or site `just install-aiuse-deps`.
- **Changes to non-aiuse code go upstream as PRs** (operator rule, 2026-10-05).
  If a fix or feature needs a change in a tool aiuse depends on (CodexBar,
  OpenUsage, cswap, caut, tokscale, …), open a pull request against that
  project's upstream repo (fork if needed) rather than patching a local copy
  or leaving the change only here. Link the PR from the relevant `docs/` note
  or bead. Workarounds inside aiuse are fine meanwhile.
- **Commit early and often; push after every commit.** Prefer a commit at any
  opportune moment (green tests after a coherent change, end of a plan step,
  finished investigation docs) over holding a large uncommitted pile. More
  commits are better than fewer. After each commit, `git push` to the remote
  unless there is a concrete reason not to (e.g. the operator said not to, or
  the branch is deliberately local-only). Do not wait for separate push
  authorization.
- **Beads profile: this repo opts in to team-maintainer behavior.** Agents may
  close beads, run quality gates, commit, and push, and should sync beads
  proactively — `bd dolt pull` then `bd dolt push` — after creating, updating,
  or closing issues and at session close, without waiting for authorization.
  This overrides the "Conservative (default)" wording in the generated Beads
  block above. An explicit current "do not commit / push / sync" from the
  operator still wins. (Rationale: single-operator repo, work is reversible,
  and unsynced beads are exactly the kind of state that gets lost.)
- **Full releases (PyPI + Homebrew) only when the operator explicitly asks,**
  and then only through `just release X.Y.Z`
  ([`docs/packaging.md`](docs/packaging.md)). Do not cut a release for routine
  doc/collector work.
- Release versions are plain numeric `X.Y.Z`; never use a Homebrew `revision`
  or underscore suffix to ship a change. Increment exactly one component,
  normally patch (`Z`). Minor (`Y`) is an agent judgment call; major (`X`)
  requires explicit operator approval. `just release` enforces this policy.

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

<!-- graft:start -->

## Graft — repo context graph

This repo is indexed in `graft/`: small linked markdown nodes that explain each
system and carry exact file:line spans, kept in sync with the code through git.

For ANY task here — understanding how something works, finding where code lives,
or scoping a change — get context from the graph before grepping or opening
source files. Re-ask freely (it's cheap) and reuse literal identifiers you
already have (symbol, error string, file name) as the query. New to this repo?
Run `graft map` first — a token-budgeted orientation (dir clusters, hubs,
hotspots), no LLM, no key.

- Run `graft ask "<your question>" --source` → ranked nodes with the relevant
  code spans inlined (each hit's ≤8-line crux by default; `--full` for whole
  definitions when the crux isn't enough). Match the tool to the task shape:
  for understanding or editing, the top node IS the answer — cite its
  `covers:` file:line spans and edit straight from `--source`. For
  exhaustive tasks ("every occurrence / every caller of this pattern"), ranked
  results are top-N, not complete — run `graft grep "<literal>"` instead
  (exhaustive over indexed files, grouped by enclosing symbol), falling back
  to raw `grep -rn` only for unindexed files.
- `graft skeleton <file>` → every definition's signature + span, ~10× cheaper
  than reading the file; use it to skim an API surface.
- `graft callers <symbol>` gives precomputed, exact edges — who calls this.
  Add `--direction out` for what it calls, or `--depth N` to walk
  transitively for the full blast radius. For structural questions, skip
  ranking and use this directly.
- Or browse: `graft/INDEX.md` lists every node; follow the links.
- Monorepos and folders of multiple repos rank fairly across sub-projects —
  hits carry `[scope/]` labels naming which one they're from. Narrow with
  `graft ask "<task>" --in <scope>/` once you know where you're working.

If a returned span is truncated ("+N more lines"), open the file at that exact
range before finalizing. Only open source files when a node genuinely lacks a
needed detail, and then at the exact file:line the node points to — never
re-read whole files.

After big code changes, refresh the graph with `graft build` (deterministic,
no API key, $0).
<!-- graft:end -->
