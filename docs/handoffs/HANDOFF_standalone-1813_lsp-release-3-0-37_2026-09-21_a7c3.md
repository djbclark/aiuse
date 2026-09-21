---
schema_version: 1
handoff_id: a7c3
parent_handoff_ids: []
lineage: none
chain: [standalone-1813, aiuse-9w8]
repo: aiuse
workspace: main
branch: main
head_sha: 05ba575a362d71e3c8e60c24bfbcad68101032e6
created_at: 2026-09-21T12:00:00-04:00
writer: claude-code
---

# Handoff — Claude Code LSP setup, aiuse 3.0.37 release, zai/hyper/deepseek identity

## The Goal

Started as "install these LSP servers and configure them in Crush's crushrc."
Reframed by the operator to: make the LSP servers usable **by Claude Code**
(Crush "takes care of itself"), test them, fix what the diagnostics showed,
and clean up. Along the way: correct aiuse's zai/hyper/deepseek handling, cut
a full release, and change the agent policy to sync proactively.

## Where We Are

Everything is done, committed, pushed and synced. Nothing in flight.

- **LSP (all 8 verified live in Claude Code):** Python (basedpyright), TOML
  (taplo), Markdown (marksman), Kotlin (kotlin-lsp), Go (gopls), TypeScript
  (typescript-language-server + TS 5 sidecar), Rust (official plugin), C
  (clangd, official plugin). Delivered by a local plugin `local-lsp@local`
  v1.0.2, tracked at `site-private/claude/local-marketplace/`.
- **aiuse 3.0.37 released** (tag, GitHub release, PyPI, Homebrew tap, brew
  and pipx installs all report 3.0.37). Only functional change vs 3.0.36:
  removal of speculative `deepseek`/`glm` pool keys in `analysis/pace.py`.
- **Tests:** 640 passed; ruff check/format and mypy clean (`.venv/bin/python
-m pytest -q`, `.venv/bin/ruff`, `.venv/bin/mypy src`).
- **Git:** aiuse `main` = origin/main at `head_sha` above, tree clean. Also clean and in
  sync: homebrew-aiuse, site-private, site-djbclark, stayturgid. Beads
  synced (`bd dolt pull` + `push`). No beads in progress.

Files changed this session (aiuse): `AGENTS.md`, `CLAUDE.md`,
`docs/memory/MEMORY.md`, `docs/hyper-quota.md`, `docs/index.md` (other
session), `src/aiuse/analysis/pace.py`, `tests/test_pace.py`, `.gitignore`,
`.ignore`, version bump files. Elsewhere: `site-private` (`bin/brew-unquarantine`,
`claude/local-marketplace/`, `home-agents.md` = `~/CLAUDE.md`, memory notes),
`site-djbclark` (`roles/site_agents/templates/brew-fast-upgrade.sh.j2`),
`homebrew-aiuse` (`.gitignore`, `.ignore`), live `~/.local/bin/brew-fast-upgrade`.

## What We Tried

Failed approaches, in order — the expensive ones to rediscover:

1. **Configured Crush's `crushrc` for Claude Code's benefit.** Wrong target:
   crushrc only configures Crush. Claude Code's `LSP` tool takes servers only
   from plugins (`.lsp.json` / marketplace `lspServers`).
2. **Edited the plugin source in place after installing.** Claude Code caches
   a snapshot (`~/.claude/plugins/cache/local/local-lsp/<ver>/`); edits never
   reach it. Fix: bump `version` in BOTH `marketplace.json` and `plugin.json`,
   `claude plugin update`, then `/reload-plugins`. A running session never
   sees an in-place edit; killing the server process does not help.
3. **TypeScript: "Could not find a valid TypeScript installation."** Tried
   `npm i -g typescript` → installed TS 7 (native), which has no
   `tsserver.js`. Then `typescript@5` globally → worked for the LSP but
   **shadowed Homebrew's TS 7 `tsc`** via `~/.local/bin/tsc`. Final: TS 5 in
   a private prefix (`~/.local/share/tsserver-ts5`) referenced by
   `initializationOptions.tsserver.path`; global npm typescript removed;
   `tsc` is Homebrew 7.0.2 again.
4. **Kotlin crashed (SIGKILL) then Gatekeeper dialog** on the cask's bundled
   `intellij-server`. Fix: `xattr -dr com.apple.quarantine` on the Caskroom
   dir. Homebrew removed `--no-quarantine` (brew 4.7), so it recurs after
   upgrades → `site-private/bin/brew-unquarantine` and an allowlisted step in
   brew-fast-upgrade.
5. **Pre-commit blocked commits** twice: (a) `prettier` failed with "Cannot
   find package prettier-plugin-toml" — `node_modules` was simply never
   installed (`bun install --frozen-lockfile`, lockfiles unchanged); (b)
   another concurrent session's staged docs failed markdownlint and got swept
   into my commit attempt, and the hooks' stash/restore briefly **wiped my
   working-tree edits**. Use `git commit -o <paths>` to commit only your own
   files when another session has things staged.
6. **A test insertion of mine split an existing test**, so deleting my test
   later also deleted two original assertions (`Claude Code weekly` /
   `Cursor included` → `None`). Restored in `fbed0a0`; file verified
   identical to pre-session state. Lesson: insert new tests at a
   function boundary, not after an assertion line.

## Key Decisions

- **basedpyright, not ruff, as the Python LSP.** Ruff's server has no
  hover/definition/references (the `LSP` tool's operations); Claude Code
  allows one server per extension. Ruff stays linter/formatter
  (stayturgid pre-commit). Rejected: adding ruff too.
- **Plugin home = `site-private/claude/local-marketplace/`** (versioned),
  not `~/.claude/`. Old copy removed.
- **TS 5 sidecar rather than defaulting to older `tsc`.** No good reason to
  use the old `tsc` by default; only the language server needs `tsserver.js`.
- **zai, hyper, deepseek are three separate services — never alias.**
  zai = GLM only (5h+weekly); Charm Hyper = monthly plan with GLM + other
  models on two separate meters (no collector yet, display name
  `hyper/crush` only); deepseek = direct pay-as-you-go prepaid API.
  Rejected: commit `23ce3b6` (aliased deepseek+hyper→zai; already reverted by
  another session in `efc2b65`, and by me in `dc933c4`).
- **Removed the `deepseek`/`glm` pool keys** I had added in `35695b1`:
  speculative — no account carries both classes. Hyper's real labels are
  unknown; add keys only when a collector exposes them (bead aiuse-9w8).
- **Scrap files backed up, not committed:** raw codexbar/zai JSON dumps hold
  live account data → `~/.local/state/aiuse-scrap-backup/2026-09-21/`
  (owner-only), deleted from the repo. Not put in git.
- **Policy change:** Beads' generated "Conservative" default (no
  commit/push/Dolt sync unless asked) had no good reason in this
  single-operator repo. Added an explicit opt-in bullet to Conventions in
  both `AGENTS.md` and `CLAUDE.md` (outside the generated block, which `bd`
  regenerates). Agents now sync beads proactively.
- **Numbered lists rule** placed in `~/CLAUDE.md` (always loaded) — the
  memory note alone (`feedback_numbered_bullets.md`) wasn't auto-loaded.
- Removed the duplicate "BEADS CODEX SETUP" block from `AGENTS.md`; `bd setup
codex` may re-add it.

## Evidence & Data

- Release/HEAD parity: `aiuse --json` from released 3.0.36 vs HEAD — same
  labels, no structural diff, identical stderr. One expected change: zai
  window key `zai:glm:5h` → `zai:-:5h` (key is derived from the label at read
  time in `history.window_series_key`, so no stored history is orphaned).
  Learned sample counts jitter run-to-run in both versions (release 17→16,
  HEAD 19→22): live snapshot churn, not a regression.
- Marksman findings fixed: `AGENTS.md` ambiguous `README.md` link →
  `./README.md`; `docs/memory/MEMORY.md` `../AGENTS.md` → `../../AGENTS.md`
  (memory dir is a symlink target inside `docs/`).
- Sources for ruff/basedpyright split: docs.astral.sh/ruff/editors/setup;
  zed.dev/docs/languages/python. Homebrew `--no-quarantine` removal:
  github.com/Homebrew/brew/issues/20755.
- Release: `just release 3.0.37` (packaging/release.py); v3.0.37 GitHub
  release non-draft; PyPI 3.0.37.

## Operator Feedback

- "Crush is taking care of itself" — LSP work is for Claude Code, not Crush.
- zai and Charm Hyper "are in fact separate services"; DeepSeek is reachable
  directly (PAYG) and via the monthly plan.
- Wants list items numbered/lettered every time (now a standing rule).
- Wants proactive commit/push and bd sync; wants stale/useless artifacts
  removed rather than left.
- Prefers "check whether it's true" over assuming (e.g. asked to verify the
  ruff-in-ops preference and that concurrent work hadn't broken aiuse).

## Where We're Going

1. **Next action: nothing blocking.** Optional first move for a fresh
   session: `cd ~/src/aiuse && bd ready` and pick up `aiuse-9w8`
   (Hyper collector + independent pools) only when Hyper's real window
   labels are available (`docs/hyper-quota.md`).
2. After any `brew upgrade` touching `kotlin-lsp`: `brew-unquarantine`
   (brew-fast-upgrade already does it on its schedule).
3. Editing the LSP plugin: bump version in both JSONs, `claude plugin
update local-lsp@local`, `/reload-plugins`.
4. If `bd setup codex` re-adds the duplicate Beads block in `AGENTS.md`,
   remove it again (or teach `bd`'s recipe not to).

## Quick Start

```bash
cd ~/src/aiuse && git pull --rebase && git status -sb
.venv/bin/python -m pytest -q && .venv/bin/ruff check . && .venv/bin/mypy src
bd ready
aiuse --version            # expect 3.0.37
claude plugin list | grep -A2 local-lsp   # expect 1.0.2
```

Durable facts live elsewhere (don't re-transcribe): LSP plugin gotchas in
`site-private/memory/reference_claude_code_lsp_plugin.md`; provider identity
in `bd memories zai`; Hyper plan in `docs/hyper-quota.md`. Full transcript is
in S1 (`hindsight_s1_search.py search '<phrase>' --mode trigram`).
