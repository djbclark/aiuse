# Snapshot history and learning

`aiuse` can persist each collect under `~/.cache/aiuse/snapshots/` and blend
that history into pace scoring / chronic-waste alerts once enough data exists.

## Config flags

In `~/.config/aiuse/services.yaml`:

```yaml
analysis:
  persist_snapshots: true
  # true | false | auto (default)
  learn_from_history: auto
  snapshot_retention_days: 90
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

## Status on ``--full``

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

Full ``aiuse --json`` also includes a top-level ``history`` object (learned rates,
late-cycle leftovers, chronic underuse) — see [`json-contract.md`](json-contract.md).

## What learning does

When active ([`src/aiuse/analysis/history.py`](../src/aiuse/analysis/history.py)):

- **Learned burn rates** — blend into pace so early-window classification is less noisy
- **Learned flexibility** — light adjustment of flexibility scores
- **Chronic waste** — short windows that stay high-remaining across **multiple**
  reset cycles can surface as history-sourced alerts

## Scheduling

See [`scheduling.md`](scheduling.md). Site LaunchAgent enables `persist_snapshots`
and leaves learning on `auto`.

## Related

- [`json-contract.md`](json-contract.md) — exit codes for scheduled runs
- [`config/services.example.yaml`](../config/services.example.yaml) — example keys
