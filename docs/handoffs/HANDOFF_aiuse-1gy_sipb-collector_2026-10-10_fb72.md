---
schema_version: 1
handoff_id: fb72
parent_handoff_ids: []
lineage: none
chain: [aiuse-1gy]
repo: aiuse
workspace: /Users/djbclark/src/aiuse
branch: main
head_sha: 50920faff6a92545b26801a4e5660cb909c9b6ef
created_at: 2026-10-10T11:35:00-04:00
writer: zcode
---

# Handoff — SIPB LLMs collector (opt-in status probe), released in 3.3.9

## The Goal

Add MIT's SIPB LLMs server (`https://llms-dev-1.mit.edu/api`, Open WebUI +
Ollama; **unlimited quota, very slow shared GPU**) to aiuse so every AI agent
reading aiuse output (a) knows it exists, (b) knows whether it is UP or DOWN
(and how slowly it answered), and (c) sees the routing guidance: slow — use
sparingly / as a last resort. Implemented primarily by agy over ACP
(`gemini-pro-agent` via `acp-dispatch`) in a worktree, then reviewed, landed,
released, and enabled in the live config (operator's queue, 2026-10-10).

## Where We Are

Done, end to end. Merged to main (`0947e45`), released as **3.3.9** (PyPI
OIDC + GitHub release + Homebrew tap; `brew test` green; installed and
verified on this Mac), live config enables the collector, and the installed
binary's default report shows the row. Bead `aiuse-1gy` closed and synced.
Worktree `/tmp/wt-aiuse-sipb` and branch `sipb-collector` (local + remote)
deleted.

## What We Tried

- CLI verification kept showing 0 accounts for the new collector while direct
  python calls worked. Long hunt (spies, settrace, bytecode purge) ended at the
  real cause: `cli.main()` calls `prefer_homebrew_formula()`
  (`src/aiuse/homebrew_formula.py`), which `os.execv`s into the Homebrew 3.3.8
  install whenever that install has the caam collector — so every `aiuse`/`ai`
  invocation here ran 3.3.8, which has no sipb. Escape hatch:
  `AIUSE_SKIP_HOMEBREW_FORMULA=1`.
- That also meant `aiuse-test.sh` (the documented "try unreleased code" path)
  was testing the released formula, not the tree — verified by discriminating
  with a dev-only feature (`--usage-sources` sipb rows: 0 without the env var,
  2 with). Fixed by exporting the env var in the script (`3d864b3`).
- agy's commit swept ~11 debug/scratch files (incl. a 322-line `out.json`) and
  a stray AGENTS.md blank-line deletion into the feature commit — cleaned by
  amend + force-push before merge.
- mypy rejected a `status` local in my report.py edit (enclosing scope already
  binds `status: str`) — renamed to `status_info`.
- gitleaks rejected the test's fake `sk-`+32hex key — replaced with
  `fake-test-key`.

## Key Decisions

- Opt-in only (`DEFAULT_CONFIG` ships `sipb: enabled=false`); enabled here in
  `~/.config/aiuse/config.toml` (backup `config.toml.bak-2026-10-10-sipb`).
  The test asserting "all collectors default on" now excludes sipb.
- Up/down via the public no-auth `GET /api/config` (works keyless); optional
  Bearer `MIT_SIPB_API_KEY` `GET /api/models` (env → `secretspec` →
  `sudo-secretspec`, muse pattern) adds auth-check + model count. Key never
  logged.
- Honest unlimited modeling: no windows/balance, `billing_kind=unknown`, no
  new BillingKind value in the stable JSON contract (option noted in docs).
- Generous timeout default 120s (`[timeouts] sipb`); slow-but-successful
  answers are UP with latency; timeouts/connect errors/5xx are DOWN via the
  account `error` field.
- Compact matrix note in the default report ("unlimited · slow — use sparingly
  / last resort · UP Xs") because the narrow view clamps notes to one line;
  full notes live in `--json` and `--full`.
- Rejected: probing the sibling `llms-dev-0.mit.edu` (best-effort, docs-only),
  fabricating a 100%-remaining window, a new "unlimited" BillingKind in v1.

## Evidence & Data

- Files: `src/aiuse/collectors/sipb.py`, `tests/test_sipb.py` (mocked HTTP:
  up fast/slow, connect-error, 503, auth'd, no-key), runner/config/models/
  usage_sources/report wiring, `docs/sipb-quota.md`, README, provider-
  identity, config example. Merge `0947e45`; fix `3d864b3`; release
  `158142b`+`6ee9fb8`; docs `50920fa`.
- Tests: 944 passed on merged main (twice, via bg); focused suites green
  after each fix.
- Server facts and endpoint map: `~/src/SIPB/docs/llms/{README,api,setup}.md`.
- Live proof (installed 3.3.9, real config): `aiuse --live` → 7 accounts
  including `n/a -- SIPB LLMs (MIT) unlimited · slow — use sparingly / la…`.
- Release: https://github.com/djbclark/aiuse/releases/tag/v3.3.9 ·
  https://pypi.org/project/aiuse/3.3.9/ · publish workflow success in 32s.

## Operator Feedback

- Mid-turn, three hard requirements: very generous timeouts (server is very
  slow); "slow — use sparingly / last resort" in ALL outputs; opt-in via
  config, then enable our config (done).
- End-of-queue instructions: review the newest baton (`standalone-ocgo60`,
  the opencode-go one — its work was already landed by grok; nothing owed),
  merge, `/loose`, full release, `/loose`, delete worktree+branch,
  `/handoff|/quit`.

## Where We're Going

1. Nothing mandatory. Open-ended "what next?" → `docs/next-options.md`.
   Optional issues #16/#17/#18 (second sources) and #11–#15 remain.
2. Adjacent, not this task's debt: a grok session sits blocked in herdr
   (tab `aiuse-caam-vault-and-t#23`, "Ask: The handoff is on main at…") from
   the previous chain — its work is fully landed; it just needs the operator
   to clear it.

## Detached jobs

None outstanding. `sipb-impl` (agy, gemini-pro-agent) finished with exit 0;
report at `~/.local/state/bigteam/sipb-collector/sipb-impl-report.md` was
read and its claims re-verified (two of its statements needed correction:
scratch files committed, and its "manual CLI verification" had actually
exercised Homebrew 3.3.8 via the delegation).

## Quick Start

```bash
cd ~/src/aiuse && git log --oneline -3          # 50920fa on main, pushed
aiuse --live                                    # SIPB row among the 7 accounts
./aiuse-test.sh --usage-sources | rg sipb       # dev tree, no delegation now
rg -n "sipb" ~/.config/aiuse/config.toml        # enabled = true
```
