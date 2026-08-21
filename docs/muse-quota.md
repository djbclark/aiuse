# Muse (Spark) quota

Muse Spark / Muse Code is **pay-as-you-go** through the Meta Model API, not a seat subscription. This doc states what `aiuse` shows and how it is collected.

## Billing model

| Aspect               | Muse today                                                                                         |
| -------------------- | -------------------------------------------------------------------------------------------------- |
| API                  | `https://api.meta.ai/v1` (`x-api-version 1.0.0`, `Authorization: Bearer …`)                        |
| Dashboard            | `https://dev.meta.ai/usage` + `/billing`                                                           |
| Pricing              | Standard `$1.25` / 1 M input, `$4.25` / 1 M output; Contributor `$0.10` / 1 M input (data-sharing) |
| Rate limits          | Standard `3000 req/min`, `4 M tokens/min`; Contributor `60 req/min`, `2.1 M tokens/min`            |
| Subscription windows | **None shipped** — no 5 h / weekly / monthly pool to burn before reset                             |

Rate limits are burst throttles, not use-or-lose allotments. `aiuse` therefore does **not** turn `60 req/min` into a `QuotaWindow`; it notes the limit and ranks Muse in the `n/a` band (like `openrouter` / `deepseek`), not as `use`/`slow`.

If Meta later ships a contributor weekly credit pool (like `z.ai Lite`'s 2 k / 10 k or `clinepass`'s 5 h/weekly/monthly nesting), the same collector will promote to `SUBSCRIPTION_WINDOW` with `window_minutes` (300 / 10080 / 43200) and `analysis.provider_overrides.muse.shared_allotment` without a provider-id change.

## What `aiuse` shows

- **Provider id:** `muse` (canonical). Aliases `meta`, `muse-spark`, `muse-code`, `metamuse` → `muse` via `PROVIDER_ID_ALIASES`. Display name is `muse` (grep-able — `grep -i muse`).
- **Without a key:** no `muse` row at all (like `openrouter` when `AIUSE_OPENROUTER_MANAGEMENT_KEY` is absent). Correctness: absent credential → `[]`, not an error.
- **With a key, balance shape** (`{"data":{"total_credits":…,"total_usage":…}}` or `{"balance":…}`): one `n/a` row `balance $X.XX (pay-as-you-go)`, `billing_kind=PREPAID_BALANCE` or `PAYG_API`, `balance_usd` set.
  ```
  n/a   muse     —  —  balance $18.25 (pay-as-you-go)
  ```
- **With a key, spend shape** (`{"spend":…,"limit":…}` / `{"remaining":…}`): `PAYG_API` with `usage_credits` (`used`/`limit`/`remaining` + `balance_usd = remaining`).
- **With a key, windows shape** (`{"limits":[{"type":"five_hour","percentUsed":12,"resetsAt":"…"}]}` or `{"windows":…}`): real burn windows `Muse 5-hour` / `Muse weekly` / `Muse monthly` / `Muse daily` with `SUBSCRIPTION_WINDOW`. Only present if Meta ships subscription credits.
- **401/403 with a key:** one error row `provider=muse error="Muse API rejected the key (HTTP 401…)"` so the human knows to rotate `META_API_KEY` / re-run `muse login`.
- **Source label:** `Muse (native)` (`muse` in `SOURCE_LABELS`), lowest priority in `DEFAULT_SOURCE_PRIORITY` (native second source if `openusage`/`codexbar` ever add `muse`).

## Collector

**File:** `src/aiuse/collectors/muse.py` — dual-auth native with mutual failover (Bearer preferred, cookie fallback).

- **Bearer path (stable):** `AIUSE_MUSE_API_KEY` → `META_API_KEY` → `secretspec get MUSE_API_KEY/META_API_KEY`; probes `https://api.meta.ai/v1` candidates ` /usage → /billing/usage → /me/usage → /credits → /billing` (first 200 wins). `AIUSE_MUSE_API_URL` override pins the path.
- **Cookie path (browser):** `AIUSE_MUSE_COOKIE` or `secretspec get MUSE_COOKIE` (from `aiuse credential refresh muse --from chrome`); `GET https://dev.meta.ai/usage` to scrape `LSD` + `fb_dtsg` (`DTSGInitialData`) + `team_id` (`active_team_id` / `?team_id=`), then `POST https://dev.meta.ai/api/graphql/` `doc_id 9128374650192834` (`MuseDevBillingBalanceQuery` variables `{"team_id":…}`) → `billing_info {balance, credit_limit, remaining_budget}`. Override `AIUSE_MUSE_TEAM_ID` if auto-scrape fails.
- **Failover:** Bearer tried first; on 401/403/404/timeout it falls through to cookie, and vice-versa. Absent both → `[]`.
- **Display:** `PREPAID_BALANCE` / `PAYG_API` → `n/a` band like `deepseek`/`openrouter`/`opencode-zen`: `balance $X.XX (no expiry)` with `balance_usd` (for `credit_limit/remaining_budget` also `usage_credits`).
- **Timeout:** `timeouts.muse` (or `default`/`force`), same as every other collector (`runner.py` + `config.py` `KNOWN_*` sets).
- **Failure contract:** timeout / 5xx / non-JSON → `CollectorError` (→ `snapshot.collector_errors`); 401/403 after both transports → `AccountUsage(error=…)`; no credential → `[]`.

## Verify

```bash
# No credential → no row, no crash (like deepseek/openrouter/opencode-zen)
aiuse --json -q | jq '.snapshot.accounts[] | select(.provider=="muse")'
# → empty (collector returned [])

# Bearer (API key) — preferred
AIUSE_MUSE_API_KEY=sk_test AIUSE_MUSE_API_URL=https://api.meta.ai/v1/usage aiuse --json -q | jq '.snapshot.accounts[] | select(.provider=="muse")'

# Cookie (browser) — refresh then live-collect; mutual failover
aiuse credential refresh muse --from chrome --dry-run   # validates dev.meta.ai cookie + GraphQL billing_info
aiuse credential refresh muse --from chrome --yes       # saves MUSE_COOKIE via secretspec
AIUSE_MUSE_COOKIE='llm_sess=...' aiuse --json -q | jq '.snapshot.accounts[] | select(.provider=="muse")'
# balance $X.XX (no expiry) in n/a band, like deepseek / opencode-zen
AIUSE_MUSE_COOKIE='llm_sess=...' AIUSE_MUSE_TEAM_ID=123 aiuse --json -q | jq '.snapshot.accounts[] | select(.provider=="muse")'

# Diagnostics
aiuse --json -q | jq '.snapshot.collector_errors | map(select(contains("muse")))'
```

The 15-minute browser trace that nails the endpoint (once, on a credentialed machine):

1. Log into `https://dev.meta.ai`, open DevTools → Network, filter `api.meta.ai`.
2. Visit `https://dev.meta.ai/usage`, copy the XHR that populates the balance/limits tile → `curl -H "Authorization: Bearer $META_API_KEY" <that URL>` should return 200 outside the browser.
3. If it is GraphQL, note the operation name — the collector's candidate list can be narrowed to that single path.

## Relation to upstream

- **OpenUsage** tracks Muse as `robinebers/openusage#1078` ("Support new Muse Code from Meta", 2026-08-06) but has no shipped plugin (no `plugins/muse/`). When it ships, `aiuse` will auto-cross-check it via `openusage_ai`.
- **CodexBar** (`codexbar config providers --format json`, 69 providers on this host) has no `muse`/`meta` provider.
- **`muse` CLI itself** has no `muse usage --json` to shell out to — hence native HTTP is the correct layer, not a subprocess wrapper.

## Config

```toml
# ~/.config/aiuse/config.toml
[collectors.muse]
enabled = true   # uses AIUSE_MUSE_API_KEY / META_API_KEY, or: muse login / muse auth set

# Optional: pin the endpoint if Meta moves it (or for tests)
# AIUSE_MUSE_API_URL is an env var, not TOML — e.g. AIUSE_MUSE_API_URL=https://api.meta.ai/v1/usage

[timeouts]
muse = 45
```
