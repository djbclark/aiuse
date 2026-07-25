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
  "snapshot": { ... },
  "alerts": [ ... ],
  "suggestion": { ... } | null
}
```

`aiuse --json --alerts-only`:

```json
{
  "alerts": [ ... ],
  "cross_check_warnings": [ ... ],
  "suggestion": { ... } | null
}
```

(`cross_check_warnings` is only the subset of `snapshot.cross_checks` with
`status == "warning"`.)

### `suggestion` (optional top-level)

Single best **burn** window to use next, or `null` when there is nothing
urgent. Prefer this over re-ranking `alerts[]` in scripts. Also available via
`aiuse suggest` (human one-liner) / `aiuse suggest --json`.

| Field                | Type           | Notes                                      |
| -------------------- | -------------- | ------------------------------------------ |
| `provider`           | string         |                                            |
| `account`            | string \| null |                                            |
| `window_label`       | string         |                                            |
| `kind`               | string         | always `burn` when non-null                |
| `urgency`            | string         |                                            |
| `remaining_percent`  | number         |                                            |
| `days_until_reset`   | number \| null |                                            |
| `score`              | number         | analysis score                             |
| `reason`             | string         | human message                              |
| `source`             | string         |                                            |
| `plan`               | string \| null |                                            |

## Exit codes (collect runs)

| Code  | Meaning                                              |
| ----- | ---------------------------------------------------- |
| **0** | Success; no burn/conserve alerts (INFO-only still 0) |
| **1** | Hard failure: collector errors and **no** accounts   |
| **2** | Success with at least one burn/conserve alert        |

Cross-check notes alone never set exit code 2.

## `snapshot` object

| Field              | Type              | Notes                              |
| ------------------ | ----------------- | ---------------------------------- |
| `collected_at`     | string (ISO-8601) | UTC collection time                |
| `accounts`         | array             | Selected live rows (see below)     |
| `cross_checks`     | array             | Informational tool comparisons     |
| `collector_errors` | string[]          | Per-source failures (`"cswap: …"`) |

### `accounts[]` (`AccountUsage`)

| Field               | Type              | Stable?                                                               |
| ------------------- | ----------------- | --------------------------------------------------------------------- |
| `source`            | string            | yes — `cswap` \| `codexbar` \| `caut` \| `openusage` \| `tokscale`   |
| `provider`          | string            | yes — collector id (e.g. `claude`, `codex`, `antigravity`)            |
| `account`           | string \| null    | email or label when known                                             |
| `plan`              | string \| null    | plan name if reported                                                 |
| `billing_kind`      | string            | `subscription_window` \| `prepaid_balance` \| `payg_api` \| `unknown` |
| `windows`           | array             | quota windows                                                         |
| `balance_usd`       | number \| null    | prepaid balance                                                       |
| `credits_remaining` | number \| null    | legacy credits field                                                  |
| `usage_credits`     | object \| omitted | extra/pay-as-you-go wallet when present                               |
| `error`             | string \| null    | row-level error                                                       |
| `notes`             | string[]          | human notes (age, hydrate, etc.)                                      |

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

| Field                  | Type              | Notes                                                         |
| ---------------------- | ----------------- | ------------------------------------------------------------- |
| `urgency`              | string            | `critical` \| `high` \| `medium` \| `low` \| `info` \| `none` |
| `provider`             | string            |                                                               |
| `account`              | string \| null    |                                                               |
| `window_label`         | string            |                                                               |
| `remaining_percent`    | number            |                                                               |
| `days_until_reset`     | number \| null    |                                                               |
| `plan`                 | string \| null    |                                                               |
| `message`              | string            | human sentence                                                |
| `source`               | string            | data source for the window                                    |
| `score`                | number            | sort priority (higher = more important)                       |
| `window_minutes`       | int \| null       |                                                               |
| `kind`                 | string            | `burn` \| `conserve` \| `prepaid` (non-expiring API balance)  |
| `consumption_analysis` | object \| omitted | flexibility profile when present                              |
| `pace`                 | object \| omitted | pace profile when present                                     |

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

| Field                      | Type           |
| -------------------------- | -------------- |
| `elapsed_fraction`         | number \| null |
| `used_fraction`            | number         |
| `pace_ratio`               | number \| null |
| `projected_used_fraction`  | number \| null |
| `projected_waste_fraction` | number \| null |
| `projected_waste_usd`      | number \| null |
| `projected_exhaust_at`     | string \| null |
| `governing`                | bool           |
| `gated_by`                 | string \| null |
| `confidence`               | string         |
| `learned_sample_count`     | int (0 if no history blend) |

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
