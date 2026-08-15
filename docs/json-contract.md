# JSON contract (`aiuse --json`)

Stable machine-readable fields for scripts and cron. Prefer these keys over
pretty text parsing.

**Related:** exit codes in [README](../README.md#exit-codes); collector timing in
[collector-concurrency.md](collector-concurrency.md); scheduled runs in
[scheduling.md](scheduling.md).

## Top-level payload

Default `aiuse --json` stdout:

```json
{
  "schema_version": "1.0",
  "contract_url": "https://github.com/djbclark/aiuse/blob/main/docs/json-contract.md",
  "contract_command": "aiuse schema",

  // New in 3.0.12+ for on-disk snapshots:
  "complete": true,
  "collection_id": "2026-08-11T133605.800315Z-12345",
  "started_at": "2026-08-11T13:36:00.000000+00:00",
  "completed_at": "2026-08-11T13:36:05.800315+00:00",
  "collector_success_count": 4,
  "collector_failure_count": 0,
  "account_count": 8,

  "snapshot": { ... },
  "alerts": [ ... ],
  "suggestion": { ... } | null,
  "history": { ... }
}
```

`aiuse --json --flatten` stdout:

```json
{
  "collected_at": "2026-08-02T12:00:00+00:00",
  "accounts": [ ... ],
  "alerts": [ ... ]
}
```

`--flatten` omits the live envelope's `snapshot`, `suggestion`, and `history`
keys so callers get the same three-key shape used by on-disk cached snapshots.

## On-Disk Snapshots (Launchd/Hourly Mode)

If `analysis.persist_snapshots` is enabled (e.g. via macOS LaunchAgent), `aiuse` writes a history of payloads to `~/.cache/aiuse/snapshots/`.

Starting in **3.0.12**, these files are written atomically, guaranteeing that no consumer can observe a partial/torn snapshot during creation. A stable copy named `latest.json` is atomically updated so consumers can immediately locate the most recent valid snapshot without performing filename parsing or sorting.

Consumers reading these files should verify the `"complete": true` field before relying on them, though the atomic implementation prevents torn reads natively. Do not rely on lexicographical sorting of timestamps for the "latest" file, as older formats used colon-separated timestamps which sort differently than the newer compact ones. Use `latest.json` or sort by `mtime`.

Files in `~/.cache/aiuse/snapshots/*.json` already use this flattened shape
natively; no flag is needed when reading those files directly.

`aiuse --json --alerts-only`:

```json
{
  "schema_version": "1.0",
  "contract_url": "https://github.com/djbclark/aiuse/blob/main/docs/json-contract.md",
  "contract_command": "aiuse schema",
  "alerts": [ ... ],
  "cross_check_warnings": [ ... ],
  "suggestion": { ... } | null
}
```

(`history` is omitted from `--alerts-only` to keep that payload small.)

(`cross_check_warnings` is only the subset of `snapshot.cross_checks` with
`status == "warning"`.)

### `suggestion` (optional top-level)

Single best **burn** window to use next, or `null` when there is nothing
urgent. Prefer this over re-ranking `alerts[]` in scripts. Also available via
`aiuse suggest` (human one-liner) / `aiuse suggest --json`.

| Field               | Type           | Notes                       |
| ------------------- | -------------- | --------------------------- |
| `provider`          | string         |                             |
| `account`           | string \| null |                             |
| `window_label`      | string         |                             |
| `kind`              | string         | always `burn` when non-null |
| `urgency`           | string         |                             |
| `remaining_percent` | number         |                             |
| `days_until_reset`  | number \| null |                             |
| `score`             | number         | analysis score              |
| `reason`            | string         | human message               |
| `source`            | string         |                             |
| `plan`              | string \| null |                             |

### `history` (top-level on full `--json`)

Snapshot learning insights (additive). Empty-ish when learning is off or thin.

| Field                          | Type   | Notes                                                        |
| ------------------------------ | ------ | ------------------------------------------------------------ |
| `snapshot_count`               | int    | Retained files under cache                                   |
| `learning_active`              | bool   | Whether history influences scoring this run                  |
| `retention_days`               | int    | From config                                                  |
| `learned_burn_rates`           | object | Map `provider:duration` → `{fraction_per_day, sample_count}` |
| `chronic_underuse`             | array  | Short windows with high avg remaining across ≥2 cycles       |
| `usually_left_late_cycle`      | array  | Avg remaining when observed ≥70% into a window               |
| `burn_candidates_from_history` | array  | Subset of late-cycle leftovers (≥40% left avg) as burn hints |

Every `provider` in this section — including the `provider:duration` keys of
`learned_burn_rates` — is the **canonical** provider id used everywhere else in
the payload (`antigravity`, `opencode-go`), never the `[plans]` config key
(`gemini`, `opencode`). Sorting or joining history rows against `accounts[]`
rows by provider is therefore safe.

`chronic_underuse` entries carry:

| Field               | Type        | Notes                                                                  |
| ------------------- | ----------- | ---------------------------------------------------------------------- |
| `provider`          | string      | Canonical provider id                                                  |
| `account`           | string∣null | Account of the matching live window, when that series is still present |
| `label`             | string      | Window label, preferring the live row's spelling                       |
| `window_key`        | string      | `provider:pool:duration` — stable across collectors and relabelling    |
| `avg_remaining_pct` | number      | Mean remaining across the sampled reset cycles                         |
| `sample_count`      | int         | Distinct reset cycles sampled                                          |

`window_key` identifies one recurring allotment independently of which
collector observed it: collectors label the same window differently (CodexBar
`Gemini 5-hour` vs OpenUsage `Antigravity Gemini 5-hour`), so it is the field to
group or deduplicate on, not `label`. It is **not** unique on its own — a
provider with two subscriptions yields one row per account under the same
`window_key`. The row identity is the `(window_key, account)` pair.

## Exit codes (collect runs)

- **0** — Data collected (or deliberately skipped), producing valid output.
- **1** — Hard failure. The tool could not run or all configured collectors failed. No usable data.
- **2** — (Only in human-readable/TTY mode) Success, but there is at least one active use-or-lose alert. In `--json` mode, this returns 0 since the JSON itself is usable.

Cross-check disagreements alone do **not** change the exit code.

## `snapshot` object

| Field              | Type              | Notes                              |
| ------------------ | ----------------- | ---------------------------------- |
| `collected_at`     | string (ISO-8601) | UTC collection time                |
| `accounts`         | array             | Selected live rows (see below)     |
| `cross_checks`     | array             | Informational tool comparisons     |
| `collector_errors` | string[]          | Per-source failures (`"cswap: …"`) |

### `accounts[]` (`AccountUsage`)

| Field               | Type              | Stable?                                                                                 |
| ------------------- | ----------------- | --------------------------------------------------------------------------------------- |
| `source`            | string            | yes — `cswap` \| `codexbar` \| `caut` \| `openusage_ai` \| `openusage_sh` \| `tokscale` |
| `provider`          | string            | yes — collector id (e.g. `claude`, `codex`, `antigravity`)                              |
| `account`           | string \| null    | email or label when known                                                               |
| `plan`              | string \| null    | plan name if reported                                                                   |
| `billing_kind`      | string            | `subscription_window` \| `prepaid_balance` \| `payg_api` \| `unknown`                   |
| `windows`           | array             | quota windows                                                                           |
| `balance_usd`       | number \| null    | prepaid balance                                                                         |
| `credits_remaining` | number \| null    | legacy credits field                                                                    |
| `usage_credits`     | object \| omitted | extra/pay-as-you-go wallet when present                                                 |
| `error`             | string \| null    | row-level error                                                                         |
| `notes`             | string[]          | human notes (age, hydrate, etc.)                                                        |

`raw` is **not** included in JSON (internal only).

### `windows[]` (`QuotaWindow`)

| Field                  | Type                 |
| ---------------------- | -------------------- |
| `label`                | string               |
| `used_percent`         | number \| null       |
| `remaining_percent`    | number \| null       |
| `resets_at`            | string (ISO) \| null |
| `window_minutes`       | int \| null          |
| `reset_description`    | string \| null       |
| `refill_capacity`      | number \| null       |
| `refill_capacity_unit` | string \| null       |
| `internal_throttle`    | bool                 |

### `usage_credits` (optional)

| Field          | Type                     |
| -------------- | ------------------------ |
| `used`         | number \| null           |
| `limit`        | number \| null           |
| `remaining`    | number \| null           |
| `currency`     | string                   |
| `used_percent` | number \| null           |
| `resets_at`    | string (ISO) \| optional |

### `cross_checks[]`

| Field      | Type                                       |
| ---------- | ------------------------------------------ |
| `provider` | string                                     |
| `account`  | string \| null                             |
| `status`   | `consistent` \| `warning` \| `unavailable` |
| `sources`  | string[]                                   |
| `message`  | string                                     |

## `alerts[]` (`UseOrLoseAlert`)

| Field                   | Type              | Notes                                                         |
| ----------------------- | ----------------- | ------------------------------------------------------------- |
| `urgency`               | string            | `critical` \| `high` \| `medium` \| `low` \| `info` \| `none` |
| `provider`              | string            |                                                               |
| `account`               | string \| null    |                                                               |
| `window_label`          | string            |                                                               |
| `remaining_percent`     | number            | share of the window still available                           |
| `used_percent`          | number \| null    | share already consumed; null when the source reports neither  |
| `days_until_reset`      | number \| null    |                                                               |
| `deadline_is_estimated` | bool              | true when the source supplied a reset date without a time     |
| `plan`                  | string \| null    |                                                               |
| `message`               | string            | human sentence                                                |
| `source`                | string            | data source for the window                                    |
| `score`                 | number            | sort priority (higher = more important)                       |
| `window_minutes`        | int \| null       |                                                               |
| `kind`                  | string            | `burn` \| `conserve` \| `prepaid` (non-expiring API balance)  |
| `consumption_analysis`  | object \| omitted | flexibility profile when present                              |
| `pace`                  | object \| omitted | pace profile when present                                     |

### `consumption_analysis` (optional)

| Field                     | Type           |
| ------------------------- | -------------- |
| `flexibility_class`       | string         |
| `consumption_flexibility` | number         |
| `value_at_risk_usd`       | number \| null |
| `cycles_needed`           | int \| null    |
| `earliest_start_calendar` | string \| null |
| `effective_burn_minutes`  | number \| null |
| `burn_estimate`           | string \| null |

### `pace` (optional)

| Field                      | Type                        |
| -------------------------- | --------------------------- |
| `elapsed_fraction`         | number \| null              |
| `used_fraction`            | number                      |
| `pace_ratio`               | number \| null              |
| `projected_used_fraction`  | number \| null              |
| `projected_waste_fraction` | number \| null              |
| `projected_waste_usd`      | number \| null              |
| `projected_exhaust_at`     | string \| null              |
| `governing`                | bool                        |
| `gated_by`                 | string \| null              |
| `confidence`               | string                      |
| `learned_sample_count`     | int (0 if no history blend) |
| `has_overage`              | bool                        |

`has_overage` is `true` when the account has a real (`AccountUsage.usage_credits`) or config-confirmed (`provider_overrides.<provider>.overage_state: "enabled"`) overage/extra-usage wallet. It qualifies, never suppresses, a `conserve`/`burn` verdict — `true` means the real risk is unplanned $ spend (soft ceiling), not lockout (hard ceiling). See `docs/shared-quota-semantics/formulas/pace.md`'s rule O1.

## Stability policy

- **Additive fields** may appear without a major version bump (new optional keys).
- **Renames / removals** of listed stable keys require a major version bump and README note.
- Message strings and pretty report layout are **not** a contract — use structured fields.
- Provider id strings may gain new values as collectors expand; treat unknown providers as pass-through.

## Scripting examples

```bash
# Fail cron only on hard errors; treat alerts as notify-worthy
ai -q --json > /tmp/ai.json
code=$?
if [ "$code" -eq 1 ]; then exit 1; fi
if [ "$code" -eq 2 ]; then
  jq -r '.alerts[] | "[\(.urgency)] \(.message)"' /tmp/ai.json
fi
```

```bash
# Actionable alerts only (still full alert objects)
ai -q --json --alerts-only | jq '.alerts | map(select(.kind == "burn" or .kind == "conserve"))'
```
