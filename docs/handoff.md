# Session handoff (current)

**Date:** 2026-07-25  
**Branch:** `main` (clean, synced with `origin/main`)  
**Local tree:** `~/src/aiuse`  
**Remote:** https://github.com/djbclark/aiuse  
**Tests:** `.venv/bin/python -m pytest -q` — **219** passing  
**Version:** **2.1.6** on PyPI + Homebrew + local `pipx` / `brew`

Fresh agents: start at [`AGENTS.md`](../AGENTS.md).

## Reopen checklist (operator)

1. Open Cursor workspace at **`~/src/aiuse`**.
2. Confirm: `aiuse --version` → `2.1.6`; `aiuse doctor` → five collectors green
   (OpenUsage may be “CLI missing; HTTP :6736 responding”).
3. LaunchAgent: `just -f ~/ops/site-djbclark/justfile site-agents-status`
   — expect `com.djbclark.aiuse` loaded ([`scheduling.md`](scheduling.md)).
4. Data sources: `./packaging/install-deps.sh --check` or
   `just -f ~/ops/site-djbclark/justfile aiuse-deps-status`
   (cswap, codexbar, caut, OpenUsage, tokscale).

## Done this stretch (2026-07-25)

| Area | Notes |
| --- | --- |
| Fix plan Steps **1–32** + **34** | Complete (no restart) |
| **Prepaid / n/a ladder** | Deepseek etc. non-expiring → `n/a` band (empty → n/a → slow → mid → use) |
| **Multi-source collectors** | **caut** + **OpenUsage** enabled by default; generalized all-pairs cross-check |
| **Site install** | site-djbclark: `just install-ai-quota-tools`, brew fragment openusage, caut via cargo |
| **Docs** | [`collectors-caut-openusage.md`](collectors-caut-openusage.md); [`competitive-landscape.md`](competitive-landscape.md) refreshed for five sources |
| **Dep installer** | [`packaging/install-deps.sh`](../packaging/install-deps.sh); `just install-deps`; site `just install-aiuse-deps` |
| **Packaging** | **2.1.6** PyPI + Homebrew (no new PyPI/brew ship for this doc pass) |

Release: https://github.com/djbclark/aiuse/releases/tag/v2.1.6  
PyPI: https://pypi.org/project/aiuse/2.1.6/

## Loose-ends scan

| Item | Status | Action |
| --- | --- | --- |
| Working tree / push | Expect clean after 2.1.6 ship | None |
| Tests | 219 green | None |
| Installers | 2.1.6 on PyPI + tap | None |
| OpenUsage CLI on PATH | Optional | Settings → Command Line → Install if desired |
| OpenUsage.app | Must be running (or CLI installed) for that collector | `open -ga OpenUsage` |
| LaunchAgent | Hourly persist + learn | Let it run |
| **Step 33** | Blocked: [claude-swap#170](https://github.com/realiti4/claude-swap/issues/170) | Wait for upstream |
| **Step 35** | Parked (ccusage ≠ plan %) | Do not start unless asked |

## Operator preferences (standing)

- Commit early/often; push after every commit.
- Do not install/configure external collectors *inside* aiuse feature PRs;
  operators use `packaging/install-deps.sh` or site `just install-aiuse-deps`.
- Do not use ccusage as plan 5h/7d authority.
- Open-ended “what next?” → **do not restart fix plan at Step 1**.
- Scheduled agents → **site-djbclark** `site_agents`.

## Next options (when resuming)

1. **Wait / poll Step 33** — cswap lastGoodUsage when #170 merges.
2. **OpenUsage CLI install** (operator) — Settings → Command Line → Install.
3. **Live smoke** — `aiuse --full -q --no-tui` vs real UIs.
4. **Let it run** — multi-source snapshots densify history.

## Quick verification

```bash
just -f ~/ops/site-djbclark/justfile ai-quota-status
aiuse --version   # 2.1.6
aiuse doctor
aiuse --no-tui -q | head -25
.venv/bin/python -m pytest -q
```

## Handoff rule

Update **this file** + [`AGENTS.md`](../AGENTS.md) when ending a session with
durable state; commit and push.
