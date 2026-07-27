# Session handoff (current)

**Date:** 2026-07-27  
**Branch:** `main` (synced with `origin/main` after this handoff)  
**Local tree:** `~/src/aiuse`  
**Remote:** https://github.com/djbclark/aiuse  
**Tests:** `.venv/bin/python -m pytest -q` — **291** passing  

**Package version:** **2.1.15** on PyPI + GitHub + Homebrew  

Fresh agents: start at [`AGENTS.md`](../AGENTS.md).  
Open-ended “what next?” → [`next-options.md`](next-options.md) (not Step 1 of the fix plan).

## Reopen checklist (operator)

1. Open workspace at **`~/src/aiuse`**.
2. Confirm package: `.venv/bin/aiuse --version`, global `aiuse --version`, and
   `/opt/homebrew/bin/aiuse --version` → **2.1.15**.
3. `aiuse doctor` → five collectors green; **macOS codesign (caut)** should show
   **ok stable-signed** (identity `aiuse-local-codesign`) after operator setup.
4. `aiuse trust status` → caut Authority + CodexBar Cache account list.
5. LaunchAgent: `just -f ~/ops/site-djbclark/justfile site-agents-status`
   — expect `com.djbclark.aiuse` loaded ([`scheduling.md`](scheduling.md)).
6. Data sources: `./packaging/install-deps.sh --check` or site
   `just aiuse-deps-status`.

## Done this stretch (2026-07-27)

| Area | Notes |
| ---- | ----- |
| **`aiuse trust` (caut)** | Stable local codesign so Keychain Always Allow survives `cargo install`; `setup` / `sign-caut` / `probe` / `ensure-identity` / `grant-guide`; doctor WARN when enabled+adhoc; install-deps hint + `AIUSE_AUTOSIGN_CAUT=1` |
| **`aiuse trust fix-codexbar-cache`** | CodexBar [#679](https://github.com/steipete/CodexBar/issues/679): rewrite `com.steipete.codexbar.cache` ACLs so **CodexBarCLI** is trusted (not only `.app`); `--dry-run` / `--account` |
| **Docs** | [`macos-keychain-trust.md`](macos-keychain-trust.md), plan, collectors note; queue: **normal CLI must not depend on hourly snapshot cache** ([`next-options.md`](next-options.md)) |
| **Operator Mac config** | `collectors.caut.enabled: true` in `~/.config/aiuse/services.yaml`; caut **already stable-signed** with `aiuse-local-codesign` |
| **CodexBar prefs** | `promptMode=never`, `readStrategy=securityFramework`; clear `claudeOAuthKeychainDeniedUntil` when it reappears after Deny |
| **2.1.15 full release** | Tag `v2.1.15`; GitHub Release; PyPI OIDC success; Homebrew formula + tap `djbclark/homebrew-aiuse`; local pipx + brew upgraded |

### Issue estimates (scan)

| #                                                                                                 | Hours                | Norm. tok | Norm. $  | Title                                                    |
| ------------------------------------------------------------------------------------------------- | -------------------- | --------- | -------- | -------------------------------------------------------- |
| [#1](https://github.com/djbclark/aiuse/issues/1)                                                  | 1–3h after cswap#170 | ~50k–300k | ~$0.5–8  | Track upstream cswap last-good (**blocked**)             |
| [#2](https://github.com/djbclark/aiuse/issues/2)–[#9](https://github.com/djbclark/aiuse/issues/9) | —                    | —         | —        | **done**                                                 |
| [#10](https://github.com/djbclark/aiuse/issues/10)                                                | 0.5–2h               | ~10k–100k | ~$0–2    | Announce **2.1.15** (operator; **do not auto-post**)     |
| [#11](https://github.com/djbclark/aiuse/issues/11)                                                | 8–24h                | ~0.5–4M   | ~$5–80   | Optional: thin MCP stdio over `serve`                    |
| [#12](https://github.com/djbclark/aiuse/issues/12)                                                | 1–4h                 | ~20k–200k | ~$0–5    | Optional: peer outreach shared-semantics (**last**)      |
| [#13](https://github.com/djbclark/aiuse/issues/13)                                                | 2–8h                 | ~0.2–1.5M | ~$2–30   | Optional: richer History (text, not BI)                  |
| [#14](https://github.com/djbclark/aiuse/issues/14)                                                | 4–12h                | ~0.3–2M   | ~$3–40   | Optional: `aiuse watch` pull-refresh (not menubar)       |
| [#15](https://github.com/djbclark/aiuse/issues/15)                                                | 1–4h                 | ~50k–0.5M | ~$0.5–10 | Optional: more golden fixtures                           |

Release: https://github.com/djbclark/aiuse/releases/tag/v2.1.15  

PyPI: https://pypi.org/project/aiuse/2.1.15/

## Loose-ends scan (this handoff)

| Item | Status | Action |
| ---- | ------ | ------ |
| Working tree / push | Clean after this commit; push to `origin/main` | Done with handoff |
| Tests | **291** green | None |
| Installers (remote) | **2.1.15** GitHub + PyPI OIDC + Homebrew tap | None |
| PATH `aiuse --version` | Global pipx + Homebrew + venv → **2.1.15** | None |
| Doctor | Five collectors green; caut **stable-signed** | None |
| caut codesign identity | `aiuse-local-codesign` on `~/.cargo/bin/caut` | Re-run `aiuse trust sign-caut` after every `cargo install` |
| caut Always Allow | Operator may still need one `aiuse trust probe` if dialogs remain | Optional interactive |
| **CodexBar Cache ACL (#679)** | Dry-run listed 5 accounts; **fix may not have been applied yet** | Operator: `aiuse trust fix-codexbar-cache` if CLI still prompts |
| CodexBar `deniedUntil` | Cleared again this handoff (had reappeared → ~2026-07-28 UTC) | If prompts return after Deny, delete key again or menu Refresh |
| LaunchAgent `com.djbclark.aiuse` | Loaded; last exit **2** = success with burn/conserve alerts (normal) | Let it densify History |
| Live CLI vs hourly cache | **Live collect always** for normal `aiuse`; hourly only writes History | Policy queued in next-options — do not invert |
| **Step 33** / [#1](https://github.com/djbclark/aiuse/issues/1) | Blocked on [claude-swap#170](https://github.com/realiti4/claude-swap/issues/170) | Wait for upstream |
| **Step 35** | Parked (ccusage ≠ plan %) | Do not start unless asked |
| Optional polish #11–#15 | Open, **not default** | Only if concrete pain |
| Public announce | [#10](https://github.com/djbclark/aiuse/issues/10) should mention **2.1.15** | **Operator posts** when ready |
| Full release | **2.1.15 complete** | Release only when explicitly requested |

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
- **Normal `aiuse` must live-collect** — never make interactive runs depend on
  the hourly snapshot cache (History-only).

## Next options (when resuming)

Preferred order (detail in [`next-options.md`](next-options.md)):

1. **Operator interactive (if keychain still noisy):**  
   `aiuse trust probe` · `aiuse trust fix-codexbar-cache`  
   (caut already signed on this Mac as of handoff).
2. **Announce** — [#10](https://github.com/djbclark/aiuse/issues/10) for **2.1.15**; no auto-post.
3. **Wait / poll Step 33** — cswap `lastGoodUsage` when #170 merges ([#1](https://github.com/djbclark/aiuse/issues/1)).
4. **Let it run** — hourly snapshots densify History.
5. **Optional polish only if pain** — #11 MCP · #13 History · #14 watch · #15 fixtures · #12 peer (last).
6. **Parked Step 35** — only if operator asks.

## Quick verification

```bash
.venv/bin/aiuse --version          # 2.1.15
aiuse --version                    # pipx: 2.1.15
/opt/homebrew/bin/aiuse --version  # Homebrew: 2.1.15
aiuse doctor
aiuse trust status
aiuse trust fix-codexbar-cache --dry-run
aiuse suggest
aiuse status
.venv/bin/python -m pytest -q
just -f ~/ops/site-djbclark/justfile site-agents-status
```

## Key docs

| Doc | Purpose |
| --- | ------- |
| [`next-options.md`](next-options.md) | **What next** + gap difficulty + issue index + queue |
| [`macos-keychain-trust.md`](macos-keychain-trust.md) | Operator: caut codesign + CodexBar#679 |
| [`macos-keychain-trust-plan.md`](macos-keychain-trust-plan.md) | Design notes for `aiuse trust` |
| [`collectors-caut-openusage.md`](collectors-caut-openusage.md) | caut/OpenUsage collectors |
| [`competitive-landscape.md`](competitive-landscape.md) | Positioning vs quotabot / onWatch / Layer 1 |
| [`companion-stack.md`](companion-stack.md) | Ambient tools + status/prompt |
| [`agent-api.md`](agent-api.md) | `aiuse serve` loopback HTTP |
| [`history-learning.md`](history-learning.md) | Snapshots + History section |
| [`shared-quota-semantics/`](shared-quota-semantics/) | v0.1 schemas + fixtures |
| [`json-contract.md`](json-contract.md) | Stable JSON + `suggestion` + `history` |
| [`packaging.md`](packaging.md) | Release / OIDC / Homebrew |
| [`scheduling.md`](scheduling.md) | LaunchAgent hourly |

## Handoff rule

Update **this file** + [`AGENTS.md`](../AGENTS.md) when ending a session with
durable state; commit and push.
