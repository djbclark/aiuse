# JSON contract (`aiuse --json`)

Stable machine-readable fields for scripts and cron. Prefer these keys over
pretty text parsing.

`aiuse watch` does **not** emit this contract. It is an alternate-screen UI
(exit 0 on quit). Scripts that want JSON should use `aiuse --json` or
`aiuse serve`.

**Related:** exit codes in [README](../README.md#exit-codes); collector timing in
[collector-concurrency.md](collector-concurrency.md); scheduled runs in
[scheduling.md](scheduling.md).

## How to read this (start here — three real failure modes, worked)

These three misreads all happened on 2026-10-03. The 1.1 fields exist so the
next agent cannot repeat them without ignoring data that is right in front of
it. Or skip all of it and call `aiuse --available`.

1. **`used_percent` is the share CONSUMED.** An agent read
   `Codex 5-hour quota: used_percent 100` as "100% free" and dispatched work to
   codex, which failed with a usage-limit error. Decide from
   `remaining_percent` (or its alias `headroom_percent`), or better from the
   explicit `state` — here `state: "exhausted"`, `usable_now: false`,
   `available_at` set to the reset time. Every window in 1.1 carries `state`:
   `exhausted` (≤1% left) | `tight` (<15% left) | `ok` | `unknown`.

2. **One vendor can hold several independent pools by model family.** agy
   (provider `antigravity`) has a Gemini pool and a separate Claude/GPT pool.
   The Claude/GPT 5-hour window read 0% used at probe time and was exhausted
   ~35 minutes later (one Opus-high review drained it), while Gemini still had
   ~75% left. The agent concluded "agy is exhausted" and moved to another
   vendor. In 1.1 each split-vendor window carries `pool_family`
   (`antigravity`: `gemini` vs `claude_gpt`;
   `cursor`: `auto` / `included` / `other` / `grok_bot`) plus a `models_hint`
   naming which models draw it. One family `exhausted` does NOT make the
   vendor unusable — retry on the other family before abandoning it.
   Claude is **not** an independent split: `default` represents the shared
   quota, while `fable` is a model cap within that quota. Fable can consume up
   to 50% of the shared weekly budget, not an extra 50%. At overall 85% used
   and Fable 100% used, other models still have the shared 15% left; Fable
   cannot use it. An exhausted shared 5-hour or weekly window blocks Fable
   even when its own cap has headroom.

3. **The fullest window of an account binds.** codex at 5-hour 100% used /
   weekly 35% used is unusable for ~5 hours, but that requires reasoning
   across windows. In 1.1 the account carries `usable_now` (false if any
   applicable shared window is exhausted), `binding_window` (label of the window with the least
   remaining), and `available_at` (earliest `resets_at` among exhausted
   windows). `summary_lines` renders the whole account as one sentence with
   used AND left for every window.

Everything below is also stated inside the output itself: `--json` carries a
`semantics` object mapping each of these field names to its meaning, so a
consumer that never read this document can learn the conventions from the JSON
alone. When data is cached rather than live, `age_seconds` (computed at read
time) and `fresh` (age ≤ `analysis.fresh_threshold_seconds`, default 1500s)
say how old it is — a stale `ok` can hide a fresh exhaustion, so re-collect
(`--live`) before trusting it for routing.

## What's new in schema 1.1 (additive; nothing renamed or removed)

- Every window: `state`, `headroom_percent`, and on split vendors
  `pool_family` + `models_hint`.
- Claude windows also carry `quota_scope` (`shared` or `model_sublimit`).
  Model caps carry `shared_pool_family: "default"`; Fable additionally carries
  `max_share_of_parent_percent: 50`. Their raw percentages are relative to
  their own caps, never percentages of an additional pool.
  Claude account `usable_now`, `binding_window` and `available_at` describe
  the shared quota, not an individual model restriction.
  Model routing entries include both shared windows and their cap, so
  `usable_now` requires shared headroom as well as model headroom.
  Fable routing `headroom_percent` scales its cap's remaining percent by
  0.5 before taking the minimum with shared-window headroom
  (`headroom_basis: "shared_weekly"`). For example, overall 15% left and
  Fable 20% of its cap left gives at most 10% of the shared weekly budget
  usable by Fable. Shared session limits can constrain it further.
- Every account: `usable_now`, `binding_window`, `binding_headroom_percent`,
  `available_at`, `age_seconds`, and `collected_at` when the source itself
  reports a measurement time (CodexBar rows do, via `usage.updatedAt`).
- Top level: `summary_lines` (one line per pool, used AND left always),
  `semantics` (field-name → meaning), `agent_notes` (active agent-reported
  exhaustion overrides), `age_seconds`, `fresh`.
- On-disk snapshots additionally mirror the live envelope under a top-level
  `snapshot` key (see below), so `.snapshot.accounts[]` works on both the
  cache file and `aiuse --json`.
- New commands: `aiuse --available [--json] [--live]` and
  `aiuse note-exhausted <provider> [--family F] --resets-in 4h53m|--resets-at
ISO [--reason TEXT]`.

## Top-level payload

Default `aiuse --json` stdout:

```json
{
  "schema_version": "1.1",
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
  "history": { ... },

  // New in schema 1.1:
  "age_seconds": 0.0,
  "fresh": true,
  "summary_lines": [ "codex: Codex 5-hour quota (1): EXHAUSTED (100% used / 0% left, resets 15:42); … -> NOT usable now" ],
  "semantics": { "used_percent": "share CONSUMED; 100 means exhausted, 0 means untouched", "...": "..." },
  "agent_notes": [ { "provider": "codex", "pool_family": null, "resets_at": "...", "reason": "429", "source": "agent-reported" } ]
}
```

Live `--json` is by definition fresh (`age_seconds: 0`); the same keys appear
with real values in `aiuse --available` output and in `aiuse serve`'s
`/v1/snapshot` response when it serves a cached read.

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

Files in `~/.cache/aiuse/snapshots/*.json` are flat at the top level (that is
the shape `--flatten` mirrors), and since **schema 1.1** they ALSO carry a
top-level `snapshot` key mirroring the live envelope (`snapshot.accounts[]`,
`snapshot.collected_at`, …), so one consumer code path —
`.snapshot.accounts[]` — works on both the cache file and `aiuse --json`.
The flat `.accounts[]` path keeps working unchanged (the `aiuse-pools`
operator script reads it). Enriched fields (`state`, `usable_now`,
`summary_lines`, `semantics`, …) are present in both spellings.

`aiuse --json --alerts-only`:

```json
{
  "schema_version": "1.1",
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
- **3** — (`--available` only) The run succeeded but zero pools are `usable_now` — everything measured is exhausted or unknown. Distinct from 1: the data is fine, the quota is not.

Cross-check disagreements alone do **not** change the exit code.

## `aiuse --available [--json] [--live]` — the routing shortlist

The command an orchestrator should call instead of parsing windows. Prints
only pools with `usable_now: true`, one entry per `(account, pool_family)`,
sorted by headroom (most left first), each with `models_hint` and the local
`cli_binary` that spends the quota:

```json
{
  "schema_version": "1.1",
  "generated_at": "2026-10-03T23:05:00+00:00",
  "source": "cache",
  "age_seconds": 412.0,
  "fresh": true,
  "available": [
    {
      "provider": "antigravity",
      "account": null,
      "pool_family": "gemini",
      "cli_binary": "agy",
      "usable_now": true,
      "binding_window": "Gemini weekly",
      "headroom_percent": 70.6,
      "available_at": null,
      "models_hint": "agy models — gemini-* models draw this pool",
      "age_seconds": 412.0,
      "windows": [ ... ]
    }
  ],
  "semantics": { ... }
}
```

Reads the snapshot cache (`latest.json`, kept fresh by `aiuse watch` and the
hourly LaunchAgent) by default — a fast answer in well under a second.
`--live` forces a fresh collect (27–60 s). Exit 3 when nothing is usable.

Pools the operator has ruled out in `analysis.excluded_pools` (`"provider"` or
`"provider/pool_family"` → reason) are dropped from `available` and listed in a
top-level `excluded` array — `provider`, `account`, `pool_family`,
`cli_binary`, `headroom_percent`, `reason` — so a reader can tell "ruled out"
from "exhausted". Text mode prints one `excluded by operator: …` line per pool
on stderr. The exit code counts only `available`.

Providers in top-level `[disabled_services]` (the stronger, overall disable)
are also never listed: their rows are filtered at collection, and the view
additionally drops them from any cache that still carries them — so they can
appear in `excluded` with the disabled reason too. The payload carries a
top-level `disabled_services` object (`provider` → reason, `{}` when none)
mirroring the snapshot field, plus its `semantics` entry.

## `aiuse note-exhausted` — agent-reported exhaustion overrides

```
aiuse note-exhausted antigravity --family claude_gpt --resets-in 4h53m --reason "RESOURCE_EXHAUSTED"
aiuse note-exhausted codex --resets-at 2026-10-03T23:42:00+00:00
```

A 429 seen by one agent should be visible to the next agent without waiting
for a collector pass. The note is a small JSON file under
`~/.cache/aiuse/agent-notes/` that expires at its own reset time. While
active it flips matching windows to `state: "exhausted"` with
`state_source: "agent-reported"`, forces `usable_now: false`, and appears in
the top-level `agent_notes` list — so `--json` and `--available` honour it and
label it. Advisory by design: the note never edits numbers, and once it
expires a live collector reading `ok` wins. Match scope is provider-wide, or
provider + `--family` for split vendors.

## `aiuse attribute --json` — quota burned beside tokens spent

Schema `1.0`, separate from the collect envelope. Guide:
[`attribution.md`](attribution.md).

| Field                     | Meaning                                                                                                                                                           |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `since`, `until`          | The range, ISO 8601.                                                                                                                                              |
| `coverage`                | `quota_samples`, `burst_samples`, `ledger_samples`, `ledger_span` (`[start, end]` of the session-file ledger, or `null`), `litellm` (`on`, `off`, or `error: …`). |
| `providers[].windows[]`   | `label`, `account`, `start_used`, `end_used`, `burned_points` (can exceed 100 across resets), `resets`, `samples`.                                                |
| `providers[].tokscale[]`  | Per `client` and `model`: `input`, `output`, `cache_read`, `cache_write`, `reasoning`, `messages`, `cost`, `share` (0–1).                                         |
| `providers[].litellm[]`   | Per `client` (virtual-key alias) and `model`: `requests`, `failures`, `input`, `output`, `reasoning`, `share`.                                                    |
| `providers[].per_point`   | Tokens per burned point of the longest window that moved, per ledger, over the span that ledger covers. Absent when nothing qualifies.                            |
| `providers[].intervals[]` | `start`, `end`, `burned` (label → points), and per-client `tokscale` / `litellm` totals in that interval.                                                         |
| `unmapped`                | Token rows with no quota provider mapped.                                                                                                                         |

Burned points and tokens are different units; the report never converts one
into the other.

## `snapshot` object

| Field               | Type              | Notes                                                                                                                                                                                      |
| ------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `collected_at`      | string (ISO-8601) | UTC collection time                                                                                                                                                                        |
| `accounts`          | array             | Selected live rows (see below)                                                                                                                                                             |
| `cross_checks`      | array             | Informational tool comparisons                                                                                                                                                             |
| `collector_errors`  | string[]          | Per-source failures (`"cswap: …"`)                                                                                                                                                         |
| `disabled_services` | object            | provider → reason from top-level `[disabled_services]` (1.1; `{}` = none). Rows for these providers are not collected — do not route to or spend them until the operator removes the entry |

### `accounts[]` (`AccountUsage`)

| Field                      | Type              | Stable?                                                                                                                                                                                                                   |
| -------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source`                   | string            | yes — `cswap` \| `codexbar` \| `caut` \| `openusage_ai` \| `openusage_sh` \| `tokscale` \| `clinepass` \| `hermes` \| `openrouter` \| `muse` \| `qwencloud` \| `bailian` \| `grok_billing`; grows as collectors are added |
| `provider`                 | string            | yes — collector id (e.g. `claude`, `codex`, `antigravity`)                                                                                                                                                                |
| `cli_binary`               | string \| null    | local CLI binary that spends this quota; `null` = API-only / cloud-run (see below)                                                                                                                                        |
| `account`                  | string \| null    | email or label when known                                                                                                                                                                                                 |
| `plan`                     | string \| null    | plan name if reported                                                                                                                                                                                                     |
| `billing_kind`             | string            | `subscription_window` \| `prepaid_balance` \| `payg_api` \| `unknown`                                                                                                                                                     |
| `windows`                  | array             | quota windows                                                                                                                                                                                                             |
| `balance_usd`              | number \| null    | prepaid balance                                                                                                                                                                                                           |
| `credits_remaining`        | number \| null    | legacy credits field                                                                                                                                                                                                      |
| `usage_credits`            | object \| omitted | extra/pay-as-you-go wallet when present                                                                                                                                                                                   |
| `error`                    | string \| null    | row-level error                                                                                                                                                                                                           |
| `notes`                    | string[]          | human notes (age, hydrate, etc.)                                                                                                                                                                                          |
| `provider_id`              | string            | canonical provider id used for matching/history (equals `provider` unless the row carries an alias spelling)                                                                                                              |
| `service_id`               | string \| null    | reserved per-service split within a provider; always `null` today                                                                                                                                                         |
| `collector_id`             | string            | collector that produced the row (equals `source` unless a collector overrides it)                                                                                                                                         |
| `collected_at`             | string \| omitted | 1.1 — the source's own measurement time for this row (CodexBar `usage.updatedAt`); older than the snapshot's `collected_at` means the row was a cached read. Omitted when the source reports none                         |
| `usable_now`               | bool              | 1.1 — false if any applicable window is exhausted or no window has data. For Claude, only shared limits affect account usability; model caps affect their routing family in `--available`                                 |
| `binding_window`           | string \| null    | 1.1 — label of the applicable window with the least remaining; Claude model caps do not bind the shared account                                                                                                           |
| `binding_headroom_percent` | number \| null    | 1.1 — that window's remaining share                                                                                                                                                                                       |
| `available_at`             | string \| null    | 1.1 — earliest `resets_at` among applicable exhausted windows; Claude model caps are excluded from the shared account. Null when nothing applicable is exhausted or no reset is known                                     |
| `age_seconds`              | number \| null    | 1.1 — seconds since this account's `collected_at`, computed at read time                                                                                                                                                  |

`raw` is **not** included in JSON (internal only).

`cli_binary` names the local CLI that _spends_ the provider's quota — the
coding TUI you launch to burn the allotment (`agy`, `zcode`, `cursor-agent`,
`opencode`, `qwen`, …) — resolved through provider aliases (`gemini` → `agy`).
It is deliberately **not** the quota-measurement tool a collector shells out to
(`qwencloud`, `bl`, `caut`, `tokscale`, `openusage`, `cswap`, `codexbar`
report quota but do not consume it). Providers with no local CLI — API-only or
cloud-run, e.g. `openrouter`, `deepseek` — emit `null`. Added in **3.0.40**;
additive per the stability policy below.

### `windows[]` (`QuotaWindow`)

| Field                         | Type                 |
| ----------------------------- | -------------------- |
| `label`                       | string               |
| `used_percent`                | number \| null       |
| `remaining_percent`           | number \| null       |
| `resets_at`                   | string (ISO) \| null |
| `window_minutes`              | int \| null          |
| `reset_description`           | string \| null       |
| `refill_capacity`             | number \| null       |
| `refill_capacity_unit`        | string \| null       |
| `internal_throttle`           | bool                 |
| `state`                       | string               | 1.1 — `exhausted` (≤1% left) \| `tight` (<15% left) \| `ok` \| `unknown` (no data); computed so you never have to                                                                     |
| `headroom_percent`            | number \| null       | 1.1 — alias of `remaining_percent`, spelled for humans                                                                                                                                |
| `pool_family`                 | string \| omitted    | 1.1 — routing family: `antigravity` → independent `gemini`/`claude_gpt`; `claude` → shared `default` with model caps such as `fable`; `cursor` → `auto`/`included`/`other`/`grok_bot` |
| `quota_scope`                 | string \| omitted    | Claude: `shared` or `model_sublimit`; a model cap is within shared quota, not additional capacity                                                                                     |
| `shared_pool_family`          | string \| omitted    | Claude model caps: `default`, the shared parent whose windows also constrain model availability                                                                                       |
| `max_share_of_parent_percent` | number \| omitted    | Fable: `50`, its maximum share of the shared weekly budget                                                                                                                            |
| `models_hint`                 | string \| omitted    | 1.1 — which models draw this pool, when cheap to say (e.g. `agy models — gemini-* models draw this pool`)                                                                             |
| `state_source`                | string \| omitted    | 1.1 — `agent-reported` when an active `note-exhausted` override flipped this window                                                                                                   |
| `agent_reported`              | object \| omitted    | 1.1 — `{resets_at, reason}` from the active override, when present                                                                                                                    |

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

```bash
# The one call an orchestrator needs: what can I use right now?
aiuse --available --json | jq -r '.available[] | "\(.provider)/\(.pool_family // "default") via \(.cli_binary): \(.headroom_percent)% left"'
```

```bash
# Report an exhaustion you just hit, so the next agent sees it immediately
aiuse note-exhausted antigravity --family claude_gpt --resets-in 4h53m --reason "RESOURCE_EXHAUSTED"
```
