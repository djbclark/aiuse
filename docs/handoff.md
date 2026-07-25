# Session handoff (current)

**Date:** 2026-07-25  
**Branch:** `main` (synced with `origin/main` after release)  
**Local tree:** `~/src/aiuse`  
**Remote:** https://github.com/djbclark/aiuse  
**Tests:** `.venv/bin/python -m pytest -q` — **263** passing  
**Version:** **2.1.10** on PyPI + Homebrew + local `pipx` / `brew`

Fresh agents: start at [`AGENTS.md`](../AGENTS.md).

## Reopen checklist (operator)

1. Open workspace at **`~/src/aiuse`**.
2. Confirm: `aiuse --version` → `2.1.10`; `aiuse doctor` → five collectors green
   (OpenUsage often “CLI missing; HTTP :6736 responding” — OK).
3. LaunchAgent: `just -f ~/ops/site-djbclark/justfile site-agents-status`
   — expect `com.djbclark.aiuse` loaded ([`scheduling.md`](scheduling.md)).
4. Data sources: `./packaging/install-deps.sh --check` or site
   `just aiuse-deps-status` (cswap, codexbar, caut, OpenUsage, tokscale).

## Done this stretch (2026-07-25)

| Area | Notes |
| --- | --- |
| **Docs pass** | Competitive landscape refreshed for post-#2–#9; README/AGENTS/shared-semantics/companion/agent-api aligned |
| **2.1.10 release** | Shared quota-semantics v0.1 package + dogfood tests; handoff/loose-ends refresh |
| **2.1.9 release** | Product issues #2–#8 (suggest, status/prompt, serve, forecast, History, health_path, local runtimes) |
| **2.1.8** | Antigravity dual pools (Gemini vs Google Claude/GPT) |
| **#2–#9** | Closed (product + shared-semantics v0.1) |
| Fix plan Steps **1–32** + **34** | Complete — do not restart |
| Collectors | cswap + CodexBar + caut + OpenUsage + tokscale |
| Config | `~/.config/aiuse/` only |
| LaunchAgent | Hourly `com.djbclark.aiuse` (persist + learn auto) |

### Issue estimates (scan)

| # | Hours | Norm. tok | Norm. $ | Title |
| --- | --- | --- | --- | --- |
| [#1](https://github.com/djbclark/aiuse/issues/1) | 1–3h after cswap#170 | ~50k–300k | ~$0.5–8 | Track upstream cswap last-good (**blocked**) |
| [#2](https://github.com/djbclark/aiuse/issues/2)–[#9](https://github.com/djbclark/aiuse/issues/9) | — | — | — | **done** (see titles for historical est.) |
| [#10](https://github.com/djbclark/aiuse/issues/10) | 0.5–2h | ~10k–100k | ~$0–2 | Announce (operator; do not auto-post) |

Release: https://github.com/djbclark/aiuse/releases/tag/v2.1.10  
PyPI: https://pypi.org/project/aiuse/2.1.10/

## Loose-ends scan (post-release)

| Item | Status | Action |
| --- | --- | --- |
| Working tree / push | Clean; synced with `origin/main` | None |
| Tests | **263** green | None |
| Installers | **2.1.10** PyPI + Homebrew tap + local pipx/brew | None |
| Doctor | Five collectors green | None |
| LaunchAgent `com.djbclark.aiuse` | Loaded | Let it run |
| OpenUsage CLI on PATH | Optional (HTTP loopback OK) | Settings → Command Line → Install if desired |
| OpenUsage.app | Needed for HTTP if no CLI | `open -ga OpenUsage` |
| **Step 33** / [#1](https://github.com/djbclark/aiuse/issues/1) | Blocked on [claude-swap#170](https://github.com/realiti4/claude-swap/issues/170) | Wait for upstream |
| **Step 35** | Parked (ccusage ≠ plan %) | Do not start unless asked |
| Product backlog #2–#9 | Closed | — |
| Shared semantics v0.1 | In-tree + pytest dogfood | Peer tickets optional last step |
| Public announce | [#10](https://github.com/djbclark/aiuse/issues/10) draft ready | **Operator posts** when ready |
| MCP stdio | Deferred when #5 shipped serve MVP | Only if agents need native MCP |
| Full release | Done this session (2.1.10) | — |

## Operator preferences (standing)

- Commit early/often; push after every commit.
- **Full releases only when explicitly requested.**
- Do not install/configure external collectors inside feature work; use
  `packaging/install-deps.sh` or site `just install-aiuse-deps`.
- Do not use ccusage as plan 5h/7d authority.
- Open-ended “what next?” → **do not restart fix plan at Step 1**.
- Scheduled agents → **site-djbclark** `site_agents`.

## Next options (when resuming)

1. **Announce** — [#10](https://github.com/djbclark/aiuse/issues/10) (Show HN / Lobsters / …); no auto-post.
2. **Wait / poll Step 33** — cswap `lastGoodUsage` when #170 merges ([#1](https://github.com/djbclark/aiuse/issues/1)).
3. **Let it run** — hourly snapshots densify History.
4. **Optional polish** — MCP stdio on top of `aiuse serve`; more golden fixtures; peer outreach for shared semantics.
5. **Parked Step 35** — only if operator asks.

## Quick verification

```bash
aiuse --version   # 2.1.10
aiuse doctor
aiuse suggest
aiuse status
.venv/bin/python -m pytest -q
just -f ~/ops/site-djbclark/justfile site-agents-status
```

## Key docs

| Doc | Purpose |
| --- | --- |
| [`competitive-landscape.md`](competitive-landscape.md) | Positioning vs quotabot / onWatch / Layer 1 (post-#2–#9) |
| [`companion-stack.md`](companion-stack.md) | Ambient tools + status/prompt |
| [`agent-api.md`](agent-api.md) | `aiuse serve` loopback HTTP |
| [`history-learning.md`](history-learning.md) | Snapshots + History section |
| [`shared-quota-semantics/`](shared-quota-semantics/) | v0.1 schemas + fixtures |
| [`antigravity-pools.md`](antigravity-pools.md) | Gemini vs Claude/GPT pools |
| [`json-contract.md`](json-contract.md) | Stable JSON + `suggestion` + `history` |
| [`packaging.md`](packaging.md) | Release / OIDC / Homebrew |

## Handoff rule

Update **this file** + [`AGENTS.md`](../AGENTS.md) when ending a session with
durable state; commit and push.
