# Changelog

All notable changes to `aiuse`. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are plain
`X.Y.Z` and match the git tags, PyPI and Homebrew
([`docs/packaging.md`](docs/packaging.md)). The `--json` contract has its own
history in [`docs/json-contract.md`](docs/json-contract.md#changelog).

Releases before 3.9.0 are in the
[GitHub releases](https://github.com/djbclark/aiuse/releases) and the git log
(`git log v3.8.0`). From 3.12.1 on, every release adds an entry here.

## [5.3.2] - 2026-11-21

### Fixed

- The live `codexbar usage` path now drops CodexBar's invented reset clock on a
  0%-used window, as the widget-snapshot path has since 5.3.0. A window nobody
  has used has no real clock (it starts on first use), but CodexBar fills
  `resetsAt` with its snapshot time plus the window length (read live
  2026-11-21 14:47Z: Claude/GPT 5-hour and weekly both one second short of
  `updatedAt` plus 300 and 10080 minutes). The slots and `extraRateWindows`
  are both covered; the cell reads `5h↻` until a real call starts the clock.
  A used window keeps its real reset. See
  [`docs/antigravity-pools.md`](docs/antigravity-pools.md).

## [5.3.1] - 2026-11-21

### Fixed

- The antigravity capacity signal added in 5.3.0 never fired on real data: Paseo
  marks an agent that hit the error `lastStatus: "closed"`, and the scan only
  read `error` agents. It now reads `lastError` from agents in any status
  (verified on the 2026-11-19 and 2026-11-21 agent files); the z.ai throttle and
  refusal scans keep their error-only reading.
- README: `--json` is schema 1.7 (it said 1.4) and says what makes `usable_now`
  false.

## [5.3.0] - 2026-11-21

### Changed

- A window under 5% left is no longer "usable": `usable_now` is false and the
  window's reset becomes `available_at` (the window itself stays `tight`, not
  `exhausted`; `NEAR_EMPTY_BELOW` in `analysis/selfdescribe.py`). On
  2026-11-21 OpenCode Go's weekly window read 2% left and "usable", and the next
  agent run hit `GoUsageLimitError` within minutes. Live test calls against
  every pool (OpenCode Go, ClinePass, z.ai, antigravity) agreed with aiuse's
  full/usable verdicts otherwise.
- A new `client_limits` entry of `kind: "capacity"` on antigravity reports
  Paseo agents' transient `Resource has been exhausted (e.g. check quota)`
  errors from cloudcode-pa for 10 minutes: a short, model-level cooldown, never
  an exhausted pool (`usable_now` stays quota-based). It names the models that
  failed. See [`docs/antigravity-pools.md`](docs/antigravity-pools.md).

### Fixed

- Antigravity windows at 0% no longer show a made-up countdown. CodexBar sets
  an unused window's reset to its own snapshot time plus the window length, so
  `Claude/GPT 5-hour` read `4h56m` forever; the stamp is dropped and the cell
  reads `5h↻` (clock not started) until the first call starts a real clock.

## [5.2.1] - 2026-11-21

### Changed

- The `Detail: aiuse --full` hint line is gone from the default stderr header,
  the TUI footer and `aiuse watch` (the header already carries the AI
  `--json` line; `aiuse --help` and the README still describe `--full`).
  Watch gains a row back.
- The ACCT column caps labels at 9 characters (was 16), cut with `…`;
  `[account_aliases]` still shortens a long name.

### Fixed

- z.ai's Chinese "no coding plan" reply (`当前用户不存在coding plan`) now reads
  `z.ai: this account has no coding plan` instead of the raw plugin error. The
  account really has no active plan then; the original text is kept in the row's
  `notes` ([`docs/zai-quota.md`](docs/zai-quota.md)).

## [5.2.0] - 2026-11-21

### Added

- `aiuse_version`, the aiuse build that collected the data, is recorded on every
  persisted snapshot, burst sample and ledger sample and shown on the
  `--json`, `--available --json` and serve envelopes (additive, JSON schema
  1.7). Old records without it mean "unknown". A snapshot replayed from disk
  shows its own recorded build. This lets history from a build later found buggy
  be found and corrected ([`docs/data-corrections.md`](docs/data-corrections.md)).
- Human output names the build: the meta line reads `Collected by aiuse X.Y.Z
at …` (`aiuse ?` when unrecorded), and `--available`, `attribute`,
  `usage-sources`, `gate status` and the chat report show it once.

### Changed

- `aiuse watch` puts the `Collected by aiuse …` line on the same row as the AI
  `--json` line, right-aligned, when the terminal is wide enough; narrower
  terminals keep the two-row layout.
- The plain `aiuse` hint reads `Detail: aiuse --full` (was `Detail: ai --full`).

## [5.1.3] - 2026-11-21

### Fixed

- An idle or freshly bought ClinePass plan is now classed as
  `subscription_window` instead of `unknown`. ClinePass omits `resetsAt` while a
  window sits at 0%, so a plan with no usage yet had no reset time on any window
  and fell through the reset-time test (seen on the second ClinePass account,
  2026-11-21, checked against the live API and the Cline dashboard).
  `SUBSCRIPTION_WINDOW_PROVIDERS` in `collectors/codexbar.py` now names providers
  whose plan is always a subscription; noted in `docs/clinepass-quota.md`.
- Codex: OpenAI API prepaid credits surface as their own fail-closed pool, with
  the pool family named `openai_api` (matching the canary).
- Report: alert-only rows use `matrix_pool_scope` for their POOL/SCOPE names.
- launchd: the scheduled refresh now also refreshes `openai-api` credits
  alongside `claude-api`.
- Docs: `docs/json-contract.md` separates the pre-Schema-1.6 and Schema-1.6
  POOL/SCOPE names.

## [Unreleased]

### Added

- Per-service headroom floors (schema 1.8, additive and advisory): every
  `--available` entry (`available[]` and `unavailable[]`) carries
  `floor_percent`, `floor_basis` (`reserve_usd`, `config`, `default`) and
  `headroom_above_floor_percent`. `floor_percent = clamp(reserve_usd /
plan_monthly_price * 100, min_percent, max_percent)`, so a $199.99 plan
  gives 5.0 and a $20 plan 20.0; `default_percent` (10.0) when no plan price is
  known. New `[floors]` config table with per-provider `percent` or
  `reserve_usd` overrides, validated by `validate_config` and explained in
  `config/config.example.toml`. `headroom_percent`, `usable_now` and the 5%
  near-empty rule are unchanged.
- `aiuse --available --json` gains a top-level `unavailable[]` (schema 1.8, bumped
  from 1.7): one item per pool entry that is not usable now and is neither
  operator-excluded nor a hidden ceiling row, with `reason` (`exhausted`,
  `near_empty`, `rate_gated`, `no_reading`), `binding_window`, `available_at`,
  `headroom_percent`, the refresh-gate fields and `rate_gated_by` when present.
  A consumer can now tell "empty until Monday" from "not reported".
  `available` and `excluded` are unchanged. Documented in
  `docs/json-contract.md` and `semantics.unavailable`.

### Fixed

- `validate_config` (and so `aiuse doctor`) warns when a `[floors.providers.<p>]`
  override exists for a provider that has a built-in prepaid pool-family default
  (`codex` and `omp` with `openai_api`): the provider's percent or reserve does
  not reach that pool, whose pool-family default (`default_percent`, basis
  `default`) still applies unless `[floors.pool_families.openai_api]` is set. A
  warning only; precedence is unchanged (schema 1.8 adversary round 2, nit 7).
- `validate_config` warns when `[floors.pool_families]` carries two spellings of one
  family (`openai_api` and `OPENAI_API`), as it already did for
  `[floors.providers]`; the first in the table applies (schema 1.8 adversary round 2,
  nit 6).
- A boolean `query_min_interval` (`antigravity = true`) is now a `validate_config`
  error instead of passing as a 1 s gate, and an invalid value (nan, text, a
  boolean, a list) leaves the refresh-gate fields on the same default the throttle
  enforces (900 s for antigravity) instead of dropping `refreshable_at`,
  `refresh_blocked_by_gate` and `gate_remaining_seconds`, which read as "no query
  gate" (schema 1.8 adversary round 2, nit 5). `enrich_snapshot` now takes its
  intervals from `collectors.throttle.min_intervals`, the table the gate enforces.
- `next_query_at` (the throttle state behind `refreshable_at` /
  `refresh_blocked_by_gate`) treats a future `queried_at` as clock skew, the way
  `TimeoutBackoff.blocked_until` does and `QueryGate.__enter__` allows the query,
  instead of reporting a closed gate for up to the whole interval (schema 1.8
  adversary round 2, nit 4).
- The Argon evidence store's load-modify-write now runs under an exclusive
  `flock` on `argon-evidence.json.lock` (same pattern as the query throttle and
  `TimeoutBackoff`), so a sampler and a CLI run that collect at once no longer lose
  an increment or an "Argon window seen" reset (schema 1.8 adversary round 2, nit 2).
- A partial Antigravity reading is not evidence that the plan has no Argon window
  (schema 1.8 adversary round 2, nit 1). Only a reading that shows the shared
  Gemini (Flash) window and no Argon window counts toward the 3 readings over 6
  hours; a reading holding just the Claude/GPT window is skipped and neither
  extends nor resets the evidence.
- The Argon evidence store is written only on the collection path (schema 1.8
  adversary round 2, important B). `aiuse --available`, a cache `--json`, the serve
  endpoints and a screen-only `watch` used to write
  `~/.local/state/aiuse/argon-evidence.json` through `enrich_snapshot`, and
  re-reading one cached snapshot with a future `collected_at` could prove the
  absence of an Argon window by itself (`[False, False, True]`). Now
  `history.save_snapshot` (the sampler and every run that saves a snapshot) passes
  `enrich_snapshot(record_argon_evidence=True)`; read paths use the read-only
  `argon_evidence.proven_absent`, which neither writes nor creates the directory.
  A reading stamped in the future (clock skew) is skipped instead of clamped.
  **Upgrade consequence:** the fold needs evidence a build with the store has
  collected itself, so after upgrading an existing Google AI Ultra account folds
  nothing for at least 6 hours and 3 collected readings, and meanwhile an Argon
  refusal leaves the shared `gemini` pool `usable_now: true` (it fails open).
- The Argon evidence store no longer crashes on a corrupt or foreign-typed record.
  A record field that is a string, list, dict, NaN, Infinity, a huge number, a
  boolean, a negative count, an unparsable or unrepresentable date, or a date in the
  future now resets just that record to "no evidence" instead of raising out of
  `enrich_snapshot` (and so out of `--available`, `--json`, serve and the sampler).
  The store file is also created 0600 from the start and its temp file is removed
  when a write fails (schema 1.8 adversary round 2, important A).
- The Argon model-to-pool map no longer assumes every account has an Argon
  window. `gemini-4-argon*` maps to `gemini_argon` only when the account
  reports an Argon-own window (Google AI Pro did); on Google AI Ultra, where
  Carbon, Neon, Hydrogen and Argon draw the one shared Gemini pool, it maps to
  `gemini`, the Gemini `models_hint` drops its "except gemini-4-argon*", and an
  Argon refusal note or ACP turn applies to the shared pool instead of a pool
  that does not exist (schema 1.8; `docs/antigravity-pools.md`).

- `available_at` is the LATEST reset among a pool's depleted windows (exhausted,
  or under 5% left), not the earliest: every depleted window must reset before
  the pool works again. OpenCode Go account `t-e` read 2026-11-23 (the weekly
  reset) while its monthly window stayed out until 2026-11-27. Applies to the
  account's and the routing entry's `available_at` and to Cursor sibling pools;
  the omp shared-Claude gate now combines with the entry's own depleted windows
  the same way (schema 1.8; `docs/json-contract.md`). When any depleted window
  has no parseable reset time, `available_at` is null (and the unavailable
  item's `binding_window` names that window) instead of naming another
  window's reset while the undated one is still empty.
- Schema 1.8 review nits: a blocked pool's `unavailable[]` item carries `state`,
  `refusal_kind` and `operator_action_required` (a re-collect cannot clear it);
  `[floors.providers]` warns on two spellings of one provider and on an unknown provider;
  `floor_basis: "default"` is documented as "no positive numeric plan price" and a
  non-numeric `monthly_price` no longer yields `headroom_usd`; `accounts[]` documents
  `floor_percent`, `floor_basis` and the refresh-gate fields; the two `binding_window`
  meanings and the excluded-and-unusable gap are spelled out; new end-to-end tests for
  every `unavailable[]` reason, and a frozen clock in the stale-pool age test.
- Per-pool-family floors: `[floors.pool_families.<family>]` (`percent` or `reserve_usd`)
  overrides the provider's floor for one pool, and the prepaid `openai_api` pool (the
  Codex CLI's OpenAI API credits) now takes `default_percent` (`floor_basis` `default`)
  instead of the Codex plan-price formula. New account field `floor_by_pool_family`.
- `[floors]` `min_percent` or `max_percent` set alone, crossing the other's
  default (`min_percent = 50`, `max_percent = 1`), now draws a `validate_config`
  warning that names which way the bounds are swapped; both set and crossed stays an
  error, and `resolve_floor` still swaps defensively.
- `query_min_interval.<provider> = inf` (or 1e20) no longer crashes every
  enrichment path with `OverflowError`: `validate_config` rejects non-finite
  values and anything above 7 days (`MAX_QUERY_MIN_INTERVAL`), and the
  throttle and the refresh-gate fields clamp what slips through. The example
  config's "cap 6h" for the empty-answer backoff said 6h where the code caps
  it at 1h.
- `refreshable_at` / `refresh_blocked_by_gate` / `gate_remaining_seconds` now come
  from the throttle state (`collectors/throttle.py` `next_query_at`: the last
  query's `queried_at` plus the interval in force, including the empty-answer
  backoff, and the CodexBar timeout backoff) instead of the reading's own time
  plus the interval, which could read "open" while `--live` was still refused
  (CodexBar's `updatedAt` lags the query by 15-30 minutes). The reading time is
  only the fallback when no state exists.
- The Argon fold needs positive evidence (schema 1.8). A `gemini_argon` refusal
  note or ACP turn folds into the shared `gemini` pool only when the account has
  had no Argon window across at least 3 consecutive readings over at least
  6 hours (new `aiuse.analysis.argon_evidence`, per account, hashed, in
  `~/.local/state/aiuse/argon-evidence.json`); a hidden, unreadable or
  one-reading-missing Argon window never folds, a hidden `gemini_argon` family
  counts as reported, and the frozen-quota pass and the note matcher now judge
  each account on its own families instead of the provider-wide union.
  `pool_family_for_acp_model` without families now returns the nominal
  `gemini_argon`.
- omp's account-level `available_at` (`aiuse --json` `accounts[]`) is now the later
  of the shared-Claude gate's reset and its own depleted windows, matching the
  routing entry; it used to keep the gate's earlier reset alone (schema 1.8).
- Judge freshness per pool instead of with one global 30-minute threshold. A
  fast-draining pool (antigravity) now reads stale long before the global
  limit — `analysis.fresh_threshold_seconds_by_provider` ships `antigravity =
600` — and `aiuse --available`'s top-level `fresh` is true only when every
  usable entry is within its own pool's limit, with a `stale_pools` list
  naming the offenders. On 2026-11-12 the envelope reported `fresh: true` /
  `usable_now: true` with 15-20-minute-old antigravity readings while a burst
  of agents was emptying the pools (Antigravity already refused with
  "Individual quota reached"). A future `collected_at` (clock skew) clamps to
  age 0 instead of a negative age. Additive, schema stays 1.5; contract noted
  in `docs/json-contract.md`.

### Added

- Cursor routing entries carry `sibling_pools` (the account's other pools in any
  state, with their own reset) and the `grok_bot` entry drops `cli_binary`.
  `--available` lists only usable pools, so with the monthly Auto and third-party
  pools exhausted (the plan rejecting a Cursor model with "Upgrade your plan")
  the only Cursor entry left was `grok_bot` at ~80% headroom, which read as
  `cursor-agent` being usable on 2026-11-13. `grok_bot` is the Grok Bot weekly
  allowance, not what `cursor-agent`'s models spend; its `models_hint` now says
  so and names the monthly pools that are out, and `aiuse --available` prints a
  `NOT usable now` line per exhausted sibling. A spent monthly `total` ceiling
  now also makes the `auto` and `third_party` entries `usable_now: false`
  (they stayed usable at 0% headroom). Additive, schema stays 1.5;
  contract in `docs/json-contract.md`, vocabulary in `docs/cursor-quota.md`.
- Paseo agents are now a passive refusal source, so a pool fails closed right
  after the first vendor refusal instead of when the quota endpoint catches
  up. The daemon's per-agent error files (`~/.paseo/agents/...`) are scanned
  read-only at note/limit load time: a multi-day `Individual quota reached …
Resets in XhYmZs` refusal persists as the usual `agent-reported` exhaustion
  note (reset parsed from the text, anchored to the error's `updatedAt`;
  antigravity `claude-*`/`gpt-*` → `claude_gpt`, `gemini-4-argon*` →
  `gemini_argon`), and the refusal beats a stale or optimistic reading until
  its reset — or, with no reset in the text, until a fresh successful probe
  (notes whose evidence file no longer shows the refusal are pruned). The Pi
  `enforced spend limit` refusal (metered OpenAI API platform, `openai/*`
  models → the `pi` provider's `openai_api` pool, never codex) marks that
  pool exhausted with a bounded 24 h fallback reset; `openai_api` exhaustion
  is refusal-driven only, since neither CodexBar's meter nor the OpenAI API
  exposes the enforced limit. A short rate limit — the Pi z.ai `429 code
1302` — is never an exhaustion: it surfaces as a `client_limits` cooldown
  entry (state `limited`, 10 min) on the `pi` account with `usable_now`
  untouched. Evidence 2026-11-12: agents saw both antigravity weekly pools
  refuse while aiuse still reported 36%/31% left.

## [4.1.1] - 2026-11-12

### Changed

- Rank actionable subscription allowances by projected unused value per day until
  reset, giving behind-pace allowances approaching reset higher priority. A
  one-day floor bounds urgency; conserve and shared-budget safeguards remain.

### Fixed

- A Claude model cap whose name is not plain (`weekly_model_fable (beta)`, over 32
  characters, control characters) is now kept under the generic model
  `unknown_model` instead of vanishing, so the shared hint still reports a
  model cap. The model-name guard also covers non-`weekly_model_` window ids
  (#52).

## [4.1.0] - 2026-11-10

### Added

- **`tokscale` collector and ledger caching**:
  - File-backed caching and `QueryGate` throttling in `collect_tokscale` to prevent child process stampedes and reuse recent responses within `max_cache_age_seconds` (default 600s).
  - Previous-sample caching in `capture_tokscale_ledger` within `max_cache_age` (default 300s) to avoid re-running `tokscale models` on every snapshot.
  - Collector cache and QueryGate reuse in `aiuse watch` across screen-only and `all_providers` runs.

### Changed

- **Relaxed sampling and freshness cadences for lower CPU and run-queue footprint**:
  - Increased `sampling.burst_interval` from 180s to 300s (5m) to reduce LaunchAgent wakeup frequency.
  - Increased `sampling.active_interval` from 900s to 1800s (30m).
  - Relaxed `analysis.fresh_threshold_seconds` from 1500s to 1800s (30m).
  - Increased `collectors.codexbar.max_cache_age_seconds` from 300s to 600s (10m).
  - Changed default `collectors.openusage_ai.force_refresh` from `true` to `false` so the local service's cached limits are reused without forcing an upstream refresh on every collection.

### Fixed

- Client-limit messages now require fresh quota and client-availability checks
  for the configured native provider instead of recommending a retired ACP
  client. Agent guidance distinguishes historical delegation enforcement from
  current launch admission. Passive ACP log accounting is unchanged.

## [4.0.3] - 2026-11-10

### Fixed

- **`aiuse --available` now says when a Claude model cap is out.** An exhausted
  Fable weekly window made no entry (only usable pools are listed) while the
  shared Claude entry read usable, "without a reported model cap", so a Fable
  dispatch hit the vendor's 429. The shared entry carries `model_sublimits`
  (every cap, with state, `usable_now`, `reason`, its own `available_at`), its hint names the
  exhausted model and its reset, and the text form prints a `NOT usable now`
  line. Additive; schema stays 1.5
  ([`docs/json-contract.md`](docs/json-contract.md#changelog)).

### Changed

- **The matrix's first column is headed `TUI`, not `SERVICE`**, and the OpenCode
  Go row is named `opencode` instead of `oc-go` (`oc-zen` is unchanged). The old
  name still works wherever a provider is named: `--include oc-go`,
  `[disabled_services]`, `[plans]`.
- **DeepSeek is `deepseek/codewhale`**: its row is named for CodeWhale (formerly
  DeepSeek-TUI), the TUI that spends the DeepSeek balance, as `zai/zcode` and
  `clinepass/cline` are. JSON `cli_binary` for `deepseek` is now `codewhale`
  (previously `null`); `codewhale` and `codew` are accepted as provider names.

### Added

- **`omp` rows from omp's own credentials**: collector `omp_agent_db`
  (`[collectors.omp_agent_db]`) now reads two local routes by presence only.
  It keeps the existing Anthropic API-key pool from `~/.omp/agent/agent.db`
  (credential metadata, never the key) and adds an OpenAI API-key route from
  `~/.omp/agent/models.yml` when `providers.openai.apiKey` is a non-empty string,
  the `omp` binary is on PATH, and the same agent directory's `config.yml`
  (or `config.yaml` only when it is absent) does not disable OpenAI. An empty
  primary config still takes precedence. Invalid config fails closed; missing
  both config filenames uses defaults. Each YAML read accepts regular files only (including symlinks),
  open without waiting on FIFOs, and cap reads at 256 KiB. YAML aliases and
  merge keys are rejected before expansion. Explicit default selection ignores
  an inherited legacy-profile directory while preserving custom overrides.
  Invalid active profiles suppress the OpenAI row, including explicit models
  overrides. Automatic `models.yaml` fallback and `models.json` migration remain
  unobserved; an existing models file can be selected explicitly.
  No omp subprocess or key
  resolution occurs. Profile/config-root/agent-directory relocation is honored.
  Project, CLI/runtime and `PI_CONFIG_FILES` overlays are not observed; failing
  key commands and insufficient credits are not detected. Explicit omp source
  pins suppress the independent row. The OpenAI matrix scope
  is `openai-api` with independent pool key `omp_openai`. The OpenAI
  row uses source `omp_models_yml`, `pool_family` `openai_api`,
  `headroom_percent: null`, `state: "unknown"` and models hint `omp TUI - OpenAI
API key (platform credits): openai/gpt-* models`; aiuse never executes or
  emits the configured `apiKey`. A CodexBar grant row still outranks the
  Anthropic key fallback, operator credit overrides replace only
  Claude/Anthropic credit rows, and the Claude shared-window rate gate does not
  apply to the OpenAI route. Schema stays 1.5
  ([`docs/json-contract.md`](docs/json-contract.md#changelog)).
- **`--include LIST` / `--exclude LIST`**: per-run adjustment of the provider
  set without editing `config.toml`. Lists are comma-separated and repeatable;
  entries are provider ids, aliases, display names or globs (`oc-*`) matched
  against the canonical id and display name. `--include` adds providers,
  re-enabling a match that `[disabled_services]` disables; `--exclude` removes
  providers from the set and wins when both match. A run adjusted this way
  is filed as a partial sample (never `latest.json`), watch shows it
  screen-only (its `a` sweep ignores the flags), and `serve` / `sample` reject
  the flags. History summaries (chronic underuse, learned burn, late-cycle)
  skip providers the run leaves out, so they no longer resurface as alerts.
- **`--show-included [LIST]` / `--show-excluded`**: print the resulting
  provider sets as comma-separated ids and exit; a `LIST` previews it as the
  `--include`.

## [4.0.0] - 2026-11-03

### Added

- **Bypass omp rate-gate for active API keys**: inspects `~/.omp/agent/agent.db`
  for active, unblocked `anthropic:api_key` credentials. Claude Code weekly quota
  exhaustion (`claude-shared-window`) only limits Claude OAuth sessions; an API
  key draws from platform credits under standard API tier limits. When an active
  API key is present, `aiuse` bypasses the inferred shared-window gate.
- **`monthly_spend_limit` in `[plans.<provider>]`**: supports declaring an
  organizational budget ceiling for pay-as-you-go providers (e.g. `[plans.pi]
monthly_spend_limit = 100`). Anchors the spend meter in the `MONTH` column
  with accurate percentage used and queue score instead of displaying `n/a`,
  while allowing active credit grants to track remaining cash liquidity in the
  `OTHER` column. Supports per-account sub-tables and alias `spend_limit`.

## [3.14.0] - 2026-11-03

### Added

- **Pi (`pi`, scope `codex-api`)**: independent OpenAI API credit accounting from
  CodexBar's `openai` source (organization spend meter), verified console
  entries (`aiuse set-credits pi`) and optional Costs API estimates
  (`aiuse refresh-credits pi`). It never borrows Codex subscription capacity
  ([`docs/pi-openai-credits.md`](docs/pi-openai-credits.md)). JSON additions are
  listed in [`docs/json-contract.md`](docs/json-contract.md#changelog).
- Manual Linux CI dispatch (`gh workflow run test.yml`).

### Changed

- The provider id `openai` now canonicalizes to `pi` (CodexBar's API costs, not
  its `codex` OAuth subscription source).
- OMP's board scope is now `claude-api` (its stable pool id `omp_claude` and JSON
  `pool_family: "claude"` are unchanged); API scope is kept on
  explanatory/rate-gated rows.
- `aiuse --available` now applies the CLI overrides (`--timeout`,
  `--min-remaining`, `--max-days`, `--no-*`), filters accounts and pools by
  `--providers` or the config provider list, and marks live snapshots partial
  whenever a provider list is set.

### Fixed

- `aiuse set-credits omp` now rejects an equal-or-older reading instead of
  overwriting an established one, and `aiuse set-credits` exits 1 with an
  error instead of printing "recorded" when an equal or newer reading is
  already accepted; already-applied readings can still refresh or expire.
- Credit refreshes preserve sibling API-provider readings, using locked, atomic
  file updates.
- The installed `secretspec.toml` manifest now declares `OPENAI_ADMIN_KEY`, so
  `aiuse refresh-credits pi` can receive it from `sudo-secretspec run` on a
  wheel or Homebrew install.

## [3.13.2] - 2026-11-02

### Fixed

- Narrow terminals trim the OTHER column with `…✂` before dropping PACE
  ([`docs/pretty-display.md`](docs/pretty-display.md)).

## [3.13.1] - 2026-11-02

### Fixed

- **Antigravity (and any provider) readings no longer sit one CodexBar refresh
  behind when `http_url` is set.** CodexBar's HTTP server is
  stale-while-revalidate: the first request after its TTL returns the previous
  snapshot (old `updatedAt`) and refreshes in the background. aiuse recorded
  that as a live query, so the gated reading (and its Gemini 4 Argon window)
  stayed 15-30 min old, past the 25 min stale line, and `acp-dispatch` refused
  Argon with "no fresh reading". A snapshot older than 330 s is now requested
  again until it advances (up to 20 s); if the server is not refreshing, the
  CLI runs live.

### Added

- **`stale_reason`** on stale `--available` pools and accounts (JSON, plus a
  `[stale: ...]` suffix on the summary line): the reading's age against the
  fresh limit and, when a throttled query reused an earlier payload, the
  collector's own note. Additive; schema stays 1.5.

## [3.13.0] - 2026-11-01

### Added

- **Support Copilot weekly (7-day) and session quota windows** in tokscale and
  codexbar collectors, model definitions, and quota refusal records.
- Copilot quota architecture and rate-limit documentation in
  [`docs/copilot-quota.md`](docs/copilot-quota.md).
- GitHub Actions CI workflow running a full Semgrep scan (`p/default`, `--error`,
  excluding `.github`) on push and pull requests.

### Fixed

- **Prevent repeated macOS Keychain prompts from the Muse collector** by
  making Keychain access opt-in via `AIUSE_MUSE_KEYCHAIN=1` and bypassing quota
  probes when subscription status is inactive (`subs_active: false`).
- Scope local Semgrep hook in `.pre-commit-config.yaml` to `stages: [pre-push]`
  with changed files only and exclude `.github/`, avoiding whole-repo scan CPU
  spikes on routine commits.

## [3.12.1] - 2026-11-01

### Changed

- **Bedrock shows the total AWS account spend so far**, not month-to-date:
  `↑ $25.66 AWS total (as of …)`. The Cost Explorer query now spans the 13
  whole months before the current one plus the current month, which is as far
  back as the API serves. The new metadata field `bedrock_billing.since_month`
  names the earliest month with records
  ([`docs/bedrock-quota.md`](docs/bedrock-quota.md)).
- The Cursor on-demand remainder reads `↓$0.00 cap left` instead of
  `↓$0.00 prepaid`: it is a spend cap, not a wallet bought ahead. Other
  providers' prepaid wallets keep `prepaid`.

### Fixed

- Bedrock no longer shows `$0.00` when Cost Explorer has no cost records.
  AWS answers an empty period with `Amount: "0"`, most visibly on the first day
  of a month, and aiuse read that as a measured zero. An empty window is now
  `UNKNOWN`; a real zero-amount record is still a measured zero.
- The Bedrock cache logic no longer fabricates a `$0.00` row from an old
  `UNKNOWN` store or from a read that returned no rows; both stay unknown.

## [3.12.0] - 2026-11-01

### Added

- Gemini 4 Argon has its own `gemini_argon` pool family; JSON schema 1.5
  publishes the model-to-family map.

## [3.11.0] - 2026-10-29

### Fixed

- Bedrock shows overall AWS account month-to-date charges before credits,
  with no category breakdown, and keeps its collector source pin.
- omp grant pace is preserved, and Bedrock credits are separated from spend.
- The release script smokes the installed `watch` before other probes.

## [3.10.5] - 2026-10-29

### Fixed

- An unknown Bedrock total shows as `UNKNOWN`.

## [3.10.4] - 2026-10-29

### Added

- Dollar direction is named (`↓` left, `↑` spent), the header is compacted,
  and Bedrock Cost Explorer reads are gated (one billed read per six hours,
  and only after other usage moves).

### Fixed

- omp's monthly credit is paced from dollars left and the reset time.
- OpenCode Go and Zen share one vault read when collected together.

## [3.10.3] - 2026-10-29

### Fixed

- OpenCode: every Go API key is read for Go windows and the Zen balance.
- `watch` leaves the tty blocking so the first frame paints, and ignores
  trackpad scroll sequences instead of quitting.

## [3.10.2] - 2026-10-29

### Added

- CodexBar usage is read from its HTTP server when it answers `/health`;
  the CLI is the fallback.

### Fixed

- OpenCode Go sizes its monthly window from `access.startsAt`.
- Matrix note-only rows start at the first column and show oc-go on the sweep.
- History counts snapshots by filename, which shortens `watch` start.

## [3.10.1] - 2026-10-28

### Added

- SIPB collector option `ignore_expired_certificates`.

### Fixed

- Uninstalled optional collectors are expected in the `watch` footer.
- Frozen-quota detection scans each candidate's own span.
- Matrix rows pass the reference timestamp to window-clock inference.

## [3.10.0] - 2026-10-23

### Added

- ClinePass multi-account: one labelled row per account, with no false
  disagreement ([#41](https://github.com/djbclark/aiuse/issues/41)).

## [3.9.4] - 2026-10-22

### Added

- The omp rate gate is a durable fail-closed latch: evidence persists in
  `~/.local/state/aiuse/rate-gates.json`, Anthropic 429 retry-after is read
  from omp's own logs, and `aiuse gate status|clear` inspects it
  ([`docs/omp-cloud-credits.md`](docs/omp-cloud-credits.md)).

### Fixed

- The `watch` `a` sweep leaves deprecated collectors at their configured state.

## [3.9.3] - 2026-10-22

### Fixed

- A rate-gated omp summary line says `GATED`, and the gated omp row is stored
  in the snapshot cache.

## [3.9.2] - 2026-10-22

### Changed

- The default install is cswap, CodexBar and tokscale. caut, caam, OpenUsage.ai
  and OpenUsage.sh are deprecated for overlap; their code stays and still runs
  when the binary is present and the collector is enabled
  ([`docs/collectors-caut-openusage.md`](docs/collectors-caut-openusage.md)).

### Fixed

- An Antigravity source that still reports Gemini is kept.
- Windowless status rows are kept when a pool is hidden.

## [3.9.1] - 2026-10-21

### Added

- `paseo` collector: the Paseo daemon's cached usage report as a second source.

### Fixed

- A rate-gated omp matrix row is empty, not a spendable percent.
- Matrix OTHER shows omp dollars left only.

## [3.9.0] - 2026-10-21

### Added

- omp rate gate on the shared Claude account window (JSON schema 1.4), mirrored
  onto snapshot accounts so `--for-chat` shows it.

### Fixed

- Stale-window hygiene: reset-passed cached windows are dropped, old cswap
  hydration expires, and `watch` survives a `brew upgrade`.

[5.1.3]: https://github.com/djbclark/aiuse/compare/v5.1.2...v5.1.3
[Unreleased]: https://github.com/djbclark/aiuse/compare/v5.3.2...HEAD
[5.3.2]: https://github.com/djbclark/aiuse/compare/v5.3.1...v5.3.2
[5.3.1]: https://github.com/djbclark/aiuse/compare/v5.3.0...v5.3.1
[5.3.0]: https://github.com/djbclark/aiuse/compare/v5.2.1...v5.3.0
[5.2.1]: https://github.com/djbclark/aiuse/compare/v5.2.0...v5.2.1
[5.2.0]: https://github.com/djbclark/aiuse/compare/v5.1.3...v5.2.0
[4.1.1]: https://github.com/djbclark/aiuse/compare/v4.1.0...v4.1.1
[4.1.0]: https://github.com/djbclark/aiuse/compare/v4.0.3...v4.1.0
[4.0.3]: https://github.com/djbclark/aiuse/compare/v4.0.0...v4.0.3
[4.0.0]: https://github.com/djbclark/aiuse/compare/v3.14.0...v4.0.0
[3.14.0]: https://github.com/djbclark/aiuse/compare/v3.13.2...v3.14.0
[3.13.2]: https://github.com/djbclark/aiuse/compare/v3.13.1...v3.13.2
[3.13.1]: https://github.com/djbclark/aiuse/compare/v3.13.0...v3.13.1
[3.13.0]: https://github.com/djbclark/aiuse/compare/v3.12.1...v3.13.0
[3.12.1]: https://github.com/djbclark/aiuse/compare/v3.12.0...v3.12.1
[3.12.0]: https://github.com/djbclark/aiuse/compare/v3.11.0...v3.12.0
[3.11.0]: https://github.com/djbclark/aiuse/compare/v3.10.5...v3.11.0
[3.10.5]: https://github.com/djbclark/aiuse/compare/v3.10.4...v3.10.5
[3.10.4]: https://github.com/djbclark/aiuse/compare/v3.10.3...v3.10.4
[3.10.3]: https://github.com/djbclark/aiuse/compare/v3.10.2...v3.10.3
[3.10.2]: https://github.com/djbclark/aiuse/compare/v3.10.1...v3.10.2
[3.10.1]: https://github.com/djbclark/aiuse/compare/v3.10.0...v3.10.1
[3.10.0]: https://github.com/djbclark/aiuse/compare/v3.9.4...v3.10.0
[3.9.4]: https://github.com/djbclark/aiuse/compare/v3.9.3...v3.9.4
[3.9.3]: https://github.com/djbclark/aiuse/compare/v3.9.2...v3.9.3
[3.9.2]: https://github.com/djbclark/aiuse/compare/v3.9.1...v3.9.2
[3.9.1]: https://github.com/djbclark/aiuse/compare/v3.9.0...v3.9.1
[3.9.0]: https://github.com/djbclark/aiuse/compare/v3.8.0...v3.9.0
