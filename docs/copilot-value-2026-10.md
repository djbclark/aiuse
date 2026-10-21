# Copilot value check (2026-10-21)

Related reading: [`AGENTS.md`](../AGENTS.md) · [`README.md`](../README.md)

Operator question: "Is Copilot good value? It seems like it always runs out
quickly and is $30." Answer below is grounded in aiuse's own snapshot/ledger
history plus the June-2026 Copilot billing model.

## What the account is on

aiuse's meter shows a **7,000-credit/month allowance** (plan string
`Individual_pro`). Since 2026-06-01 Copilot bills usage in **AI credits**
(1 credit = $0.01, token-priced at roughly API list — e.g. Claude Sonnet
5/5.5 ≈ $2/M in, $10/M out). The 7,000-credit tier is **Pro+**: $39/month or
$390/year (**$32.50/month** — the "$30"). Code completions and next-edit are
unlimited on paid plans; only chat/agent/premium models burn credits. Unused
credits do **not** roll over; the counter resets on the calendar 1st.

## Observed burn (aiuse snapshots)

| Cycle (reset) | Outcome                                                          |
| ------------- | ---------------------------------------------------------------- |
| Aug (Sep 1)   | ~~30% used — 4,887/7,000 left (~~$49 face value wasted)          |
| Sep (Oct 1)   | ~94% used — nearly maxed                                         |
| Oct (Nov 1)   | capped day 21 at 1.5× even pace, −327 into overage, 10 days left |

Also: omp's default model role is `github-copilot/gpt-5.6-luna`, so agent
routing drains the same pool — October's last-week sprint burned ~5,100
credits in a few days.

## Verdict

For this usage pattern — bursty heavy months alongside a large redundant
subscription fleet — Pro+ is the wrong shape:

- In a maxed month $39 buys $70 of face value (~56¢ per API-dollar; ~46¢
  annual) — a real discount **only** vs API list, which the fleet mostly
  avoids, and only in months that max out.
- In a light month (Aug) it is strictly worse than pay-as-you-go: ~$49 of
  the bundle evaporated with no rollover.
- Structural: no rollover + calendar reset + agent-mode burn → capped around
  day 20–21 of heavy months, exactly the "runs out quickly" experience.

Recommendation (2026-10-21): **downgrade to Pro ($10)** — keeps unlimited
completions plus a $15-credit buffer — and route heavy agent work to the
existing pools; revisit Pro+ annual only if Copilot-specific models become
load-bearing, then with a small explicit overage budget (~$3/month observed)
so the last third of heavy months does not stall.

Sources: [Copilot plans & pricing](https://github.com/features/copilot/plans) ·
[Models and pricing (docs)](https://docs.github.com/copilot/reference/copilot-billing/models-and-pricing) ·
[Usage-based billing announcement](https://github.blog/news-insights/company-news/github-copilot-is-moving-to-usage-based-billing) ·
[No-rollover legacy doc](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/copilot-requests)
