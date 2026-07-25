# Session handoff (current)

**Date:** 2026-07-25  
**Branch:** `main` (clean, synced with `origin/main`)  
**Local tree:** `~/src/aiuse`  
**Remote:** https://github.com/djbclark/aiuse  
**Tests:** `.venv/bin/python -m pytest -q` — **212** passing  
**Version:** **2.1.5** on PyPI + Homebrew + local `pipx` / `brew`

Fresh agents: start at [`AGENTS.md`](../AGENTS.md).

## Reopen checklist (operator)

1. Open Cursor workspace at **`~/src/aiuse`**.
2. Confirm: `aiuse --version` → `2.1.5`; `aiuse doctor` → `~/.config/aiuse/`.
3. LaunchAgent: `just -f ~/ops/site-djbclark/justfile site-agents-status`
   — expect `com.djbclark.aiuse` loaded ([`scheduling.md`](scheduling.md)).

## Done this stretch (2026-07-24)

| Area | Notes |
| --- | --- |
| Fix plan Steps **1–32** + **34** | Complete (no restart) |
| **E — Packaging** | Through **2.1.5**: OIDC PyPI, Homebrew tap, MIT, releases |
| **H — LaunchAgent** | Hourly `com.djbclark.aiuse` via site-djbclark `site_agents` |
| **G — History** | `persist_snapshots` + `learn_from_history: auto` |
| **G — deeper UX** | `--full` **History** section; blended-with-history pace notes; `learned_sample_count` in JSON |
| **Prepaid ladder** | Deepseek/OpenRouter non-expiring → `n/a` band (empty → n/a → slow → mid → use) |
| **Competitive note** | [`competitive-landscape.md`](competitive-landscape.md) — Layer 1 monitors vs Layer 2 ranking (quotabot / onWatch) |

Release: https://github.com/djbclark/aiuse/releases/tag/v2.1.5  
PyPI: https://pypi.org/project/aiuse/2.1.5/

## Loose-ends scan (close-out)

| Item | Status | Action |
| --- | --- | --- |
| Working tree / push | Clean; `main` == `origin/main` | None |
| Tests | 210 green | None |
| Installers | 2.1.5 on PyPI + `djbclark/homebrew-aiuse` | None |
| Local PATH | `~/.local/bin/aiuse` (pipx) shadows Homebrew; both **2.1.5** | Prefer one channel if confused |
| LaunchAgent | Loaded; interval **3600s**; persist + learn auto | Let it run |
| Snapshot cache | ~80+ files under `~/.cache/aiuse/snapshots` | Learning already active |
| **Step 33** | Blocked: [claude-swap#170](https://github.com/realiti4/claude-swap/issues/170) **open**; [aiuse#1](https://github.com/djbclark/aiuse/issues/1) open | Wait for upstream merge |
| **A — Live smoke** | Not done this session | Operator-owned when convenient |
| **Step 35** | Parked (ccusage / local burn ≠ plan %) | Do not start unless asked |
| New features | None queued | Operator pick |

Nothing else actionable in-repo without operator choice or upstream #170.

## Operator preferences (standing)

- Commit early/often; push after every commit (SSH for workflow files).
- Do not install/configure `cswap` / `codexbar` / `tokscale` in code changes.
- Do not use ccusage as plan 5h/7d authority.
- Open-ended “what next?” → **do not restart fix plan at Step 1**.
- Scheduled agents / LaunchAgents for this Mac → **site-djbclark** `site_agents`, not ad-hoc plists.

## Next options (when resuming)

1. **Wait / poll Step 33** — when #170 merges, implement official `lastGoodUsage` in `collectors/cswap.py` (cache hydrate → fallback). Design: [`cswap-reliability.md`](cswap-reliability.md).
2. **Live smoke (A)** — eyeball `aiuse --full` History + collectors against real accounts.
3. **Let it run** — hourly collect densifies history; no code needed.
4. **New feature** — only if operator names one.

## Quick verification

```bash
just -f ~/ops/site-djbclark/justfile site-agents-status
aiuse --version   # 2.1.5
ls ~/.cache/aiuse/snapshots | wc -l
aiuse --full -q --no-tui | head -30
.venv/bin/python -m pytest -q
```

## Handoff rule

Update **this file** + [`AGENTS.md`](../AGENTS.md) when ending a session with
durable state; commit and push.
