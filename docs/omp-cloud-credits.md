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
2. **OpenAI**: The OpenAI route advertises configuration only: a non-blank string
   `providers.openai.apiKey` in `models.yml`, the `omp` binary on PATH, and no
   `openai` entry in `disabledProviders` in the same directory's `config.yml`
   (or `config.yaml` only when `config.yml` is absent).
   Each YAML read is capped at 256 KiB and accept only regular files, including
   symlinks to regular files. Special files fail closed without waiting for a
   FIFO writer. YAML aliases and merge keys are unsupported and rejected before
   expansion; unused anchors and quoted literal text remain supported.
   A present empty `config.yml` takes precedence over `config.yaml`. Missing both config filenames means default
   settings; unreadable, malformed, oversized or non-mapping config, or a
   `disabledProviders` value other than a flat list of strings, makes the row
   unusable with a sanitized reason. Missing, unreadable, malformed or oversized
   models produce no OpenAI row. Missing omp also produces no OpenAI row.

   The default agent directory is `~/.omp/agent`; `PI_CONFIG_DIR` changes `.omp`.
   `OMP_PROFILE` (`PI_PROFILE` fallback only when unset) selects
   `~/<config-root>/profiles/<name>/agent` for a non-default profile and takes
   precedence over `PI_CODING_AGENT_DIR`. Otherwise `PI_CODING_AGENT_DIR` resolves
   relative to cwd without expanding a literal `~`, like Node `path.resolve`.
   An explicitly empty or `default` `OMP_PROFILE` discards an inherited agent
   directory exactly equal to the valid legacy `PI_PROFILE` directory; a genuinely
   custom override remains honored. Invalid active profile names produce no OpenAI row,
   including when an explicit models override is set.
   `AIUSE_OMP_MODELS_YML` overrides the models path; its parent also supplies
   `config.yml` / `config.yaml`, keeping reads in one directory. Automatic `models.yaml`
   fallback and `models.json` migration are unobserved; use `AIUSE_OMP_MODELS_YML`
   to select an existing models file explicitly.
   Directory behavior was checked against omp v18.18.0 `packages/utils/src/dirs.ts`
   on 2026-11-07.

   omp is never invoked and no credential is resolved, executed or emitted.
   This is an advertisement signal, not enforcement or authentication: project
   overlays, CLI/runtime flags, extensions and `PI_CONFIG_FILES` overlays are
   not observed. Failing key commands and unset environment references still
   count by presence; credit balance and inference success are unverified.
   Custom `baseUrl` retains `openai_api` and may spend at a proxy.
   Explicit `[usage_sources] omp` pins suppress the independent OpenAI row,
   preserving source-pin behavior. Without a pin the OpenAI row coexists with
   the selected Claude/Anthropic row. Its pool key remains `omp_openai` and its
   matrix scope `openai-api`.

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
