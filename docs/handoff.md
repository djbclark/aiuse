# Session handoff (current)

**Date:** 2026-07-28
**Branch:** `main`  
**Local tree:** `~/src/aiuse`  
**Remote:** https://github.com/djbclark/aiuse  
**Tests:** `.venv/bin/python -m pytest -q` — **304** passing

**Package version:** **2.1.19** on GitHub + PyPI + Homebrew tap

Fresh agents: start at [`AGENTS.md`](../AGENTS.md).  
Open-ended “what next?” → [`next-options.md`](next-options.md) (not Step 1 of the fix plan).

## Immediate unfinished work (do this first)

1. Operator: announce issue #10 mentions **2.1.19** (do not auto-post).

## Reopen checklist (operator)

1. Open workspace at **`~/src/aiuse`**.
2. Confirm package: `.venv/bin/aiuse --version`, global `aiuse --version`, and
   `/opt/homebrew/bin/aiuse --version` → **2.1.19**.
3. `aiuse doctor` → five collectors green; caut **ok stable-signed**.
4. `aiuse trust status` → caut Authority + CodexBar Cache account list.
5. LaunchAgent: `just -f ~/ops/site-djbclark/justfile site-agents-status`
   — expect `com.djbclark.aiuse` loaded ([`scheduling.md`](scheduling.md)).
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

| Area                   | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Grok depleted band** | A zero-capacity live subscription row now renders `empty`, rather than falling through to `mid`. Positive fractional capacity displays as `<1%` rather than a misleading `0%`. Regression coverage spans 0%, fractional, 1%, 50%, and 100%.                                                                                                                                                                                                                                            |
| **2.1.18 release**     | `just release 2.1.18`: **303 passed**; fix commit `2c4694e`; bump `87f8f63`; tag/GitHub release/PyPI completed. PyPI OIDC workflow [30376489913](https://github.com/djbclark/aiuse/actions/runs/30376489913) succeeded. Canonical formula commit `61a27a9`; tap commit `be881f0`. `.venv/bin/aiuse doctor` reported all five collectors healthy.                                                                                                                                       |
| **2.1.19 release**     | `just release 2.1.19`: **304 passed**; release automation commit `76e37d0`; bump `cd7f573`; tag/GitHub release/PyPI completed. PyPI OIDC workflow [30377524485](https://github.com/djbclark/aiuse/actions/runs/30377524485) succeeded. Canonical formula commit `fe4e692`; tap commit `041c191`. The workflow refreshed Homebrew, upgraded the explicit formula, checked `/opt/homebrew/opt/aiuse/bin/aiuse --version`, and ran `brew test`; direct verification confirmed **2.1.19**. |

### Issue estimates (scan)

| #                                                                                                     | Hours                | Norm. tok | Norm. $ | Title                                                |
| ----------------------------------------------------------------------------------------------------- | -------------------- | --------- | ------- | ---------------------------------------------------- |
| [#1](https://github.com/djbclark/aiuse/issues/1)                                                      | 1–3h after cswap#170 | ~50k–300k | ~$0.5–8 | Track upstream cswap last-good (**blocked**)         |
| [#2](https://github.com/djbclark/aiuse/issues/2)–[#9](https://github.com/djbclark/aiuse/issues/9)     | —                    | —         | —       | **done**                                             |
| [#10](https://github.com/djbclark/aiuse/issues/10)                                                    | 0.5–2h               | ~10k–100k | ~$0–2   | Announce **2.1.19** (operator; **do not auto-post**) |
| [#11](https://github.com/djbclark/aiuse/issues/11)–[#15](https://github.com/djbclark/aiuse/issues/15) | optional             | —         | —       | Polish; only if concrete pain                        |

Release: https://github.com/djbclark/aiuse/releases/tag/v2.1.19

PyPI: https://pypi.org/project/aiuse/2.1.19/

## Operator preferences (standing)

- Commit early/often; push after every commit.
- **Full releases only when explicitly requested** — now via `just release X.Y.Z`.
- Do not install/configure external collectors inside feature work.
- Durable project knowledge → git (`docs/`, `AGENTS.md`), not tool-private memory.

## Verify commands

```bash
cd ~/src/aiuse
.venv/bin/python -m pytest -q
aiuse --version          # expect 2.1.19
aiuse -q --timeout 15    # terminal echo must remain usable after exit
just release-dry 2.1.19  # preview only
```
