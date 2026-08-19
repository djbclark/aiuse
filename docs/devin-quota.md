# Devin quota

Devin self-serve (Free / Pro / Max) is a **subscription** with included
**daily and weekly** usage, then optional extra-usage dollars. It is not a
prepaid wallet and it has **no monthly bar**.

Official [pricing](https://devin.ai/pricing):

| Plan | Price   | Quota                                             |
| ---- | ------- | ------------------------------------------------- |
| Free | $0      | Light daily + weekly included usage               |
| Pro  | $20/mo  | Larger daily + weekly included usage              |
| Max  | $200/mo | Larger weekly allowance (daily cap may be absent) |

Daily ⊂ weekly: the day bar is a short rate limit on the same included pot.
`analysis.provider_overrides.devin.shared_allotment` is **true**. No default
`monthly_price` — Free is $0; set `[plans.devin] monthly_price` on Pro/Max.

CodexBar reads `GET app.devin.ai/api/…/billing/quota/usage` (Chrome session).
The API often omits `plan` on Free; 0% / 0% with a daily reset tomorrow is a
fresh unused allotment, not a missing subscription.

## What `aiuse` shows

- Labels: `Devin daily` / `Devin weekly`.
- Display id: `devin`.
- The usage table has no Daily column. The daily bar occupies **5H** (the
  short clock); weekly occupies **WEEK**.
- Extra-usage balance with `limit: 0` is ignored (no on-demand wallet).
