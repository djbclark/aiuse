# Session handoff (current)

**Date:** 2026-07-25  
**Branch:** `main` (synced with `origin/main` after this handoff)  
**Local tree:** `~/src/aiuse`  
**Remote:** https://github.com/djbclark/aiuse  
**Tests:** `.venv/bin/python -m pytest -q` — **267** passing

**Package version:** **2.1.12** on PyPI + GitHub + Homebrew

Fresh agents: start at [`AGENTS.md`](../AGENTS.md).  
Open-ended “what next?” → [`next-options.md`](next-options.md) (not Step 1 of the fix plan).

## Reopen checklist (operator)

1. Open workspace at **`~/src/aiuse`**.
2. Confirm package: `.venv/bin/aiuse --version`, global `aiuse --version`, and
   `/opt/homebrew/bin/aiuse --version` → **2.1.12**.
3. `aiuse doctor` → five collectors green (OpenUsage often “CLI missing; HTTP
   :6736 responding” — OK).
4. LaunchAgent: `just -f ~/ops/site-djbclark/justfile site-agents-status`
   — expect `com.djbclark.aiuse` loaded ([`scheduling.md`](scheduling.md)).
5. Data sources: `./packaging/install-deps.sh --check` or site
   `just aiuse-deps-status` (cswap, codexbar, caut, OpenUsage, tokscale).

## Done this stretch (2026-07-25)

| Area                                | Notes                                                                                                                                     |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **OpenCode Go history gate**        | Suppress stale 5h history alerts when the current shared monthly window governs; live exhausted state remains authoritative              |
| **Concurrency test stability**      | Replaced a flaky wall-clock threshold with a synchronization-barrier proof that provider queries overlap                                 |
| **2.1.12 full release**             | Honest no-reset history wording + clean quality/security gates; GitHub/PyPI OIDC/Homebrew all verified; local pipx + brew upgraded        |
| **Reset deadline + quality repair** | History-only 5-hour rows without a live reset now say `use more each cycle`; complete `just lint` is green (typing, formatting, security) |
| **2.1.11 release**                  | First reset-time repair borrows a matching live reset when available; PyPI/GitHub release + Homebrew formula                              |
| **Next-options + issues**           | [`next-options.md`](next-options.md); optional polish **#11–#15**; #10 should announce the latest release                                 |
| **Homebrew loose end**              | Public tap lagged at 2.1.11 during release; rebased safely and published **2.1.12** at tap commit `5329aeb`                               |
| **Docs pass**                       | Competitive landscape post-#2–#9; README/AGENTS/shared-semantics/companion/agent-api aligned                                              |
| **2.1.10 release**                  | Shared quota-semantics v0.1 package + dogfood tests                                                                                       |
| **2.1.9 release**                   | Product issues #2–#8 (suggest, status/prompt, serve, forecast, History, health_path, local runtimes)                                      |
| **2.1.8**                           | Antigravity dual pools (Gemini vs Google Claude/GPT)                                                                                      |
| **#2–#9**                           | Closed (product + shared-semantics v0.1)                                                                                                  |
| Fix plan Steps **1–32** + **34**    | Complete — do not restart                                                                                                                 |
| Collectors                          | cswap + CodexBar + caut + OpenUsage + tokscale                                                                                            |
| Config                              | `~/.config/aiuse/` only                                                                                                                   |
| LaunchAgent                         | Hourly `com.djbclark.aiuse` (persist + learn auto)                                                                                        |

### Issue estimates (scan)

| #                                                                                                 | Hours                | Norm. tok | Norm. $  | Title                                                    |
| ------------------------------------------------------------------------------------------------- | -------------------- | --------- | -------- | -------------------------------------------------------- |
| [#1](https://github.com/djbclark/aiuse/issues/1)                                                  | 1–3h after cswap#170 | ~50k–300k | ~$0.5–8  | Track upstream cswap last-good (**blocked**)             |
| [#2](https://github.com/djbclark/aiuse/issues/2)–[#9](https://github.com/djbclark/aiuse/issues/9) | —                    | —         | —        | **done**                                                 |
| [#10](https://github.com/djbclark/aiuse/issues/10)                                                | 0.5–2h               | ~10k–100k | ~$0–2    | Announce latest release (operator; **do not auto-post**) |
| [#11](https://github.com/djbclark/aiuse/issues/11)                                                | 8–24h                | ~0.5–4M   | ~$5–80   | Optional: thin MCP stdio over `serve`                    |
| [#12](https://github.com/djbclark/aiuse/issues/12)                                                | 1–4h                 | ~20k–200k | ~$0–5    | Optional: peer outreach shared-semantics (**last**)      |
| [#13](https://github.com/djbclark/aiuse/issues/13)                                                | 2–8h                 | ~0.2–1.5M | ~$2–30   | Optional: richer History (text, not BI)                  |
| [#14](https://github.com/djbclark/aiuse/issues/14)                                                | 4–12h                | ~0.3–2M   | ~$3–40   | Optional: `aiuse watch` pull-refresh (not menubar)       |
| [#15](https://github.com/djbclark/aiuse/issues/15)                                                | 1–4h                 | ~50k–0.5M | ~$0.5–10 | Optional: more golden fixtures                           |

Release: https://github.com/djbclark/aiuse/releases/tag/v2.1.12

PyPI: https://pypi.org/project/aiuse/2.1.12/

## Loose-ends scan (this handoff)

| Item                                                           | Status                                                                                    | Action                                                       |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Working tree / push                                            | Clean after this commit; push to `origin/main`                                            | Done with handoff                                            |
| Tests / full lint                                              | **266** green; `just lint` clean                                                          | None                                                         |
| Installers (remote)                                            | **2.1.12** GitHub + PyPI (OIDC run `30181813167`) + Homebrew tap                          | None                                                         |
| PATH `aiuse --version`                                         | Global pipx, Homebrew, and project venv all **2.1.12**                                    | None                                                         |
| Doctor                                                         | Five collectors green (OpenUsage HTTP OK)                                                 | Optional OpenUsage CLI install                               |
| LaunchAgent `com.djbclark.aiuse`                               | **Loaded**                                                                                | Let it densify History                                       |
| **Step 33** / [#1](https://github.com/djbclark/aiuse/issues/1) | Blocked on [claude-swap#170](https://github.com/realiti4/claude-swap/issues/170)          | Wait for upstream                                            |
| **Step 35**                                                    | Parked (ccusage ≠ plan %)                                                                 | Do not start unless asked                                    |
| Product backlog #2–#9                                          | Closed                                                                                    | —                                                            |
| Optional polish #11–#15                                        | Open, **not default**                                                                     | Only if concrete pain — [`next-options.md`](next-options.md) |
| Shared semantics v0.1                                          | In-tree + pytest dogfood                                                                  | Peer outreach = #12 (last)                                   |
| Public announce                                                | [#10](https://github.com/djbclark/aiuse/issues/10) draft should target the latest release | **Operator posts** when ready                                |
| Full release                                                   | **2.1.12 complete**; source tag `v2.1.12`, tap commit `5329aeb`                           | Release only when explicitly requested                       |

## Operator preferences (standing)

- Commit early/often; push after every commit.
- **Full releases only when explicitly requested.**
- Do not install/configure external collectors inside feature work; use
  `packaging/install-deps.sh` or site `just install-aiuse-deps`.
- Do not use ccusage as plan 5h/7d authority.
- Open-ended “what next?” → [`next-options.md`](next-options.md); **do not
  restart fix plan at Step 1**.
- Scheduled agents → **site-djbclark** `site_agents`.
- **Do not auto-post** announce (#10) or peer outreach (#12).

## Next options (when resuming)

Preferred order (detail in [`next-options.md`](next-options.md)):

1. **Announce** — [#10](https://github.com/djbclark/aiuse/issues/10) (Show HN / Lobsters / …); no auto-post.
2. **Wait / poll Step 33** — cswap `lastGoodUsage` when #170 merges ([#1](https://github.com/djbclark/aiuse/issues/1)).
3. **Let it run** — hourly snapshots densify History.
4. **Optional polish only if pain** — [#11](https://github.com/djbclark/aiuse/issues/11) MCP · [#13](https://github.com/djbclark/aiuse/issues/13) History · [#14](https://github.com/djbclark/aiuse/issues/14) watch · [#15](https://github.com/djbclark/aiuse/issues/15) fixtures · [#12](https://github.com/djbclark/aiuse/issues/12) peer (last).
5. **Parked Step 35** — only if operator asks.
6. **Do not chase by default** — menubar app, own scrapers, LiteLLM router, ccusage-as-plan-%.

## Quick verification

```bash
.venv/bin/aiuse --version       # 2.1.12
aiuse --version                 # pipx: 2.1.12
/opt/homebrew/bin/aiuse --version  # Homebrew: 2.1.12
aiuse doctor
aiuse suggest
aiuse status
.venv/bin/python -m pytest -q
just -f ~/ops/site-djbclark/justfile site-agents-status
```

## Key docs

| Doc                                                    | Purpose                                      |
| ------------------------------------------------------ | -------------------------------------------- |
| [`next-options.md`](next-options.md)                   | **What next** + gap difficulty + issue index |
| [`competitive-landscape.md`](competitive-landscape.md) | Positioning vs quotabot / onWatch / Layer 1  |
| [`companion-stack.md`](companion-stack.md)             | Ambient tools + status/prompt                |
| [`agent-api.md`](agent-api.md)                         | `aiuse serve` loopback HTTP                  |
| [`history-learning.md`](history-learning.md)           | Snapshots + History section                  |
| [`shared-quota-semantics/`](shared-quota-semantics/)   | v0.1 schemas + fixtures                      |
| [`antigravity-pools.md`](antigravity-pools.md)         | Gemini vs Claude/GPT pools                   |
| [`json-contract.md`](json-contract.md)                 | Stable JSON + `suggestion` + `history`       |
| [`packaging.md`](packaging.md)                         | Release / OIDC / Homebrew                    |

## Handoff rule

Update **this file** + [`AGENTS.md`](../AGENTS.md) when ending a session with
durable state; commit and push.
