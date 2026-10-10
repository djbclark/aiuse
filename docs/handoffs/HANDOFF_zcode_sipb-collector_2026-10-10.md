# Handoff — SIPB LLMs collector, release 3.3.9 (2026-10-10)

Session: zcode (`sess_d17ec3d6`), worktree `/tmp/wt-aiuse-sipb` (branch
`sipb-collector`, now deleted), implementation primarily by **agy** over ACP
(`gemini-pro-agent`, dispatched via `acp-dispatch`).

## What landed (all in 3.3.9, pushed)

1. `sipb` collector (`src/aiuse/collectors/sipb.py`): MIT SIPB LLMs
   (`https://llms-dev-1.mit.edu/api`, Open WebUI + Ollama) — **unlimited quota,
   very slow (shared GPU), use sparingly / as a last resort**.
   - Up/down via the public no-auth `GET /api/config` probe (latency in notes +
     `raw.status`); optional Bearer `MIT_SIPB_API_KEY` `GET /api/models`
     (env → `secretspec`/`sudo-secretspec`, muse pattern; never logged).
   - Honest unlimited modeling: no windows/balance, `billing_kind=unknown`;
     down = `error` row, so it renders in the error band.
   - Visible everywhere: `--json` notes, `--full` per-provider notes, and a
     compact matrix note in the default report ("unlimited · slow — use
     sparingly / last resort · UP Xs", report.py `_build_matrix_rows`).
   - **Opt-in only**: `DEFAULT_CONFIG` leaves it disabled; enabled here in
     `~/.config/aiuse/config.toml` (`[collectors.sipb] enabled = true`, backup
     `config.toml.bak-2026-10-10-sipb`).
   - Very generous timeout default **120s** (`[timeouts] sipb`, per operator:
     the server is very slow); slow-but-successful answers are UP, only
     timeouts/connect errors/5xx are DOWN.
2. `aiuse-test.sh` fix (3d864b3): it was exec-delegating to the Homebrew
   formula (`prefer_homebrew_formula`), so it tested the _released_ binary, not
   the tree. Now exports `AIUSE_SKIP_HOMEBREW_FORMULA=1`.
3. Release **3.3.9** (PyPI OIDC + GitHub release + Homebrew tap, `brew test`
   green; installed and verified on this Mac).

## Verification

- 944 tests pass (worktree and merged main); sipb tests are mocked-HTTP only
  (up fast/slow, connect-error, 503, auth'd, no-key).
- Live end-to-end on the installed 3.3.9: `aiuse --live` shows 7 accounts
  including `n/a -- SIPB LLMs (MIT) unlimited · slow — use sparingly / …`.
- Server facts and endpoint map: `~/src/SIPB/docs/llms/{README,api,setup}.md`;
  aiuse-side note: `docs/sipb-quota.md`.

## Gotchas worth remembering

1. `aiuse`/`ai` on this machine exec into the Homebrew formula whenever that
   install has the caam collector (`src/aiuse/homebrew_formula.py`), so testing
   unreleased code needs `AIUSE_SKIP_HOMEBREW_FORMULA=1` (now baked into
   `aiuse-test.sh`). This made the CLI look like it ignored the new collector
   mid-development — the venv console script delegates too.
2. gitleaks rejects even fake `sk-`+32hex keys in tests; use `fake-test-key`.
3. Sibling instance `llms-dev-0.mit.edu` is best-effort, not collected.
4. Instance is often MITnet-only; off-campus needs MIT VPN (503 otherwise).

## State

- main is clean and pushed (`6ee9fb8`); bead `aiuse-1gy` closed and synced.
- The blocked grok session in herdr (tab `aiuse-caam-vault-and-t#23`, "Ask:
  The handoff is on main at…") is the previous chain (`standalone-ocgo60`)
  waiting on the operator — its work is fully landed, nothing owed there.
- Nothing else open for this task.
