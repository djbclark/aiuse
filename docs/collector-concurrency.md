# Collector concurrency and timeouts

Audit of how `aiuse` shells out to external data sources (post 45s default timeout
policy). No code change required from this write-up unless noted.

## Data sources (all seven)

| Collector        | Interface                                                | Role                                        |
| ---------------- | -------------------------------------------------------- | ------------------------------------------- |
| **cswap**        | `cswap list --json`                                      | Multi-account Claude (canonical)            |
| **CodexBar**     | `codexbar usage --format json`                           | Broad live quotas (preferred non-Claude)    |
| **caut**         | `caut usage --json`                                      | Independent multi-provider peer / fill-in   |
| **caam**         | `caam limits --format json` (vault profiles)             | Last-resort peer behind every direct source |
| **OpenUsage.ai** | `openusage` CLI and/or `http://127.0.0.1:6736/v1/limits` | Independent peer / fill-in                  |
| **OpenUsage.sh** | `openusage-sh export --output - --format json`           | Independent local telemetry / quota backup  |
| **tokscale**     | `tokscale usage --json`                                  | Independent peer; preferred for Copilot     |

Install all of them: [`packaging/install-deps.sh`](../packaging/install-deps.sh)
or site `just install-aiuse-deps`.

## Architecture (wall-clock)

```
aiuse main
 └─ run_collectors (ThreadPoolExecutor, max_workers = N enabled collectors ≤ 7)
     ├─ collect_cswap        → one `cswap list --json` (timeout: cswap)
     ├─ collect_codexbar     → discovery + concurrent per-provider queries
     ├─ collect_caut         → `caut usage --provider all --json` (timeout: caut)
     ├─ collect_caam         → `caam limits --format json` + local `caam status --json` (timeout: caam)
     ├─ collect_openusage_ai → CLI and/or loopback HTTP (timeout: openusage_ai)
     ├─ collect_openusage_sh → versioned CLI export (timeout: openusage_sh)
     └─ collect_tokscale     → one `tokscale usage --json` (timeout: tokscale)
```

**Wall-clock cost ≈ max(collector durations)**, not the sum, while all enabled
collectors are healthy.

## Defaults

| Knob                    | Default               | Where                                                                                     |
| ----------------------- | --------------------- | ----------------------------------------------------------------------------------------- |
| `timeouts.default`      | **45s**               | `config.toml` / built-in                                                                  |
| Per-tool keys           | inherit default       | `codexbar`, `codexbar_discovery`, `caut`, `caam`, `openusage_ai`, `tokscale`              |
| `timeouts.cswap`        | **90s**               | `cswap list --json` crossed 45s beside the other collectors                               |
| `timeouts.caam`         | **90s**               | `caam limits` sweeps four providers under caam's own 60s deadline                         |
| `timeouts.openusage_sh` | **90s**               | direct `openusage-sh export` often takes ~30s and crosses 45s beside the other collectors |
| CLI `-t` / `--timeout`  | sets `timeouts.force` | wins over every tool for that run                                                         |
| Doctor version probe    | **5s** hard cap       | does not use usage endpoints                                                              |

Tools either return in tens of seconds or hang; long budgets only delay failure
(see fix-plan history: 180s → 45s).

## Per-collector detail

### cswap

- Single subprocess: `cswap list --json`.
- Timeout: built-in `timeouts.cswap` is 90s. A generic `timeouts.default` does not erase it. An explicit `timeouts.cswap` or `--timeout` still wins.
- Multi-account JSON; may hydrate from on-disk last-good when decision-stale
  (see [cswap-reliability.md](cswap-reliability.md)).

### CodexBar

- Discovers enabled providers (`codexbar config providers` or equivalent),
  timeout `codexbar_discovery` (usually milliseconds).
- Then **one subprocess per provider**, concurrent via `ThreadPoolExecutor`,
  capped at **`_MAX_CONCURRENT_PROVIDER_QUERIES = 16`**.
- Per-provider timeout: `timeout_for(config, "codexbar")` (full 45s budget
  **each** — a stuck provider can hold its own slot that long), unless
  `[collectors.codexbar] provider_timeouts = { <provider> = <seconds> }`
  overrides it. The built-in map sets `opencodego = 60`, the same budget as
  CodexBar's own `--web-timeout`, so that fetch is not killed at 45s. A CLI
  `--timeout` still wins over both. OpenCode Go is `--source web` only: a
  miss does not fall through to auto, which rescans
  `~/.local/share/opencode/opencode.db` (there is no usage-result cache;
  `--refresh` belongs to `codexbar cost`) and can report a dollar-cap
  estimate that disagrees with the console.
- **Hang backoff** (aiuse-e9d, 2026-10-09): a provider whose query is killed
  at its timeout is skipped for `[collectors.codexbar] timeout_backoff`
  seconds (default 1800), doubling per further consecutive timeout up to 12x.
  Its `codexbar-query-errors` entry says `skipped: query timed out ... next
try in ...`. Any answer that is not a timeout clears it. State is shared by
  every aiuse process in `~/.cache/aiuse/query-throttle/codexbar-timeouts.json`,
  so the scheduled sampler pays a hang once per window, not once per sample.
  `alibabatokenplan` was the motivating case: on 2026-10-09 an explicit
  `codexbar usage --provider alibabatokenplan` still hung past 60s (CodexBar
  0.73.0), while `devin` answered in about 20s.
- Rationale: bundled “all enabled” calls inside CodexBar are serial; fan-out
  makes wall-clock ≈ slowest provider, not sum.

### caut

- Single subprocess: `caut usage --provider all --json` by default (correctness).
- Timeout: `timeout_for(config, "caut")`.
- Setup: [collectors-caut-openusage.md](collectors-caut-openusage.md).

### caam

- `caam limits --format json` (vault profiles, claude/codex/grok/cursor) plus a
  local `caam status --json` for health notes; see [caam.md](caam.md).
- Timeout: built-in `timeouts.caam` is 90s (caam's own deadline is 60s).

### OpenUsage

- Prefer PATH CLI `openusage` (optional `--force`); else HTTP
  `GET http://127.0.0.1:6736/v1/limits` (optionally launch the app first).
- Timeout: `timeout_for(config, "openusage")`.
- Setup: [collectors-caut-openusage.md](collectors-caut-openusage.md).

### tokscale

- Single subprocess: `tokscale usage --json`.
- Timeout: `timeout_for(config, "tokscale")` (45s).
- No per-provider fan-out today — investigation:
  [tokscale-per-provider-investigation.md](tokscale-per-provider-investigation.md).

## Selection and cross-check

- **Selection** picks one primary source per provider (priority lists in
  `collectors/runner.py`: Claude → cswap first; Copilot → tokscale first;
  else CodexBar → caut → OpenUsage → tokscale).
- **Cross-checks** compare **all pairs** of live sources for correctness.

## Executor lifecycle note

`run_collectors` submits all jobs inside a `with ThreadPoolExecutor(...)` block,
then reads `.result()` **after** the context exits. Exiting the context calls
`shutdown(wait=True)`, so work is finished before results are collected. Correct,
if slightly unusual; do not “optimize” by dropping `wait` without also moving
result collection inside the `with` block.

## Failure isolation

- A raised exception from one collector becomes a `snapshot.collector_errors`
  string; other collectors still contribute accounts.
- CodexBar partial provider failures can attach an error row without wiping
  successful providers.
- Exit code **1** only when there are errors **and** zero accounts overall.

## What looks healthy

| Scenario                     | Expected                                                                           |
| ---------------------------- | ---------------------------------------------------------------------------------- |
| All tools warm / cached      | Often **under ~5–20s** wall-clock                                                  |
| Cold CodexBar multi-provider | Dominated by slowest provider; **≤ 45s** per provider slot, **60s** for opencodego |
| One tool hang                | Fails at its own budget; other collectors stay usable                              |
| `aiuse -t 10`                | Every tool forced to 10s (faster fail for scripts)                                 |

## Recommendations (standing)

1. Keep **45s** as the default. `cswap`, `caam`, and `openusage_sh` keep their built-in 90s. Use `-t` only for tighter scripts.
2. Prefer `--no-tokscale` when iterating on Claude-only workflows if tokscale is slow.
3. Use `aiuse doctor` for PATH + version probe; full usage still needs a collect run.
4. Do not raise global timeout back toward 180s without evidence a tool needs it.

## Code map

| Piece                        | Path                                                    |
| ---------------------------- | ------------------------------------------------------- |
| Concurrent top-level collect | `src/aiuse/collectors/runner.py` → `run_collectors`     |
| CodexBar provider fan-out    | `src/aiuse/collectors/codexbar.py` → `_query_providers` |
| Timeout resolution           | `src/aiuse/config.py` → `timeout_for`                   |
| Doctor probe                 | `src/aiuse/cli.py` → `probe_tool_version`               |
| Dep installer                | `packaging/install-deps.sh`                             |
