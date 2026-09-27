---
schema_version: 1
handoff_id: a17f
parent_handoff_ids: [c9ef]
lineage: c9ef
chain: [aiuse-1qo, aiuse-e9d, aiuse-oja, aiuse-al9, a17f]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 3835d4a8bce1a4db5233061c39406cabc814d306
created_at: 2026-09-27T11:38:32-04:00
writer: ZCode
---

# Handoff — cli_binary JSON field, grok_billing takeover, release 3.0.40

## The Goal

Three threads, all closed:

1. Add a `cli_binary` field to `aiuse --json` account rows so no future
   session has to _guess_ the binary behind a provider id. The prompt: a
   session mapping providers to CLIs guessed `gemini` and `zai` when the real
   binaries are `agy` and `zcode`.
2. Take over the abandoned in-progress `grok_billing` collector (the other
   session was long gone). Its two mypy errors were blocking **every** commit
   in the repo, and the user said to take it over mid-session.
3. Operator-approved full release (PyPI + GitHub + Homebrew) as `3.0.40`.

## Where We Are

**All three done and shipped.**

- `e041aad` — feat(grok): Extra Usage Credits collector, complete.
- `e7b49cd` — feat(models): `cli_binary` in `AccountUsage.to_dict()`.
- `096ee42` — AGENTS.md status refresh (was stale at 3.0.17 / "five
  collectors" / 2026-08-14; now 15 collectors, cli_binary, 2026-09-27).
- `fff0722` + `3835d4a` — release 3.0.40:
  [GitHub v3.0.40](https://github.com/djbclark/aiuse/releases/tag/v3.0.40),
  [PyPI 3.0.40](https://pypi.org/project/aiuse/3.0.40/), Homebrew tap bumped,
  `brew test` and pipx upgrade verified by the release script.

Validation: 689 tests passing (659 baseline + 28 cli_binary + 2 merge/config),
`just ci` fully green. Tree clean, pushed, beads synced.

## Key Decisions

- **`cli_binary` = the CLI that _spends_ the quota, never the measurement
  tool.** The load-bearing case: `qwencloud` → `qwen` (Qwen Code TUI), because
  `qwencloud`/`bl`/`caut`/`tokscale`/`openusage`/`cswap`/`codexbar` report
  quota but do not consume it. `alibaba` → `qwen` too — the operator's
  `~/.qwen/settings.json` carries `BAILIAN_CODING_PLAN_API_KEY`.
- **Verification over guessing**: every mapping was confirmed with `which` +
  `--help`/`--version` on this machine. Surprises vs the operator's
  expectation: `devin` is a real Cognition CLI (terminal + cloud) → mapped,
  not null; `muse` is the real Muse Code CLI; `grok` is xAI's Grok Build TUI.
- `clinepass` → `cline` (official CLI), not `crush` (which remains only in
  the display name `clinepass/crush`).
- **Emit explicit `null`, not key omission** — stable for JSON consumers;
  additive per the contract's stability policy, no major bump.
- **grok merge keeps the standalone wallet row** when no other collector has
  a grok row (was a silent drop — against the repo's "silence is dangerous"
  principle, cf. clinepass.py's comment). With a host row it folds in as
  `usage_credits` and drops itself, as designed.
- `openrouter`/`clinepass` added to `KNOWN_COLLECTOR_KEYS`/`KNOWN_TIMEOUT_KEYS`
  alongside `grok_billing` — the runner gated them but config.py warned on
  them as unknown (pre-existing bug, fixed in the grok commit).

## Evidence & Data

- Binary verification (2026-09-27): `agy`, `zcode`, `opencode`, `codex`,
  `claude`, `cursor-agent`, `copilot`, `grok` (v1.0.41 Grok Build TUI),
  `devin` (v3000.11.3), `qwen` (Qwen Code), `crush` (djbclark fork), `muse`
  present; `deepseek`, `openrouter`, `clinepass`, `hyper` absent. `cursor`
  exists but is the editor launcher — hence `cursor-agent`.
- Live `aiuse --json --flatten`: all 18 provider rows resolve (unknown rows
  `codexbar-query-errors`/`litellm` → null); grok row carries `$14.68`
  Extra Usage Credits folded from the live billing API.
- Docs updated: `docs/json-contract.md` (cli_binary + the never-documented
  `provider_id`/`service_id`/`collector_id` + full source list), README
  "Which vendor CLI is which" table, `docs/grok-quota.md` (new),
  `docs/index.md`, `docs/source-coverage.md`, `config/config.example.toml`.

## Where We're Going

Nothing pending from this session. Still parked: public announce via
[#10](https://github.com/djbclark/aiuse/issues/10) (operator-only), optional
[#11](https://github.com/djbclark/aiuse/issues/11)–[#15](https://github.com/djbclark/aiuse/issues/15).

## Quick Start

```bash
git log --oneline v3.0.39..HEAD        # this release's contents
aiuse --json --flatten | jq '.accounts[] | {provider, cli_binary}'
just release-dry 3.0.41                # next release preview
```
