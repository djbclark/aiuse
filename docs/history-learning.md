# Snapshot history and learning

`aiuse` can persist each collect under `~/.cache/aiuse/snapshots/` and blend
that history into pace scoring / chronic-waste alerts once enough data exists.

## Config flags

In `~/.config/aiuse/config.toml`:

```toml
[analysis]
persist_snapshots = true
# true | false | auto (default)
learn_from_history = "auto"
snapshot_retention_days = 90
```

| Flag                 | Default | Effect                  |
| -------------------- | ------- | ----------------------- |
| `persist_snapshots`  | `false` | Save snapshots each run |
| `learn_from_history` | `auto`  | See below               |

### `learn_from_history`

| Value   | Behavior                                                               |
| ------- | ---------------------------------------------------------------------- |
| `auto`  | Learn once retained snapshot count ≥ **2** (same floor as the learner) |
| `true`  | Always attempt learning (still no-op if history is empty/thin)         |
| `false` | Never use history for scoring/alerts                                   |

With `auto`, learning turns on by itself as soon as it can be useful — no manual
flip after the LaunchAgent starts filling the cache. Set `false` to keep
persist-only forever.

Learning (when active) also implies snapshot persistence for that run.

## Status on `--full`

`aiuse --full` includes a **History** section with:

```text
## History
History: N snapshots in …/snapshots (learning auto/waiting|auto/on|on|off)
  span: YYYY-MM-DD HH:MM → YYYY-MM-DD HH:MM UTC (…, N files)
  retention: 90d (config snapshot_retention_days)
  Learned burn rates (blended into pace when present):
    · Claude weekly: ~12%/day (5 samples)
  Usually left late in cycle (≥70% elapsed; proxy for waste at reset):
    · Codex weekly: ~45% left avg (4 late samples)
  History suggests burning these (often leftover late in window):
    · Codex weekly — usually ~45% left late
  Chronic underuse (short windows, multiple reset cycles):
    · Claude 5-hour: 85% left avg over 3 cycles
```

When learning is waiting or disabled, the section explains that instead of listing
rates. Action-plan and per-window detail lines note `blended with history (N samples)`
when pace used learned rates.

Full `aiuse --json` also includes a top-level `history` object (learned rates,
late-cycle leftovers, chronic underuse) — see [`json-contract.md`](json-contract.md).

## `aiuse history` (no collect)

[Issue #13](https://github.com/djbclark/aiuse/issues/13). `aiuse history` prints
the same History section as `--full`, without collecting: it reads the newest
saved snapshot and the retained history behind it, so it touches no vendor.
Its cost is one JSON parse of every retained snapshot file, read once and
shared by every section, so it grows linearly with retention up to the
10,000-file cap: about 1 s for `--json` and 1.5 s for text with 2,600
snapshots (107 MB). It leads with one line naming what history says to burn:

```text
aiuse history · newest snapshot 2026-10-09 06:30 UTC (3m old) · read from disk, no collect
Burn from history: Codex weekly (~68% left late, 2 samples) · Claude weekly (~52% left late, 9 samples)
History: 2592 snapshots in …/snapshots (learning auto/on)
  …
```

The headline lists up to three `burn_candidates_from_history` (windows usually
left ≥40% late in their cycle), then `+N more`. It says "nothing stands out"
when learning is on and nothing qualifies, and is omitted while learning is off
or waiting (the section explains why).

`aiuse history --json` is the scripting path to "what to burn from history":

```bash
aiuse history --json | jq -r '.history.burn_candidates_from_history[] | "\(.provider) \(.duration_kind)"'
```

It prints `schema_version`, `contract_url`, `source: "cache"`, `collected_at`,
`age_seconds` and `history`, the same object as the top-level `history` on full
`--json` (see [`json-contract.md`](json-contract.md)). Exit 1 when no snapshot
exists yet.

## What learning does

When active ([`src/aiuse/analysis/history.py`](../src/aiuse/analysis/history.py)):

- **Learned burn rates** — blend into pace so early-window classification is less noisy
- **Learned flexibility** — light adjustment of flexibility scores
- **Chronic waste** — short windows that stay high-remaining across **multiple**
  reset cycles can surface as history-sourced alerts. When a short window is a
  child of a shared allotment, its history alert is suppressed in favor of the
  current governing weekly/monthly window, so stale short-window averages
  cannot contradict an exhausted pool.

## Scheduling

See [`scheduling.md`](scheduling.md). Site LaunchAgent enables `persist_snapshots`
and leaves learning on `auto`.

## Related

- [`json-contract.md`](json-contract.md) — exit codes + `history` object shape
- [`config/config.example.toml`](../config/config.example.toml) — example keys
- [`competitive-landscape.md`](competitive-landscape.md) — History vs onWatch BI
- [`scheduling.md`](scheduling.md) — LaunchAgent that densifies snapshots
- [`next-options.md`](next-options.md) — where History polish ([#13](https://github.com/djbclark/aiuse/issues/13)) sits
