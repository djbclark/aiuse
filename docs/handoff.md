# Session handoff (current)

**Date:** 2026-07-25  
**Branch:** `main` (clean, synced with `origin/main`)  
**Local tree:** `~/src/aiuse`  
**Remote:** https://github.com/djbclark/aiuse  
**Tests:** `.venv/bin/python -m pytest -q` — **226** passing  
**Version:** **2.1.8** on PyPI + Homebrew + local `pipx` / `brew`

Fresh agents: start at [`AGENTS.md`](../AGENTS.md).

## Reopen checklist (operator)

1. Open Cursor workspace at **`~/src/aiuse`**.
2. Confirm: `aiuse --version` → `2.1.8`; `aiuse doctor` → five collectors green
   (OpenUsage may be “CLI missing; HTTP :6736 responding”).
3. LaunchAgent: `just -f ~/ops/site-djbclark/justfile site-agents-status`
   — expect `com.djbclark.aiuse` loaded ([`scheduling.md`](scheduling.md)).
4. Data sources: `./packaging/install-deps.sh --check` or
   `just -f ~/ops/site-djbclark/justfile aiuse-deps-status`
   (cswap, codexbar, caut, OpenUsage, tokscale).

## Done this stretch (2026-07-25)

| Area | Notes |
| --- | --- |
| **2.1.8 release** | Antigravity dual pools (Gemini vs Google Claude/GPT) + Anthropic/cswap vs Google docs; PyPI + Homebrew |
| **Antigravity dual pools** | Gemini vs Claude/GPT independent allotments ([`antigravity-pools.md`](antigravity-pools.md)) |
| Fix plan Steps **1–32** + **34** | Complete (no restart) |
| **Prepaid / n/a ladder** | Deepseek etc. non-expiring → `n/a` band (empty → n/a → slow → mid → use) |
| **Multi-source collectors** | **caut** + **OpenUsage** enabled by default; generalized all-pairs cross-check |
| **Site install** | site-djbclark: `just install-ai-quota-tools`, brew fragment openusage, caut via cargo |
| **Docs** | [`collectors-caut-openusage.md`](collectors-caut-openusage.md); [`competitive-landscape.md`](competitive-landscape.md) refreshed for five sources |
| **Dep installer** | [`packaging/install-deps.sh`](../packaging/install-deps.sh); `just install-deps`; site `just install-aiuse-deps` |
| **Packaging** | **2.1.8** PyPI + Homebrew |
| **Feature backlog (pull ideas in)** | Issues [#2](https://github.com/djbclark/aiuse/issues/2)–[#8](https://github.com/djbclark/aiuse/issues/8): suggest, forecast, ambient, MCP, history, local note, health_path |
| **Shared semantics design** | [`shared-quota-semantics.md`](shared-quota-semantics.md) — JSON Schema / YAML enums / pace formulas / golden vectors for multi-language peer reuse |
| **Shared semantics implement** | [Issue #9](https://github.com/djbclark/aiuse/issues/9) — package + CI dogfood first; open tickets with peer apps only as the last step |
| **Public announce (operator)** | [Issue #10](https://github.com/djbclark/aiuse/issues/10) — venues (prefer non-vendor-siloed) + draft copy; do not auto-post |
| **Issue estimates** | Titles prefixed `[est. …]`; see table below (wide/rough focused-work ranges) |

### Issue estimates (scan)

| # | Est. | Title |
| --- | --- | --- |
| [#1](https://github.com/djbclark/aiuse/issues/1) | 1–3h after cswap#170 | Track upstream cswap last-good usage (blocked) |
| [#2](https://github.com/djbclark/aiuse/issues/2) | 2–8h | `suggest` — single winner for next token pool |
| [#3](https://github.com/djbclark/aiuse/issues/3) | 1–4h | Louder exhaustion / burn-rate forecast |
| [#4](https://github.com/djbclark/aiuse/issues/4) | 1–4h | Ambient companion stack docs + one-line status |
| [#5](https://github.com/djbclark/aiuse/issues/5) | 8–24h | Optional MCP / loopback query surface |
| [#6](https://github.com/djbclark/aiuse/issues/6) | 4–16h | Deeper History (burn patterns, chronic underuse) |
| [#7](https://github.com/djbclark/aiuse/issues/7) | 1–4h | Local runtime fallback note when empty |
| [#8](https://github.com/djbclark/aiuse/issues/8) | 1–4h | `health_path` / probe URL overrides |
| [#9](https://github.com/djbclark/aiuse/issues/9) | 16–40h | Shared quota-semantics package |
| [#10](https://github.com/djbclark/aiuse/issues/10) | 0.5–2h | Announce (operator post; not product code) |
| **GitHub hygiene** | Repo description, homepage, and topics set on `djbclark/aiuse` |

Release: https://github.com/djbclark/aiuse/releases/tag/v2.1.8  
PyPI: https://pypi.org/project/aiuse/2.1.8/

## Loose-ends scan

| Item | Status | Action |
| --- | --- | --- |
| Working tree / push | Clean; synced with `origin/main` | None |
| Tests | **226** green | None |
| Installers | 2.1.8 on PyPI + Homebrew tap | None |
| GitHub repo metadata | Description + homepage + topics set | None |
| OpenUsage CLI on PATH | Optional | Settings → Command Line → Install if desired |
| OpenUsage.app | Must be running (or CLI installed) for that collector | `open -ga OpenUsage` |
| LaunchAgent | Hourly persist + learn | Let it run |
| **Step 33** / [#1](https://github.com/djbclark/aiuse/issues/1) | Blocked: [claude-swap#170](https://github.com/realiti4/claude-swap/issues/170) | Wait for upstream |
| **Step 35** | Parked (ccusage ≠ plan %) | Do not start unless asked |
| Product backlog | [#2](https://github.com/djbclark/aiuse/issues/2)–[#8](https://github.com/djbclark/aiuse/issues/8) open | Operator picks next feature |
| Shared package | [#9](https://github.com/djbclark/aiuse/issues/9) design done; implement not started | Optional next coding track |
| Public announce | [#10](https://github.com/djbclark/aiuse/issues/10) draft ready; not posted | Operator posts when ready |

## Operator preferences (standing)

- Commit early/often; push after every commit to git.
- **Full releases (PyPI + Homebrew) only when explicitly requested** — do not
  ship “everywhere” on doc/collector polish unless the operator says so.
- Do not install/configure external collectors *inside* aiuse feature PRs;
  operators use `packaging/install-deps.sh` or site `just install-aiuse-deps`.
- Do not use ccusage as plan 5h/7d authority.
- Open-ended “what next?” → **do not restart fix plan at Step 1**.
- Scheduled agents → **site-djbclark** `site_agents`.

## Next options (when resuming)

1. **Wait / poll Step 33** — cswap lastGoodUsage when #170 merges.
2. **Product issues #2–#8** — pick one pull-into-aiuse feature (start with
   `suggest` #2 or forecast UX #3 if agents/scripts matter).
3. **Shared semantics #9** — implement package (schemas/fixtures/CI); peer
   outreach only after it works in-tree.
4. **caut** — optional `claude auth login` if you want more Claude cross-checks;
   Codex windows need upstream caut work (see collectors-caut-openusage.md).
5. **OpenUsage CLI install** (operator) — Settings → Command Line → Install.
6. **Let it run** — multi-source snapshots densify history.
7. **Announce** — [issue #10](https://github.com/djbclark/aiuse/issues/10) when
   operator wants Show HN / Lobsters / etc. (draft ready; no auto-post).
8. **Full release** — only if operator asks (PyPI + Homebrew).

## Quick verification

```bash
just -f ~/ops/site-djbclark/justfile ai-quota-status
aiuse --version   # 2.1.8
aiuse doctor
aiuse --no-tui -q | head -25
.venv/bin/python -m pytest -q
```

## Handoff rule

Update **this file** + [`AGENTS.md`](../AGENTS.md) when ending a session with
durable state; commit and push.
