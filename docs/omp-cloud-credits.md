# omp: Claude API subscription credits

Related reading: [`AGENTS.md`](../AGENTS.md) · [`README.md`](../README.md) ·
[`docs/provider-identity.md`](provider-identity.md)

**omp** tracks the **monthly API credit** from claude.ai **Settings → Billing →
API credits** (Max/Ultra/Team link to a Console org — e.g. $200/month, linked
2026-10-13, **Next credit** on the billing cycle date). It is **not**:

- **Usage credits** on the same page (“Buy usage credits… **$0.00** current
  balance”) — that wallet is for extra claude.ai / app usage after plan limits,
  not the linked Console API grant omp spends.
- **Cloud session credits** on the Usage page (`claude-cloud-credits` / CodexBar
  Claude OAuth row).
- The Console **tier monthly spend cap** (e.g. Scale **$200,000**) — a rate-limit
  ceiling, not the promotional grant size.

## Grant clock (operator source of truth)

On [claude.ai billing](https://claude.ai/new#settings/billing):

1. **API credits** — monthly amount ($200 on Ultra), org link date, **Next credit**
   (when the next deposit arrives and the current grant ends). For djbclark
   (2026-11-04): linked **Oct 13, 2026**; **Next credit Nov 6, 2026**.
2. CodexBar’s Claude web row exposes a close timestamp as
   `usage.subscriptionRenewsAt` (here `2026-11-05T21:23:03Z`, same cycle as
   “Nov 6” in local billing UI).

Record that clock in config or with `aiuse set-credits omp` when collectors
cannot see it:

```toml
[credits.omp]
organization = "Freedom Included"
grant_total_usd = 200
granted_at = "2026-10-13"
next_credit_at = "2026-11-06"
```

When several Console API organizations exist, CodexBar returns the **first**
org whose billing it can read. Set `organization` to the one linked on claude.ai
(**Freedom Included** for this account); aiuse **drops** `omp` rows for any
other org name so a second Console account cannot pollute quota. Until CodexBar
can target a specific org ([steipete/CodexBar#4654](https://github.com/steipete/CodexBar/issues/4654)),
a mismatch means no automated row — use `set-credits` from the billing page.

`expires_at` and `next_credit_at` are equivalent in config. Date-only values
parse as **UTC midnight** on that day (CodexBar Console labeling convention).

## Data sources (precedence)

1. **CodexBar Claude Platform** (`codexbar usage --provider claudeplatform`) —
   Console `/prepaid/credits`, `subscription_api_credit` grant in `usage.primary`
   (or exhausted grant under “Other credit grants”). Best automated remaining
   dollars when Console cookies work.
2. **Operator reading** — `aiuse set-credits omp --remaining … --total 200
--granted 2026-10-13 --expires 2026-11-06` from the billing page (use
   `--remaining 0` when the grant is exhausted).
3. **Anthropic Admin API `cost_report`** (optional) — org-wide API spend since
   `granted_at`, not promotional-only; do not treat tier spend caps as the grant.

## How aiuse models it

`collectors/codexbar.py:_omp_from_claudeplatform_row` maps the Claude Platform
row to provider `omp` (pool `claude-api` / `omp_claude`):

1. One window, label `credits`, percentages of the **grant** (`refill_capacity`
   = monthly API credit, e.g. $200), `resets_at` = next credit / grant end.
2. `balance_usd` / `spent_usd` are dollars left and spent **from that grant**.
3. `usage_credits` is intentionally unset (not the Usage-credits wallet).

## Fallback: omp's own API-key pool

When CodexBar reads no grant (cookies expired, grant lapsed), the `omp_agent_db`
collector reports the pool from `~/.omp/agent/agent.db` (read-only; only
`provider`, `credential_type`, `disabled_cause` and the rate-limit block table are
read, never the key). The row says whether omp can spend now (binary present,
an enabled Anthropic `api_key`, no active block) and has no balance, so
`headroom_percent` is null. A CodexBar grant row outranks it.

## Rate-window gate, cache, disabling

Unchanged from prior docs: shared Claude quota can rate-gate omp; durable latch
in `rate-gates.json`; widget cache triggers a live `claudeplatform` query;
`[disabled_services] omp` skips the row.

## Related

- [Monthly API credits (Help Center)](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-and-team-plans)
- [CodexBar claudeplatform.md](https://github.com/steipete/CodexBar/blob/main/docs/claudeplatform.md)
- JSON: [`json-contract.md`](json-contract.md#omp--an-expiring-dollar-grant)
