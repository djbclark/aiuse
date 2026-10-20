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
   `cursor`: `total` / `auto` / `third_party` / `on_demand` / `grok_bot`) plus a `models_hint`
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

## What's new in schema 1.3 (2026-10-14)

Schema 1.3 adds ceiling/breakdown semantics, operator `[hidden_pools]`, and
per-window learned burn rates:

- **Top-level `hidden_pools`**: `{provider[/pool_family]: reason}` applied at
  collection; omitted from human outputs and `--available` by default.
- **Window fields**: `role` (`ceiling` | `breakdown`), `effective_left_percent`,
  `ceiling_binding`, `ceiling_headroom_percent`, `ceiling_window_id`.
- **Cursor `pool_family` values**: `total`, `auto`, `third_party`, `on_demand`,
  `grok_bot` (legacy config keys such as `cursor/cursor_grok` still match via
  alias normalization).
- **`overall_pace_percent`**: excludes breakdown rows (e.g. Cursor auto /
  third-party); only headline windows drive account pace and exit codes.
- **`learned_burn_rates` keys**: `provider:pool:duration` (per window series).

## What's new in schema 1.2 (2026-10-12)

Schema 1.2 adds per-window pace and per-account overall pace across all JSON
surfaces (`aiuse --json`, on-disk cached snapshots, `aiuse serve`, and
`aiuse --available`):

- **Every window (`windows[]`)**:
  - `pace_percent` (`integer | null`): `round((pace_ratio - 1) * 100)`. Signed
    percentage indicating whether usage is ahead (`+`) or behind (`-`) an even
    spend across the elapsed window duration. Floored at 5% elapsed. Null when
    the window is exhausted (≤1% remaining) or unclocked (no `resets_at` or no duration).
  - `pace_ratio` (`number | null`): unblended `used_fraction / elapsed_fraction`
    measured at snapshot collection time (minimum elapsed fraction 0.05). Null
    when the window is exhausted or unclocked.
  - `pace_early` (`bool`): `true` when window elapsed fraction is below 0.10
    (early in the window, pace is noisy); `false` when pace is null or not early.
- **Every account (`accounts[]`)**:
  - `overall_pace_percent` (`integer | null`): highest non-null `pace_percent`
    among the account's windows (the window furthest ahead of spend that will run
    out first). Null when no window in the account has a pace reading, or when
    **any** window in the account is exhausted (since the account is locked out
    until that reset).
  - `overall_pace_early` (`bool`): `true` when the window that supplied
    `overall_pace_percent` is early (<10% elapsed); `false` when
    `overall_pace_percent` is null or not early.
- **Shortlist and summary lines**:
  - `aiuse --available` includes pace in window summaries (e.g. `, pace +37%` or `, pace +1351%~`).
  - Top-level `summary_lines` format includes `, pace +N%` / `, pace -N%` (with `~` when early).
- **Top-level `semantics`**:
  - Contains explicit definitions for `pace_percent`, `pace_ratio`, `pace_early`,
    `overall_pace_percent`, and `overall_pace_early`.

### Changelog

#### Additive (2026-10-15, schema stays 1.3)

- `accounts[].expires_at` (ISO string | omitted): when an expiring dollar grant (`omp`)
  lapses; also on its `--available` entry.
- `accounts[].source` / `observed_at` (omp): the reading carried, `codexbar` or
  `operator-override` (`aiuse set-credits`), and its time; `superseded_reading`
  (`{source, observed_at, balance_usd}`) when an override replaced a CodexBar reading;
  `reading_expired: true` when a CodexBar reading older than 24 h has no override, so the
  balance is dropped and the pool is `unknown`. An override lasts 7 days and ends at
  `expires_at`; malformed ones are ignored.
- `available[].burn_before_reset` (object | omitted): `{expires_at, days_left,
remaining_percent, remaining_usd, suggestion}` when an expiring grant lapses within 21
  days with more left than a bar that slides from 50% (10+ days out) toward 0.

- `accounts[].spent_usd` (number | omitted): dollars already spent from an
  expiring dollar grant, today only the `omp` provider (Claude cloud-session
  credits). Omitted when not reported. See
  [`omp` — an expiring dollar grant](#omp--an-expiring-dollar-grant).
- `omp` windows get `pool_family: "claude"` and a `models_hint`; its
  `--available` routing entry carries `spent_usd` and `balance_usd`; its
  summary line states spent / left / grant size and says `expires` instead of
  `resets`. New `semantics.spent_usd`.
- **Operator gate.** `accounts[].requires_operator_approval` (bool | omitted)
  and `accounts[].ai_notice` (string | omitted), present only when true —
  today only on `omp`. The same pair is on that account's `--available` routing
  entries and on any `alerts[]` raised for it (whose `message` then ends with
  `— AI: Do not use without asking operator`); its summary line ends the same
  way. New `semantics.requires_operator_approval` and `semantics.ai_notice`.
  **Agents must not spend, route work to, or recommend burning a gated pool
  without asking the operator first.** See
  [`omp` — an expiring dollar grant](#omp--an-expiring-dollar-grant).

#### Additive (2026-10-14, schema stays 1.3)

- `pace.exhaustion_lead_seconds` (number | null): lead time between projected
  exhaustion and the window reset. New optional key per the stability policy; no
  version bump.

#### Schema 1.3 (2026-10-14)

- Bump `schema_version` to **1.3** (additive).
- Top-level **`hidden_pools`**: operator display-only hides applied at collection.
- Window fields: **`role`** (`ceiling` | `breakdown`), **`effective_left_percent`**,
  **`ceiling_binding`**, **`ceiling_headroom_percent`**, **`ceiling_window_id`**.
- Cursor **`pool_family` values**: `total`, `auto`, `third_party`, `on_demand`,
  `grok_bot` (legacy config keys such as `cursor/cursor_grok` still match).
- **`overall_pace_percent` meaning**: excludes breakdown rows (e.g. Cursor auto /
  third-party); only headline windows drive account pace and exit codes.
- **`learned_burn_rates` keys**: `provider:pool:duration` (per window series).

#### 2026-10-14: `suggestion` skips a burn the board's pool collapse hides (schema stays 1.2)

Same shape, tightened meaning. The board collapses a pool onto its most attention-grabbing
alert, so a burn next to a stronger sibling in the same pool (a Codex 5h burn behind its
weekly conserve, say) renders as the pool's `slow` row. `suggestion` is now the
highest-ranked burn the board actually shows, so it is `null` where it previously named that
hidden burn. Replaying 3300 cached snapshots, 24 (0.73%) change, all to `null`; no suggestion
is pool-masked after the change (`aiuse-57n`).

#### 2026-10-13: `suggestion` follows board order (schema stays 1.2)

A meaning change, not a shape change: no field was added, renamed or removed, so under the
[stability policy](#stability-policy) the schema version does not move. `suggestion` used to be the
burn alert with the highest analyzer `score`; it is now the burn alert the `aiuse` board ranks
highest (see [`suggestion`](#suggestion-optional-top-level)). A consumer that treats
`suggestion.score` as the reason the window won should read `alerts[].pace.projected_waste_usd`
instead.

#### Schema 1.2 (2026-10-12)

- Added `pace_percent` (`int | null`), `pace_ratio` (`float | null`), and `pace_early` (`bool`) to `windows[]` (`QuotaWindow`).
- Added `overall_pace_percent` (`int | null`) and `overall_pace_early` (`bool`) to `accounts[]` (`AccountUsage`).
- Added `pace_percent`, `pace_ratio`, `pace_early`, `overall_pace_percent`, and `overall_pace_early` to the top-level `semantics` object.
- Included window pace (marked with `~` when early) in `summary_lines` and `aiuse --available` human summaries and JSON outputs.

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
- `config_fingerprint` (string, omitted when absent) identifies the disable
  list, collector switches, and `[usage_sources]` pins that produced the
  snapshot. Schema stays 1.1. `aiuse watch` and `aiuse serve` will not reuse
  a snapshot whose fingerprint differs.
- New commands: `aiuse --available [--json] [--live]` and
  `aiuse note-exhausted <provider> [--family F] --resets-in 4h53m|--resets-at
ISO [--reason TEXT]`.

## Top-level payload

Default `aiuse --json` stdout:

```json
{
  "schema_version": "1.3",
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
  "summary_lines": [ "codex: Codex 5-hour quota (1): EXHAUSTED (100% used / 0% left, resets 3:42pm); … -> NOT usable now" ],
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
  "schema_version": "1.3",
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

**Which window wins** (changed 2026-10-13; the shape below is unchanged). The winner is the burn
alert the board ranks highest: the table's bottom row among those a burn alert backs, not the alert
with the highest `score`. That is the highest band first (`use`, then `mid`), then the most
dollars projected to expire unspent (`pace.projected_waste_fraction` × the value of one full refill
of that window, priced per account like `value_at_risk_usd`; an alert with no pace ranks by the
dollars it still holds), then the analyzer `score`, then the remaining percent, then the sooner
reset. Eligibility is unchanged: only `burn` alerts with urgency above `info` qualify, so
conserve, info and prepaid alerts never win. A burn a stronger alert in its own pool hides —
the board's pool collapse (2026-10-14) — is skipped as well, so `suggestion` can be `null`
even while a `burn` alert exists. `score` stays the analysis score; it no longer picks
the winner.

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

| Field                          | Type   | Notes                                                             |
| ------------------------------ | ------ | ----------------------------------------------------------------- |
| `snapshot_count`               | int    | Retained files under cache                                        |
| `learning_active`              | bool   | Whether history influences scoring this run                       |
| `retention_days`               | int    | From config                                                       |
| `learned_burn_rates`           | object | Map `provider:pool:duration` → `{fraction_per_day, sample_count}` |
| `chronic_underuse`             | array  | Short windows with high avg remaining across ≥2 cycles            |
| `usually_left_late_cycle`      | array  | Avg remaining when observed ≥70% into a window                    |
| `burn_candidates_from_history` | array  | Subset of late-cycle leftovers (≥40% left avg) as burn hints      |

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
  "schema_version": "1.3",
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

An expiring dollar grant (`omp`) adds `spent_usd` and `balance_usd` to its
routing entry, so a reader sees the dollars without opening the account.

A routing entry for an operator-gated account (today `omp`) carries
`requires_operator_approval: true` and `ai_notice`, and its summary line ends
with `— AI: Do not use without asking operator`. It is still listed when
usable — aiuse itself does not enforce the gate; the agent toolchain's
dispatchers refuse an omp slice without operator approval — but an agent must
ask the operator before spending or routing work to it.

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

### `client_limits` — a client is failing although the quota has headroom

Added 2026-10-09 for [#33](https://github.com/djbclark/aiuse/issues/33).
Additive and optional: present only while a limit is active. An account and
each of its routing entries (`--available`, `snapshot.accounts[]`) may carry:

```json
"client_limits": [
  {
    "client": "cli",
    "cli_binary": "agy",
    "state": "limited",
    "source": "agy-cli-log",
    "last_limited_at": "2026-10-06T10:31:01.100236+00:00",
    "failed_attempts": 8,
    "runs": 1,
    "lookback_minutes": 60.0,
    "error": "RESOURCE_EXHAUSTED (code 429)",
    "evidence": "~/.gemini/antigravity-cli/log/cli-20261006_062848.log",
    "message": "agy CLI got RESOURCE_EXHAUSTED (429) on 8 attempts in 1 run, ..."
  }
]
```

It is read **passively** at report time from the client's own logs (today:
the agy CLI's `Run: attempt N failed (RESOURCE_EXHAUSTED (code 429) …` retry
lines within `analysis.agy_cli_lockout_minutes`, default 60, 0 disables). No
request is sent. `usable_now` stays quota-based, because the other client
(`acp-run agy`) may still serve the same quota. Route agy work through ACP, or
wait, while the CLI entry is `limited`. The summary line gains `[agy cli
rate-limited: 429 xN, last Mm ago; ACP may still work]`, and the board's
antigravity row gets the message as a note. The CLI logs have no positive
success marker, so the entry clears only when the 429s age out of the window.

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

| Field                | Type              | Notes                                                                                                                                                                                      |
| -------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `collected_at`       | string (ISO-8601) | UTC collection time                                                                                                                                                                        |
| `accounts`           | array             | Selected live rows (see below)                                                                                                                                                             |
| `cross_checks`       | array             | Informational tool comparisons                                                                                                                                                             |
| `collector_errors`   | string[]          | Per-source failures (`"cswap: …"`)                                                                                                                                                         |
| `disabled_services`  | object            | provider → reason from top-level `[disabled_services]` (1.1; `{}` = none). Rows for these providers are not collected — do not route to or spend them until the operator removes the entry |
| `hidden_pools`       | object            | provider[/pool_family] → reason from `[hidden_pools]` (1.3; `{}` = none). Display-only hide applied at collection                                                                          |
| `config_fingerprint` | string \| omitted | short id of the disable list, collector switches, and `[usage_sources]` pins this snapshot was collected under. Omitted on older snapshots. Additive (schema 1.1+)                         |

### `accounts[]` (`AccountUsage`)

| Field                        | Type              | Stable?                                                                                                                                                                                                                                                                  |
| ---------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `source`                     | string            | yes — `cswap` \| `codexbar` \| `caut` \| `caam` \| `openusage_ai` \| `openusage_sh` \| `tokscale` \| `clinepass` \| `hermes` \| `openrouter` \| `muse` \| `qwencloud` \| `bailian` \| `grok_billing` \| `acp`; grows as collectors are added                             |
| `provider`                   | string            | yes — collector id (e.g. `claude`, `codex`, `antigravity`)                                                                                                                                                                                                               |
| `cli_binary`                 | string \| null    | local CLI binary that spends this quota; `null` = API-only / cloud-run (see below)                                                                                                                                                                                       |
| `account`                    | string \| null    | email or label when known                                                                                                                                                                                                                                                |
| `plan`                       | string \| null    | plan name if reported                                                                                                                                                                                                                                                    |
| `billing_kind`               | string            | `subscription_window` \| `prepaid_balance` \| `payg_api` \| `unknown`                                                                                                                                                                                                    |
| `windows`                    | array             | quota windows                                                                                                                                                                                                                                                            |
| `balance_usd`                | number \| null    | prepaid balance                                                                                                                                                                                                                                                          |
| `credits_remaining`          | number \| null    | legacy credits field                                                                                                                                                                                                                                                     |
| `spent_usd`                  | number \| omitted | 2026-10-15 — dollars already spent from an expiring dollar grant (`omp`); pairs with `balance_usd` (left). Not `usage_credits`. Omitted when not reported                                                                                                                |
| `requires_operator_approval` | bool \| omitted   | 2026-10-15 — `true` when the operator gated this account (today `omp`): an agent must not spend it, route work to it, or recommend burning it without asking the operator first. aiuse only reports it; the agent toolchain's dispatchers enforce it. Omitted when false |
| `ai_notice`                  | string \| omitted | 2026-10-15 — the gate in words, `AI: Do not use without asking operator`; present exactly when `requires_operator_approval` is true                                                                                                                                      |
| `usage_credits`              | object \| omitted | extra/pay-as-you-go wallet when present                                                                                                                                                                                                                                  |
| `error`                      | string \| null    | row-level error                                                                                                                                                                                                                                                          |
| `notes`                      | string[]          | human notes (age, hydrate, etc.)                                                                                                                                                                                                                                         |
| `provider_id`                | string            | canonical provider id used for matching/history (equals `provider` unless the row carries an alias spelling)                                                                                                                                                             |
| `service_id`                 | string \| null    | reserved per-service split within a provider; always `null` today                                                                                                                                                                                                        |
| `collector_id`               | string            | collector that produced the row (equals `source` unless a collector overrides it)                                                                                                                                                                                        |
| `collected_at`               | string \| omitted | 1.1 — the source's own measurement time for this row (CodexBar `usage.updatedAt`); older than the snapshot's `collected_at` means the row was a cached read. Omitted when the source reports none                                                                        |
| `context_usage`              | object \| omitted | ACP context-window fill and this turn's tokens, when a recent acp-run log has them. Not a plan window. See [`acp-usage.md`](acp-usage.md). Omitted when there is no reading.                                                                                             |
| `usable_now`                 | bool              | 1.1 — false if any applicable window is exhausted or no window has data. For Claude, only shared limits affect account usability; model caps affect their routing family in `--available`                                                                                |
| `binding_window`             | string \| null    | 1.1 — label of the applicable window with the least remaining; Claude model caps do not bind the shared account                                                                                                                                                          |
| `binding_headroom_percent`   | number \| null    | 1.1 — that window's remaining share                                                                                                                                                                                                                                      |
| `available_at`               | string \| null    | 1.1 — earliest `resets_at` among applicable exhausted windows; Claude model caps are excluded from the shared account. Null when nothing applicable is exhausted or no reset is known                                                                                    |
| `age_seconds`                | number \| null    | 1.1 — seconds since this account's `collected_at`, computed at read time                                                                                                                                                                                                 |
| `overall_pace_percent`       | int \| null       | 1.2 — highest non-null window `pace_percent`; null when no window has pace or any window in the account is exhausted                                                                                                                                                     |
| `overall_pace_early`         | bool              | 1.2 — true when the window that supplied `overall_pace_percent` is early (<10% elapsed); false when null or not early                                                                                                                                                    |

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
| `state`                       | string               | 1.1 — `exhausted` (≤1% left) \| `tight` (<15% left) \| `ok` \| `unknown` (no data); computed so you never have to                                                                                                      |
| `headroom_percent`            | number \| null       | 1.1 — alias of `remaining_percent`, spelled for humans                                                                                                                                                                 |
| `pool_family`                 | string \| omitted    | 1.1 — routing family: `antigravity` → independent `gemini`/`claude_gpt`; `claude` → shared `default` with model caps such as `fable`; `cursor` → `total`/`auto`/`third_party`/`on_demand`/`grok_bot`; `omp` → `claude` |
| `role`                        | string \| omitted    | 1.3 — `ceiling` (total umbrella) or `breakdown` (sub-row of a ceiling)                                                                                                                                                 |
| `effective_left_percent`      | number \| omitted    | 1.3 — breakdown headroom after ceiling cap: min(own, ceiling)                                                                                                                                                          |
| `ceiling_binding`             | bool \| omitted      | 1.3 — true when the ceiling row binds the sub-row                                                                                                                                                                      |
| `ceiling_headroom_percent`    | number \| omitted    | 1.3 — remaining on the ceiling window                                                                                                                                                                                  |
| `ceiling_window_id`           | string \| omitted    | 1.3 — window_id of the ceiling row                                                                                                                                                                                     |
| `quota_scope`                 | string \| omitted    | Claude: `shared` or `model_sublimit`; a model cap is within shared quota, not additional capacity                                                                                                                      |
| `shared_pool_family`          | string \| omitted    | Claude model caps: `default`, the shared parent whose windows also constrain model availability                                                                                                                        |
| `max_share_of_parent_percent` | number \| omitted    | Fable: `50`, its maximum share of the shared weekly budget                                                                                                                                                             |
| `models_hint`                 | string \| omitted    | 1.1 — which models draw this pool, when cheap to say (e.g. `agy models — gemini-* models draw this pool`)                                                                                                              |
| `state_source`                | string \| omitted    | 1.1 — `agent-reported` when an active `note-exhausted` override flipped this window                                                                                                                                    |
| `agent_reported`              | object \| omitted    | 1.1 — `{resets_at, reason}` from the active override, when present                                                                                                                                                     |
| `pace_percent`                | int \| null          | 1.2 — `round((pace_ratio - 1) * 100)`; positive ahead, negative behind even spend; null when exhausted or unclocked                                                                                                    |
| `pace_ratio`                  | number \| null       | 1.2 — unblended `used_fraction / elapsed_fraction` (elapsed floored at 0.05); null when exhausted or unclocked                                                                                                         |
| `pace_early`                  | bool                 | 1.2 — true when window elapsed fraction is below 0.10; false when pace is null or not early                                                                                                                            |
| `resets_at_carried_from`      | object \| omitted    | 2026-10-13 — `{source, collected_at}` of the earlier reading whose `resets_at` was carried forward because the source dropped it; an estimate (see below)                                                              |

#### `resets_at_carried_from` — a reset the selected source dropped

Added 2026-10-13. Additive and optional (schema stays 1.2): present only on a
window whose `resets_at` was **not measured by this collection**.

Sources disagree on what a window reports — OpenUsage sends the reset of an
Antigravity 5-hour window, CodexBar does not — and selection keeps one
source's rows, so whenever CodexBar wins, an in-use window used to lose its
countdown. `aiuse` now keeps the last reset an earlier reading measured for
that window (same provider, pool, duration and account, whichever source
reported it) and marks it:

```json
"resets_at": "2026-10-13T17:37:26+00:00",
"resets_at_carried_from": { "source": "openusage_ai", "collected_at": "2026-10-13T14:27:47.224836+00:00" }
```

`source` is the collector that measured the reset and `collected_at` is when
that reading was collected; a reset passed along through several collections
keeps naming the reading that measured it. Treat `resets_at` as an
**estimate**: the board renders it with `~`, and an alert on it has
`deadline_is_estimated: true`. It is carried only when all of these hold;
otherwise `resets_at` stays `null`:

- the window has no `resets_at`, is in use (`used_percent` > 0) and has a
  known duration (any duration: 5-hour, weekly, monthly, …);
- the newest earlier reading of the same window that knew a reset is no older
  than the window's own length, and that reset is still in the future at this
  collection's `collected_at`;
- that reading was in use too (`used_percent` > 0): an unused window's reset
  is only "now plus the window length" and slides with every query;
- `used_percent` has not fallen since, in that reading or any between (by
  more than 1 point of source rounding): a fall means the window reset;
- a named account never borrows from a differently named account. An
  anonymous reading (OpenUsage's envelope is provider-scoped) is adopted only
  when exactly one live account holds that window, and a named reading is
  adopted by a sole anonymous live window (a collector that stopped naming the
  account).

### `omp` — an expiring dollar grant

Added 2026-10-15. Provider `omp` (display name `omp`, `cli_binary` `omp`) is
claude.ai's promotional **cloud-session credit**, which the omp TUI spends
through the Claude API. It is not the Claude 5h/weekly plan and never refills:
unused dollars are lost at expiry. Source: CodexBar's Claude row
([`omp-cloud-credits.md`](omp-cloud-credits.md)).

```json
{
  "provider": "omp",
  "cli_binary": "omp",
  "billing_kind": "subscription_window",
  "balance_usd": 249.269521,
  "spent_usd": 0.730479,
  "requires_operator_approval": true,
  "ai_notice": "AI: Do not use without asking operator",
  "windows": [
    {
      "label": "credits",
      "window_id": "claude-cloud-credits",
      "used_percent": 0.29,
      "remaining_percent": 99.71,
      "resets_at": "2026-11-05T07:59:00+00:00",
      "window_minutes": null,
      "refill_capacity": 250.0,
      "refill_capacity_unit": "usd",
      "pool_family": "claude"
    }
  ]
}
```

| Field                        | Meaning                                                             |
| ---------------------------- | ------------------------------------------------------------------- |
| `balance_usd`                | dollars left                                                        |
| `spent_usd`                  | dollars spent so far                                                |
| `windows[0].refill_capacity` | grant size in dollars (`refill_capacity_unit: "usd"`)               |
| `windows[0].resets_at`       | the grant's **expiry**, not a refill                                |
| `windows[0].window_minutes`  | `null`: the grant date is unknown, so there is no pace              |
| `windows[0].pool_family`     | `claude` (the board's SCOPE column; internal pool key `omp_claude`) |
| `requires_operator_approval` | always `true` on `omp`: the operator gate (below)                   |
| `ai_notice`                  | `AI: Do not use without asking operator`                            |

Without a grant total the row falls back to `billing_kind: "prepaid_balance"`
with no window, still carrying `balance_usd` (and `spent_usd` when CodexBar
reports it). Summary line:
`omp claude: credits: ok (<1% used / 100% left, $0.73 spent / $249.27 left of $250.00 expiring grant, expires Thu Nov 5 2:59am) — AI: Do not use without asking operator`.

**Operator gate (rule for agents).** `omp` carries
`requires_operator_approval: true` and `ai_notice` on the account, on its
`--available` routing entry, and on any alert raised for it (the alert's
`message` and summary line end with `— AI: Do not use without asking
operator`). Do not spend this grant, route work to omp, or recommend burning
it without asking the operator first, even when it is usable and expiring.
aiuse only reports the gate; the agent toolchain enforces it at dispatch
(`acp-run` and `acp-dispatch` refuse an omp slice unless
`ACP_OPERATOR_APPROVED=omp` is set for that call; see
[`omp-cloud-credits.md`](omp-cloud-credits.md)). The board shows
`(AI: Do not use without asking operator)` across the empty 5H and WEEK cells,
and `--for-chat` adds a `↳ AI: Do not use without asking operator` line.

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

| Field                        | Type              | Notes                                                                                                                          |
| ---------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `urgency`                    | string            | `critical` \| `high` \| `medium` \| `low` \| `info` \| `none`                                                                  |
| `provider`                   | string            |                                                                                                                                |
| `account`                    | string \| null    |                                                                                                                                |
| `window_label`               | string            |                                                                                                                                |
| `remaining_percent`          | number            | share of the window still available                                                                                            |
| `used_percent`               | number \| null    | share already consumed; null when the source reports neither                                                                   |
| `days_until_reset`           | number \| null    |                                                                                                                                |
| `deadline_is_estimated`      | bool              | true when the reset is a date alone or was carried forward                                                                     |
| `plan`                       | string \| null    |                                                                                                                                |
| `message`                    | string            | human sentence                                                                                                                 |
| `source`                     | string            | data source for the window                                                                                                     |
| `score`                      | number            | sort priority (higher = more important)                                                                                        |
| `window_minutes`             | int \| null       |                                                                                                                                |
| `kind`                       | string            | `burn` \| `conserve` \| `prepaid` (non-expiring API balance)                                                                   |
| `consumption_analysis`       | object \| omitted | flexibility profile when present                                                                                               |
| `pace`                       | object \| omitted | pace profile when present                                                                                                      |
| `requires_operator_approval` | bool \| omitted   | 2026-10-15 — `true` for an alert on an operator-gated account (`omp`); `message` then ends with the notice. Omitted when false |
| `ai_notice`                  | string \| omitted | 2026-10-15 — `AI: Do not use without asking operator`, with the flag above                                                     |

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
| `exhaustion_lead_seconds`  | number \| null              |

`exhaustion_lead_seconds` is how far ahead of the window's reset the projected
exhaustion lands (`resets_at - projected_exhaust_at`, in seconds). It is set only
when exhaustion is projected strictly before the reset, else `null`. Human
outputs render it scaled to hours, days or weeks — e.g. `locked out ~Sat 3:00pm,
3 days before reset`.

`has_overage` is `true` when the account has a real (`AccountUsage.usage_credits`) or config-confirmed (`provider_overrides.<provider>.overage_state: "enabled"`) overage/extra-usage wallet. It qualifies, never suppresses, a `conserve`/`burn` verdict — `true` means the real risk is unplanned $ spend (soft ceiling), not lockout (hard ceiling). See `docs/shared-quota-semantics/formulas/pace.md`'s rule O1.

## Stability policy

- **Additive fields** may appear without a major version bump (new optional keys).
- **Renames / removals** of listed stable keys require a major version bump and README note.
- **Meaning changes** that leave a field's shape alone (such as which window `suggestion` names) keep
  the schema version and are recorded in the Changelog.
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
