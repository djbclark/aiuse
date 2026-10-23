# ClinePass quota

ClinePass is a subscription with **three nested windows** — the same shape as
OpenCode Go, not a prepaid wallet.

Official [usage docs](https://docs.cline.bot/getting-started/clinepass):

| Window         | What it is     |
| -------------- | -------------- |
| 5-hour rolling | Burst cap      |
| Weekly         | Calendar week  |
| Monthly        | Calendar month |

Burning the 5-hour bar spends the same ClinePass allotment as weekly/monthly.
`analysis.provider_overrides.clinepass.shared_allotment` is **true**, so pace
scoring uses the longest window.

List price used for value-at-risk is **$10/mo** (promo first months exist;
override `[plans.clinepass]` if yours differs).

## What `aiuse` shows

- Labels: `ClinePass 5-hour` / `weekly` / `monthly` (CodexBar slots and the
  native `clinepass` collector).
- Sources: CodexBar `api` (preferred) and native `GET …/plan/usage-limits`
  when `AIUSE_CLINE_API_KEY` or `sudo-secretspec get CLINE_API_KEY` is set.
- A fresh or lightly used month is `mid`/`slow` with all three clocks filled.
  Do not treat a low 5-hour % as a separate “burn this” pool.

Cline pay-as-you-go API balance is a different product and is not these rows.

## Several accounts

Each ClinePass account gets its own labelled row once both sources know about
it (2026-10-23):

- **CodexBar:** `[collectors.codexbar] all_accounts = ["clinepass"]` adds
  `--all-accounts`, so every CodexBar token account returns its own row
  (`account` is the token-account label). Those providers skip the widget
  snapshot, which holds only the active account, and are queried live. A
  provider with no token accounts ("No token accounts configured") or no
  usable rows falls back to the plain query.
- **Native:** one `[[collectors.clinepass.accounts]]` table per account, with
  `label` plus `env` and/or `secret`. The key comes from `env` first, then
  `secretspec get --file <aiuse manifest> <secret>`, then
  `sudo-secretspec get <secret>`. One account's failure is an error row for
  that label; the others still report. With no tables, the single unlabelled
  `AIUSE_CLINE_API_KEY` / `CLINE_API_KEY` path is unchanged.

Use the CodexBar token-account names as native labels. Selection then runs per
account (an account only native reports keeps its row), and the cross-check
compares only rows whose labels match case-insensitively; an account one source
lacks is noted as unavailable, not as a disagreement.
