# Session handoff (current)

**Date:** 2026-07-30
**Branch:** `main`  
**Local tree:** `~/src/aiuse`  
**Remote:** https://github.com/djbclark/aiuse  
**Tests:** `.venv/bin/python -m pytest -q` — **313** passing

**Package version:** **2.1.24** on GitHub + PyPI + Homebrew tap

Fresh agents: start at [`AGENTS.md`](../AGENTS.md).  
Open-ended “what next?” → [`next-options.md`](next-options.md) (not Step 1 of the fix plan).

## Immediate unfinished work (do this first)

1. **Site-agent TOML migration (separate `~/ops/site-djbclark` change):** do
   **not** run `site-agents-apply` until its `site_agents` role stops creating
   and editing `~/.config/aiuse/services.yaml`.  That role still manages the
   legacy YAML persistence settings; on a machine with `config.toml`, it would
   recreate a conflicting file and make aiuse fail with its intentional
   two-config migration error.  Replace the YAML variable/tasks with a
   TOML-aware, non-destructive equivalent, document it there, and validate it
   in that repository before applying it to this machine.
2. Operator: announce issue #10 mentions **2.1.24** (do not auto-post).

## Reopen checklist (operator)

1. Open workspace at **`~/src/aiuse`**.
2. Confirm package: `.venv/bin/aiuse --version`, global `aiuse --version`, and
   `/opt/homebrew/bin/aiuse --version` → **2.1.24**.
3. `aiuse doctor` → enabled collectors green; disabled caut is not an error.
4. `aiuse trust status` only if caut is re-enabled later.
5. LaunchAgent: after completing Immediate item 1, run
   `just -f ~/ops/site-djbclark/justfile site-agents-status` — expect
   `com.djbclark.aiuse` loaded ([`scheduling.md`](scheduling.md)).
6. Data sources: `./packaging/install-deps.sh --check` or site
   `just aiuse-deps-status`.

## Done this stretch (2026-07-27 evening)

| Area                       | Notes                                                                                                                                                                                                                                                                              |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **TTY / echo bug**         | After `aiuse`, shell typing invisible until `reset`. Cause: `caut usage --json` puts inherited stdin into raw mode; collector timeout leaves `ECHO`/`ICANON` off. Fix: `stdin=DEVNULL` in `run_json` (+ doctor probes / openusage `open`); `aiuse.tty` save/restore around `main`. |
| **`just release`**         | [`packaging/release.py`](../packaging/release.py) + `just release X.Y.Z` / `just release-dry`. Bump → pytest → commit/push → tag → `gh release` (OIDC PyPI) → Homebrew formula + `~/src/homebrew-aiuse` tap. Docs: [`packaging.md`](packaging.md).                                 |
| **2.1.16 release**         | Tag/GitHub/PyPI/Homebrew completed as end-to-end test of `just release`.                                                                                                                                                                                                           |
| **Wait-race fix & 2.1.17** | Fixed `_wait_for_pypi` to match `headBranch == vX.Y.Z` on publish workflow; shipped full release **v2.1.17** via `just release 2.1.17`.                                                                                                                                            |
| **Wait-race fix**          | In-tree (may be uncommitted): match publish run by tag, then confirm PyPI JSON for that version.                                                                                                                                                                                   |

## Done this release (2026-07-28)

| Area                     | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Grok depleted band**   | A zero-capacity live subscription row now renders `empty`, rather than falling through to `mid`. Positive fractional capacity displays as `<1%` rather than a misleading `0%`. Regression coverage spans 0%, fractional, 1%, 50%, and 100%.                                                                                                                                                                                                                                                                                                                                                                                                         |
| **2.1.18 release**       | `just release 2.1.18`: **303 passed**; fix commit `2c4694e`; bump `87f8f63`; tag/GitHub release/PyPI completed. PyPI OIDC workflow [30376489913](https://github.com/djbclark/aiuse/actions/runs/30376489913) succeeded. Canonical formula commit `61a27a9`; tap commit `be881f0`. `.venv/bin/aiuse doctor` reported all five collectors healthy.                                                                                                                                                                                                                                                                                                    |
| **2.1.19 release**       | `just release 2.1.19`: **304 passed**; release automation commit `76e37d0`; bump `cd7f573`; tag/GitHub release/PyPI completed. PyPI OIDC workflow [30377524485](https://github.com/djbclark/aiuse/actions/runs/30377524485) succeeded. Canonical formula commit `fe4e692`; tap commit `041c191`. The workflow refreshed Homebrew, upgraded the explicit formula, checked `/opt/homebrew/opt/aiuse/bin/aiuse --version`, and ran `brew test`; direct verification confirmed **2.1.19**.                                                                                                                                                              |
| **Step 33 / cswap #170** | cswap 0.24.0's official display-grade `lastGoodUsage` is now preferred when decision-grade `usage` is absent. Older cswap versions, absent fields, and malformed additive fields retain the established local-cache fallback. `cswap upgrade` updated this machine to 0.24.0; **306 tests** passed.                                                                                                                                                                                                                                                                                                                                                 |
| **2.1.20 release**       | `just release 2.1.20`: **307 passed**; Step 33 commit `13a9cb1`; release-doc automation `b12aabd`; bump `75dae95`; tag/GitHub release/PyPI completed. PyPI OIDC workflow [30539785430](https://github.com/djbclark/aiuse/actions/runs/30539785430) succeeded. Canonical formula commit `a3d3868`; tap commit `c25ff73`. The release script deterministically updated `docs/packaging.md`; Homebrew 2.1.20 and `brew test` both passed.                                                                                                                                                                                                              |
| **2.1.21 / 2.1.22**      | 2.1.21 added pipx/default-PATH verification, exposing that Homebrew had retained a stale tap. `235adff` hardened the workflow: force-refresh Homebrew, fast-forward the named tap, then verify its requested formula before upgrading. `just release 2.1.22`: **308 passed**; bump `b57cfee`; PyPI OIDC workflow [30540504520](https://github.com/djbclark/aiuse/actions/runs/30540504520) succeeded; canonical formula `cbe9c4c`; tap `2a603e0`. It upgraded Homebrew to 2.1.22, passed `brew test`, upgraded pipx, and verified default `aiuse` and `ai` both report **2.1.22**.                                                                  |
| **CI repair / 2.1.23**   | The macOS keychain dry-run no longer depends on a local keychain file; release downloads use validated HTTPS endpoints through `requests`; Semgrep is installed in the dev gate; stale docs were formatted. GitHub [test run 30541477590](https://github.com/djbclark/aiuse/actions/runs/30541477590) passed all tests and quality/security checks. `just release 2.1.23`: **309 passed**; bump `759b99f`; PyPI OIDC [30541595773](https://github.com/djbclark/aiuse/actions/runs/30541595773) succeeded; canonical formula `8929413`; tap `0d01d60`; Homebrew 2.1.23 and `brew test` passed; default-PATH `aiuse` and `ai` both report **2.1.23**. |
| **TOML config / 2.1.24** | `config.toml` is now the single default user config; its sparse example focuses on overrides. Legacy `services.yaml` remains readable only on its own; coexistence is a migration error that instructs users to remove the YAML. Local configuration was migrated to a concise TOML and the YAML removed. `just release 2.1.24`: **313 passed**; bump `8f55887`; PyPI OIDC [30544120256](https://github.com/djbclark/aiuse/actions/runs/30544120256) succeeded; canonical formula `dd7146d`; tap `6dfd130`; Homebrew 2.1.24 and `brew test` passed; default-PATH `aiuse` and `ai` both report **2.1.24**.                                           |

### Issue estimates (scan)

| #                                                                                                     | Hours    | Norm. tok | Norm. $ | Title                                                |
| ----------------------------------------------------------------------------------------------------- | -------- | --------- | ------- | ---------------------------------------------------- |
| [#1](https://github.com/djbclark/aiuse/issues/1)                                                      | —        | —         | —       | **done** — official field + legacy fallback retained |
| [#2](https://github.com/djbclark/aiuse/issues/2)–[#9](https://github.com/djbclark/aiuse/issues/9)     | —        | —         | —       | **done**                                             |
| [#10](https://github.com/djbclark/aiuse/issues/10)                                                    | 0.5–2h   | ~10k–100k | ~$0–2   | Announce **2.1.24** (operator; **do not auto-post**) |
| [#11](https://github.com/djbclark/aiuse/issues/11)–[#15](https://github.com/djbclark/aiuse/issues/15) | optional | —         | —       | Polish; only if concrete pain                        |

Release: https://github.com/djbclark/aiuse/releases/tag/v2.1.24

PyPI: https://pypi.org/project/aiuse/2.1.24/

## Operator preferences (standing)

- Commit early/often; push after every commit.
- **Full releases only when explicitly requested** — now via `just release X.Y.Z`.
- Do not install/configure external collectors inside feature work.
- Durable project knowledge → git (`docs/`, `AGENTS.md`), not tool-private memory.

## Verify commands

```bash
cd ~/src/aiuse
.venv/bin/python -m pytest -q
aiuse --version          # expect 2.1.24
aiuse -q --timeout 15    # terminal echo must remain usable after exit
just release-dry 2.1.24  # preview only
```
