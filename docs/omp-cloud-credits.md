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

## Fallback: omp's own API-key pools

When CodexBar reads no Claude grant (cookies expired, grant lapsed), the
`omp_agent_db` collector can still report local omp API-key routes by presence
only.

1. **Anthropic**: reads `~/.omp/agent/agent.db` read-only; only `provider`,
   `credential_type`, `disabled_cause` and the rate-limit block table are read,
   never the key. The row says whether omp can spend now (binary present, an
   enabled Anthropic `api_key`, at least one such key not blocked) and has no
   balance, so `headroom_percent` is null. A CodexBar grant row outranks this
   fallback whatever its age; the rate-window gate below still applies to it.
2. **OpenAI**: The OpenAI route requires a non-empty `providers.openai.apiKey` string in
   the agent directory's `models.yml` (`AIUSE_OMP_MODELS_YML` overrides the
   path) and the `omp` binary on PATH. The default agent directory is
   `~/.omp/agent`; `OMP_PROFILE` (`PI_PROFILE` fallback) selects
   `~/.omp/profiles/<name>/agent` for a non-default profile, otherwise
   `PI_CODING_AGENT_DIR` can relocate it. aiuse reads at most 256 KiB before
   safe YAML parsing; missing, unreadable, malformed or oversized models mean
   no OpenAI row. Symlinks are supported. The apiKey string counts by presence,
   including exact-case environment-variable names whether unset or empty;
   aiuse never executes or emits the value. A custom `baseUrl` retains the
   `openai_api` provider route and may spend at a proxy.

   Selectability is delegated to one `omp models --json --no-extensions` call
   per collection with an OpenAI key present (typically about 2 seconds;
   15-second timeout, stdout capped at 8 MiB, stderr discarded). Its JSON must
   contain a `models` array with an entry whose `provider` is `openai`.
   aiuse does not replicate omp settings merging. The subprocess inherits the
   aiuse process environment and cwd, so omp resolves profiles, config overlays
   and project scopes itself; extensions are not loaded. A missing binary
   produces no OpenAI row. An omitted provider or any oracle failure (timeout,
   nonzero exit, invalid JSON/shape, output too large or launch error) leaves
   a configured row unusable with a short `api_key_pool.reason`, excluded from
   `--available`. This does not validate the key or guarantee inference success;
   another omp process's CLI/runtime or extension overrides may differ.

   This is its own `omp` row (`source: "omp_models_yml"`,
   `pool_family: "openai_api"`, internal key `omp_openai`, matrix scope
   `openai-api`), independent of the Claude grant and Anthropic key row.
   Claude shared-window gates and operator Claude-credit overrides do not
   change it. Headroom stays unknown; models hint is `omp TUI - OpenAI API key
(platform credits): openai/gpt-* models`.

## Rate-window gate, cache, disabling

Unchanged from prior docs: shared Claude quota can rate-gate omp; durable latch
in `rate-gates.json`; widget cache triggers a live `claudeplatform` query;
`[disabled_services] omp` skips the row.

## Related

- [Monthly API credits (Help Center)](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-and-team-plans)
- [CodexBar claudeplatform.md](https://github.com/steipete/CodexBar/blob/main/docs/claudeplatform.md)
- JSON: [`json-contract.md`](json-contract.md#omp--an-expiring-dollar-grant)
