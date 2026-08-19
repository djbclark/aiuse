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
