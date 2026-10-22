# aiuse

[![PyPI](https://img.shields.io/pypi/v/aiuse.svg)](https://pypi.org/project/aiuse/)
[![Python](https://img.shields.io/pypi/pyversions/aiuse.svg)](https://pypi.org/project/aiuse/)
[![Tests](https://github.com/djbclark/aiuse/actions/workflows/test.yml/badge.svg)](https://github.com/djbclark/aiuse/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/pypi/l/aiuse.svg)](LICENSE)

**Your AI subscriptions reset unused capacity you already paid for.** Claude,
Codex, Copilot, Cursor, Grok, Gemini, OpenCode, and more each carve your usage
into 5-hour / weekly / monthly windows — and whatever you don't burn by the
reset is just gone. `aiuse` polls every plan you have from one terminal
command and tells you, in plain language, what to use **right now** before
it resets, and what's about to run out early so you can pace yourself.

CLI command: **`aiuse`** (stub **`ai`** → same entrypoint)

## Which vendor CLI is which

Every report line's vendor name is grep-able (case-insensitive, substring OK)
for the actual agent CLI whose quota it tracks:

| Vendor                      | CLI tracked                   |
| --------------------------- | ----------------------------- |
| Claude                      | `claude`                      |
| Codex                       | `codex`                       |
| Cursor                      | `cursor-agent`                |
| GitHub Copilot              | `copilot`                     |
| Grok / SuperGrok            | `grok` (Grok Build)           |
| Gemini / Google Antigravity | `agy`                         |
| OpenCode (Go + Zen)         | `opencode`                    |
| Z.ai                        | `zcode`                       |
| QwenCloud / Alibaba Bailian | `qwen` (Qwen Code)            |
| Muse (Meta)                 | `muse`                        |
| Devin                       | `devin`                       |
| ClinePass                   | `cline`                       |
| DeepSeek, OpenRouter        | no CLI — per-token $ API only |

`aiuse --json` exposes this same mapping per account row as `cli_binary`
(`null` when there is no local CLI) — see
[`docs/json-contract.md`](docs/json-contract.md). Note the trap this table
exists for: the _quota_ CLIs (`qwencloud`, `bl`, `caut`, `caam`, `tokscale`,
`openusage`, `cswap`, `codexbar`) measure usage; they are not how you burn it.

## See it in action

This is real `aiuse` output from synthetic demo accounts, not a screenshot.
It's the exact table printed by plain `aiuse`, read **bottom → top**: the
pools most worth using right now sort to the bottom.

<!-- readme-demo:start -->

```diff
  AI: Use `aiuse --json` for machine-readable output
        ## SERVICE    SCOPE      PACE      5H              WEEK              MONTH
- error ?? oc-zen     —             —                                                        No available fetch strategy for opencode-zen.
- empty  0 oc-go      —             —      ->           100u/0l/4d12h         <-
  n/a   -- deepseek   —             —                                                        $4.15 (counts down)
  n/a   -- openrouter —             —                                                        $18.55 (counts down)
  slow  38 agy        claude/gpt    —      ->           78u/22l/1d5h          <-
  mid   50 claude     —          +38% 12u/88l/3h7m -68% 77u/23l/3d2h +38%     <-
  mid   50 grok       —             —      ->            6u/94l/6d14h         <-
  mid   63 codex      —          -20%      ->           54u/46l/2d7h -20%     <-
  mid   66 cursor     total      -20%      ->               ->             71u/29l/3d7h -20%
  mid   66 agy        gemini     -63%  4u/96l/4h5m -78% 16u/84l/4d -63%       <-
+ use   91 copilot    —          -39%      ->               ->             58u/42l/~1d -39%
                       2d14h = until this clock resets · bold = largest unit
      -> / <- in an empty clock points at this row's window; a hole points at the earlier one
                 u/l = percent used / percent left — l is what you can still spend
  +N%/-N% = pace: ahead (+) or behind (-) of an even spend across the window · PACE = the row’s highest · ~ = early: under a tenth of the window has elapsed
```

<!-- readme-demo:end -->

Every row is measured on the same three clocks, so a column reads top to
bottom. An arrow in an empty clock points at the window that row does have:
`->` when it is to the right, `<-` when it is to the left, including a hole
between two windows, which points at the earlier one. An em-dash in a clock
cell means the row has no windows to point at.
Each cell displays `used% u / left% l / reset_span pace%`: **`u/l` = percent used /
percent left** (`l` is what you can still spend). For example, `12u/88l/3h7m -68%`
means 12% consumed, 88% remaining, resetting in 3 hours 7 minutes, and burning 68%
slower than an even spend across the window.

A sub-day rolling window (the 5-hour kind) that has not been used yet sends no reset
time — its clock starts on first use — so its cell shows the window's full length and a
`↻` marker instead of a countdown, e.g. `0u/100l/5h↻`. Unused windows of a day or
longer get no span. The legend line `↻ = clock starts on first use` appears
only when at least one such cell is on the board. The marker is screen-only; `--json`
is unchanged.

An in-use window whose source stops sending its reset time (CodexBar sends none for
Antigravity's 5-hour windows; OpenUsage does) keeps its countdown: `aiuse` carries forward
the last reset an earlier reading measured for that window, from any source, while that
reset is still ahead and the window's usage has not fallen since (a fall means it reset).
A carried time is an estimate, so it renders with `~` like any estimated reset (`~3h`),
and `--json` marks it with `resets_at_carried_from`
([`docs/json-contract.md`](docs/json-contract.md)). A window that has not been used yet
is never carried: its reported reset only slides with every query.

### Pace and the PACE column

- **Window pace %** (`+N%` / `-N%`) compares consumption against an even spend across the
  window (`round((pace_ratio - 1) * 100)` with `pace_ratio = used_fraction / elapsed_fraction`;
  elapsed floored at 5%). `+` means burning faster than an even spend (will run out early);
  `-` means burning slower (capacity will remain at reset). Early in a window (under 10%
  elapsed), readings are noisy and marked with `~` (e.g. `+1351%~`). Exhausted windows and windows
  with no reset time leave pace blank.
- **`PACE` column** reports the row's **overall pace** = the highest window pace in the row
  (the window furthest ahead of spend that will run out first), marked with `~` if that highest
  window is early. It shows an em-dash (`—`)
  when no window has a pace reading or when **any** window in the row is exhausted (since the
  row is locked out until that reset).
- **Column alignment**: all free text notes (`<-`, prepaid wallet, prepaid notes)
  appear after all fixed columns so columns align on every row. Narrow terminals shed `PACE` first,
  then individual cell pace suffixes.

The colored `##` score is the action queue from `0` (empty) to `99` (use as
soon as possible). Its action-state boundaries are contiguous: `slow` ends at
49, `mid` runs from 50–74, and an active `use` recommendation starts at 75.
`??` means usage could not be fetched; `--` means rolling prepaid/PAYG
inventory has no use-or-lose urgency.

Inside each live band (`slow` 25-49, `mid` 50-74, `use` 75-99) the score is how
much money is at risk in the pool's window, log-scaled onto the band's range (6
points per decade, $0.01 at the band's floor to $100 at its ceiling). A window is
priced from the plan's `monthly_price` in `[plans.<provider>]`, spread over the
window's refills per waking month (16 waking hours a day by default, so a 5-hour
window is 1/97 of the month and a weekly one about a third) and scaled by the
plan's `value_multiplier`. Two figures come out of that price:

- **Capacity** is what is still unspent now: the refill's value times the percent
  still left — the figure `--json` reports as `value_at_risk_usd`. `slow` rows rank
  by capacity, so 5% left of a $200 plan's weekly window ($3.45) outranks 1% left
  of a $20 one ($0.07), and a weekly window outranks the same percent of a 5-hour
  one.
- **Projected waste** is what the window's pace says will still be unspent when it
  resets: the pace's `projected_waste_fraction` times a full refill (for a priced
  account, `--json`'s `pace.projected_waste_usd`). `mid` and `use` rows rank by
  it, so an imminent reset with real waste outranks a big window with days left: a
  $20 Claude plan's 5-hour window with 20 minutes to go and two thirds of it
  unspent ($0.20) outranks a weekly window that is exactly on pace ($0), though
  that weekly window holds $3.45. A window with no pace to project (no reset time)
  keeps its capacity, the most it could lose. A window the analyzer raised no
  alert for is paced by the board the same way.

The band's older signal only breaks ties: recovery speed in `slow`, the analyzer
score in `mid` and `use`. A window whose length is neither reported nor named in
its label has no refill to price and sits at its band's floor. Plan tier is not
detected, so each provider defaults to its lowest tier: set
`[plans.claude] monthly_price = 200` for Max, or
`[plans.claude."me@x.com"] monthly_price = 200` for one account (the account value
wins). For ordering, a provider with no price is valued at the cheapest configured
plan; it still shows no dollar figure.

`aiuse suggest`, the JSON `suggestion` and the `use:` head of `aiuse status` name
the burn the board ranks last — the bottom `use` row, else the bottom `mid` row a
burn alert backs — not the loudest analyzer score.

Green (`use`) is capacity to burn now before it resets; red is capacity
already lost (`empty`, including a zero/negative prepaid balance) or a source
that failed to fetch (`error`); everything else is informational (`n/a`
positive prepaid balances that never expire, `slow`
windows you should pace yourself on, `mid` windows on track — nothing to do).

Services are named for the CLI you actually type, so the name doubles as
something to grep for. The two OpenCode services are the exception — they
abbreviate to an `oc-` family prefix (`oc-go`, `oc-zen`) to stay narrow.

## Try it in 60 seconds

```bash
pipx install aiuse
aiuse doctor   # see which data-source tools are already on your PATH
aiuse          # usage table for everything aiuse can see right now
```

No config file is required to start — `aiuse doctor` tells you exactly which
of the optional data-source tools ([below](#data-sources)) it found, and
`aiuse` works with however many you already have installed. See
[Install](#install) below for Homebrew, git-tip, and editable-dev options.

> **AI agents:** start at [`AGENTS.md`](AGENTS.md) for a map of this repo,
> active priorities, and contributor guidance.

## Data sources

| Tool                                                                                                      | Purpose                                                    | Authority                                                                                                                           |
| --------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| [**cswap**](https://github.com/realiti4/claude-swap) `cswap list --json`                                  | Live Claude Code quota for every configured email/account  | Canonical multi-account Claude source                                                                                               |
| [**CodexBar**](https://github.com/) `codexbar usage --format json`                                        | Live quotas and balances for enabled providers             | Preferred for non-Claude providers; keep its Claude source disabled on macOS until its Keychain prompting is fixed                  |
| [**caut**](https://github.com/Dicklesworthstone/coding_agent_usage_tracker) `caut usage --json`           | Independent multi-provider usage (CodexBar-class probes)   | **Deprecated** (overlap with cswap, CodexBar, tokscale). Still runs if installed. Happy to re-add support.                          |
| [**caam**](https://github.com/Dicklesworthstone/coding_agent_account_manager) `caam limits --format json` | Vault-profile limits for claude / codex / grok / cursor    | **Deprecated** (overlap; vault echo behind every direct source). Still runs if installed. See [`docs/caam.md`](docs/caam.md).       |
| [**OpenUsage.ai**](https://www.openusage.ai/) `openusage` / `127.0.0.1:6736/v1/limits`                    | Quiet macOS menu-bar companion + live limits API           | **Deprecated** (overlap with cswap, CodexBar, tokscale, native OpenCode Go). Collector key `openusage_ai`. Still runs if installed. |
| [**OpenUsage.sh**](https://openusage.sh/) `openusage-sh export --output - --format json`                  | Terminal dashboard, local telemetry, and quota export      | **Deprecated** (overlap). Collector key `openusage_sh`. Still runs if installed.                                                    |
| [**tokscale**](https://www.npmjs.com/) `tokscale usage --json`                                            | Independent live subscription quota measurement            | Cross-checked against peers; preferred for Copilot; fill-in when others lack a live row                                             |
| [**paseo**](https://paseo.sh/) `paseo usage ls --json`                                                    | Per-account quota windows + balances from the Paseo daemon | Second source behind the direct collectors (claude/codex/copilot/grok/antigravity); see [`docs/paseo.md`](docs/paseo.md)            |

This project shells out to tools already on your `PATH` (and optionally hits
OpenUsage’s loopback API); it does not scrape billing dashboards itself. For
more on collector setup, reliability, and source selection, see the
[documentation index](docs/index.md).

### macOS source policy (current)

Use **cswap** as the Claude source and **CodexBar** for every other provider.
Keep CodexBar's Claude source disabled, even as a fallback. **tokscale** and
paseo are the cross-check sources that stay supported. **caut**, **caam**,
**OpenUsage.ai**, and **OpenUsage.sh** are deprecated because they overlap
that set; the code still runs when the tool is installed, and support comes
back if someone asks. All collectors are enabled by default, whether installed
now or prepared for later; disable any source in `config.toml` when it is not
appropriate for your machine. On macOS, keep CodexBar's Claude integration
disabled while its Keychain-prompting bug is open.

## Install

**End users (pipx):**

```bash
pipx install aiuse
# or from git tip:
# pipx install 'git+https://github.com/djbclark/aiuse.git'
aiuse doctor
```

(`ai` is installed as a stub that runs the same CLI.)

**Homebrew (personal tap):**

```bash
brew tap djbclark/aiuse
brew trust djbclark/aiuse
brew install aiuse
```

**Developers (editable):**

```bash
cd /path/to/aiuse
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Optional config (standard location: **`~/.config/aiuse/`**, or `$XDG_CONFIG_HOME/aiuse/`):

```bash
# Create the canonical config.toml (never overwrites)
aiuse --generate-config

# Or copy the example by hand:
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/aiuse"
cp config/config.example.toml "${XDG_CONFIG_HOME:-$HOME/.config}/aiuse/config.toml"
```

| File                          | Purpose                                                                     |
| ----------------------------- | --------------------------------------------------------------------------- |
| `~/.config/aiuse/config.toml` | All user settings: collectors, plans, analysis, timeouts, and macOS options |

`aiuse --generate-config` creates missing parent directories and writes the TOML
starter without overwriting it. Provider credentials stay with cswap, CodexBar,
caut, OpenUsage, and tokscale — this file does not hold tokens or emails.

### Optional OpenCode Zen balance

OpenCode Zen prepaid balance is a distinct service from OpenCode Go quota.
CodexBar can report it, and `aiuse` can independently cross-check the live
OpenCode billing response when a signed-in browser session is available. This
is opt-in and normal collection never opens a browser or reads its cookie
database.

```bash
# One-time optional browser reader for a pipx install.
pipx inject aiuse browser-cookie3

# Validate the OpenCode session, then save only the validated cookie through
# SecretSpec. The default manifest is ~/.config/aiuse/secretspec.toml.
aiuse credential refresh opencode-zen --from chrome --profile Default
```

The command reads only `opencode.ai` cookies, validates an authenticated
workspace and a live Zen balance before saving, and never prints the cookie.
Use `--dry-run` to validate without saving. `aiuse credential refresh --help`
lists its confirmation, profile, timeout, and manifest options. See the
[OpenCode Zen balance guide](docs/opencode-zen-balance.md) for the source model
and SecretSpec details.

aiuse normally unifies source-local account names automatically when each
identifying source reports exactly one account for a provider. For a genuinely
multi-account provider, map a local name explicitly instead of letting aiuse
guess:

```toml
[account_aliases.codex.openusage_sh]
"codex-cli" = "me@example.com"
```

`aiuse --full` prints this exact table when a multi-account source cannot be
matched safely. Explicit mappings take precedence over automatic normalization.

Some vendors rate-limit quota probes. aiuse reads agy's (Antigravity's) quota
at most once every 15 minutes across every aiuse process (CLI, LaunchAgent,
`watch`, `serve`), reusing the last reading in between. Tune or disable it per
provider, in seconds. See
[Antigravity pools: query rate limit](docs/antigravity-pools.md#query-rate-limit-2026-10-05):

```toml
[query_min_interval]
antigravity = 900 # 0 disables
```

## Daily workflow

```bash
# Once: create defaults under ~/.config/aiuse/ (never overwrites)
aiuse --generate-config
aiuse doctor                 # PATH tools + config presence + timeouts
# Once per machine: install the data-source tools you intend to use
# (normally cswap, CodexBar, and tokscale; caut, caam, and both OpenUsage tools are deprecated)
./packaging/install-deps.sh
# or: just -f ~/ops/site-djbclark/justfile install-aiuse-deps
# macOS: follow the trust guide from the documentation index when needed
aiuse trust setup            # caut: stable codesign + guide
aiuse trust sign-caut        # re-run after every cargo install
aiuse trust fix-codexbar-cache --dry-run   # CodexBar#679: trust CLI on cache items

# Optional: refresh the separately reported OpenCode Zen prepaid balance.
aiuse credential refresh opencode-zen --from chrome --profile Default

# Morning / before a long coding block
aiuse                        # usage table on stdout (use-soon at bottom); meta on stderr
aiuse --full                 # long report: per-provider, tips, History, detailed plan
aiuse --brief                # same as default (compat alias)
aiuse --no-tui               # classic plain-text report (also used when piping)
aiuse -q                     # table only (no stderr meta / Collecting…)
aiuse suggest                # single best burn pool (or “nothing urgent”)
aiuse status                 # one line for shell prompts / status bars
aiuse prompt                 # synonym of status

# Shell completion (bash or zsh)
eval "$(aiuse --print-completion bash)"
# or: source completions/ai.bash

# Tighter thresholds for “only what resets soon”
aiuse --min-remaining 50 --max-days 7
```

## Usage

```bash
# Pretty human-readable report (default)
aiuse
aiuse --format pretty
aiuse --no-color          # plain text, no ANSI
aiuse -q / --quiet        # suppress progress on stderr

# or without install:
PYTHONPATH=src python -m ai

# Machine-readable JSON on stdout (progress on stderr unless -q)
aiuse --json
aiuse --format json
aiuse --json --alerts-only
aiuse suggest --json
aiuse --save ~/tmp/ai-snapshot.json   # also write JSON file

# Decision helpers
aiuse suggest              # single best burn pool
aiuse status               # one line for prompts / status bars
aiuse prompt               # synonym of status
aiuse serve                # loopback HTTP API for agents
aiuse watch                # full-screen board (q/esc quit · u update now · a toggle all-providers sweep; default 10m)
aiuse watch -i 2m          # faster refresh
aiuse watch --once         # one frame on stdout (scripts / tmux)
aiuse watch --all-providers  # OPERATOR-ONLY: live board of every provider, screen only (see below)
aiuse attribute            # what spent the quota: points burned beside tokens per client
aiuse attribute --since 7d --provider clinepass --intervals
aiuse sample               # scheduled entry point: samples faster while quota is moving

# Faster / partial
aiuse --providers copilot,grok,codex   # query these separately
aiuse --no-tokscale
aiuse --min-remaining 50 --max-days 10

# Subprocess timeout for external tools (default 45s; also in config.toml)
aiuse --timeout 45
aiuse -t45

# Environment check (tools on PATH, config files, timeouts) — no usage collection
aiuse doctor
aiuse --doctor
```

## Programmatic use and AI orchestration

`aiuse` turns the many local authenticated quota sources into one normalized,
machine-readable snapshot. An agent can call it once instead of opening billing
pages, finding credentials, or independently interpreting each provider’s reset
bars — saving tokens and avoiding inconsistent conclusions.

```bash
# Full live snapshot, ranked alerts, and the best next action.
aiuse -q --json

# Only actionable alerts for a short agent/tool call.
aiuse -q --json --alerts-only

# The single best pool to use next, or null when there is nothing urgent.
aiuse suggest --json
```

JSON is written to stdout; `-q` keeps progress off stderr. Exit `0` means a
successful run with no burn/conserve alert, `1` is a hard collection failure,
and `2` is a successful run with an actionable alert. The stable fields and
exit-code contract are in [`docs/json-contract.md`](docs/json-contract.md).

For a long-lived local agent, `aiuse serve` provides the same decisions through
a loopback HTTP API; see the [documentation index](docs/index.md#automation-and-agents).

> **`aiuse watch --all-providers` (and the watch `a` key) is operator-only.**
> AI agents and automation must not run it without explicit permission from
> the human operator. It polls every provider, deliberately overriding
> `[disabled_services]`, `[hidden_pools]`, `analysis.excluded_pools`, hidden
> ceiling/total rows, and collector disable flags — providers are disabled
> for reasons (vendor rate limits, cost, opt-outs) that this sweep ignores.
> Its output is display-only: nothing is written to snapshots, the token
> ledger, or sampler state, though the shared cross-process query throttle
> still applies so the sweep cannot hammer a vendor.

Projects that want the ranking logic without depending on this CLI can reuse
the language-neutral [shared quota semantics](docs/shared-quota-semantics/):
JSON Schemas, enums, pace rules, and golden fixtures are designed to be copied
or validated from any language. `aiuse` uses those fixtures itself, but they do
not require importing its Python package.

| Flag                                                                                                                   | Effect                                                                                                                                                                                                                                                                                            |
| ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| _(none)_ / `--format pretty`                                                                                           | Clock matrix on stdout (empty→n/a→slow→mid→use); meta on stderr; plain when piped                                                                                                                                                                                                                 |
| `--full`                                                                                                               | Long pretty report: per-provider, cross-checks, tips, History, detailed plan                                                                                                                                                                                                                      |
| `--json` / `--format json`                                                                                             | Full snapshot + alerts + `suggestion` + `history` as JSON (schema **1.4**: `state`, `usable_now`, `pool_family`, `role`, `effective_left_percent`, `hidden_pools`, pace fields, `summary_lines`, `semantics`, `rate_gated_by`)                                                                    |
| `--available` / `--live`                                                                                               | Routing shortlist: only pools usable **now**, per vendor + model family, sorted by headroom; reads the cache by default, `--live` forces a collect                                                                                                                                                |
| `usage-sources` / `--usage-sources`                                                                                    | Which quota tools and ACP context logs are active for each vendor. No collect, no ACP turn. `[usage_sources]` pins one source; the default still blends. See [`docs/acp-usage.md`](docs/acp-usage.md).                                                                                            |
| `--brief`                                                                                                              | Alias of the default pretty report                                                                                                                                                                                                                                                                |
| `--no-tui`                                                                                                             | Force classic plain-text pretty report                                                                                                                                                                                                                                                            |
| `--no-color`                                                                                                           | Disable ANSI colors in plain-text pretty mode                                                                                                                                                                                                                                                     |
| `-q` / `--quiet`                                                                                                       | Suppress progress messages on stderr                                                                                                                                                                                                                                                              |
| `--alerts-only`                                                                                                        | Recommendations only (respects pretty vs json)                                                                                                                                                                                                                                                    |
| `suggest` / `--suggest`                                                                                                | Single best burn pool (or nothing urgent); pair with `--json` for structured                                                                                                                                                                                                                      |
| `status` / `prompt` / `--status`                                                                                       | One-line status for shell prompts / status bars                                                                                                                                                                                                                                                   |
| `serve` / `--serve`                                                                                                    | Loopback HTTP API for agents (`127.0.0.1`)                                                                                                                                                                                                                                                        |
| `--port` / `--max-age`                                                                                                 | Serve bind port (default **28787**; fails loudly if a non-aiuse server holds it) and snapshot cache max age                                                                                                                                                                                       |
| `--print-completion bash\|zsh`                                                                                         | Print shell completion script to stdout                                                                                                                                                                                                                                                           |
| `--no-<collector>` flags: tokscale, cswap, codexbar, caut, openusage-ai, openusage-sh, muse, qwencloud, bailian, paseo | Skip specific collectors                                                                                                                                                                                                                                                                          |
| `--providers copilot,grok`                                                                                             | Query specific CodexBar providers (CSV, one per subprocess)                                                                                                                                                                                                                                       |
| `-t` / `--timeout SECONDS`                                                                                             | Force subprocess timeout for all external tools (default **45**)                                                                                                                                                                                                                                  |
| `--generate-config`                                                                                                    | Write default `~/.config/aiuse/config.toml`; never overwrites existing                                                                                                                                                                                                                            |
| `--show-config-path`                                                                                                   | Print the active config path                                                                                                                                                                                                                                                                      |
| `watch` / `--watch` `-i SECONDS` `--once`                                                                              | Full-screen board; keys: `q`/esc quit, `u` collect now (bypasses the cached snapshot), `a` toggle all-providers sweep (shows hidden/ceiling/excluded rows; press `a` again for normal view). No collect ever overlaps another (`a` queues behind a running refresh); `u`/`a` share a 2min minimum |
| `watch --all-providers`                                                                                                | **Operator-only** live board: every provider incl. `[disabled_services]`, `[hidden_pools]`, exclusions, ceiling rows, and disabled collectors — refreshes on the watch interval, **nothing recorded** (no snapshot/ledger/sampler writes; query throttling still applies)                         |
| `doctor` / `--doctor`                                                                                                  | Check tools on PATH, config presence, effective timeouts; no collect                                                                                                                                                                                                                              |
| `note-exhausted <provider> [--family F] --resets-in 4h53m\|--resets-at ISO [--reason T]`                               | Record an agent-reported exhaustion override (advisory, expires at reset; honoured by `--json`/`--available` labelled `source: agent-reported`)                                                                                                                                                   |
| `trust` …                                                                                                              | macOS: codesign status, sign caut, and Keychain grant guide                                                                                                                                                                                                                                       |
| `credential refresh opencode-zen`                                                                                      | Validate a Chrome OpenCode session and save it through SecretSpec                                                                                                                                                                                                                                 |
| `--min-remaining 50 --max-days 10`                                                                                     | Override alert thresholds                                                                                                                                                                                                                                                                         |
| `--save PATH`                                                                                                          | Also write full JSON snapshot to PATH                                                                                                                                                                                                                                                             |

### Exit codes

| Code  | When                                                                                                                                                                                                                               |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **0** | Collect succeeded (or nothing to report) and there are **no** burn/conserve alerts. INFO-only notes still count as 0.                                                                                                              |
| **1** | Hard failure: collectors reported errors and **no** accounts were collected. Also used by `aiuse doctor` when an **enabled** tool is missing from `PATH`, and by `--generate-config` when nothing was written / overwrite refused. |
| **2** | Collect succeeded and at least one **burn** or **conserve** alert is present. Cross-check disagreements alone do **not** set 2. Bad `--timeout` values also use 2.                                                                 |
| **3** | `--available` only: the run succeeded but zero pools are usable right now (everything measured is exhausted or unknown). The data is fine; the quota is not.                                                                       |

`aiuse doctor` checks config file presence, **config validation** (unknown keys, bad
timeouts, dead plan aliases), all seven data-source tools on `PATH` (and OpenUsage.ai
loopback HTTP when the CLI is missing), and a light **version probe**
(`cswap --version`, `codexbar -V`, `caut --version`, `caam --version`, `tokscale --version`). It does
**not** call usage APIs or verify login sessions.

## What “use it or lose it” means

Most **subscription** coding plans (Claude Pro/Max, ChatGPT Plus/Codex, Cursor, Copilot, SuperGrok, Google AI Pro, …) grant **windows** of usage (5-hour, weekly, monthly). When the window resets, **unused capacity disappears** — you still paid for the month.

This tool:

1. Pulls **remaining %** and **reset times** from **cswap** (Claude multi-account), **CodexBar** (broad live quotas), and **tokscale** (independent measurement; preferred for Copilot). **caut**, **caam**, **OpenUsage.ai**, and **OpenUsage.sh** still cross-check when installed; support for those four is deprecated because they overlap the sources above ([`docs/collectors-caut-openusage.md`](docs/collectors-caut-openusage.md)).
2. Scores windows with **pace-based** logic (default): compare how far through the cycle you are vs how much you've used, then project waste or early lockout.
3. Classifies each window as **Burn** (will leave capacity unused), **Conserve** (on track to exhaust before reset — slow down), or **On pace** (no alert).
4. For **shared-allotment** providers (Claude, Gemini by default), scores the longest governing window only so a fresh 5-hour bar does not outrank the weekly budget it draws from. Cursor **total** is the monthly ceiling; **auto** and **3rd-party** are breakdown rows (visible, not headline-scored). **On-demand** is prepaid dollars after the last column, not a percent row. **grok-bot** is its own family. Claude **Fable** is its own scored row (scope `fable`, weekly cell, Fable-cap pace): on Max and premium seats it can use at most half of the shared weekly pool, and that is not extra quota. The score is Fable's own and is not added into a cross-vendor total. A cap with little left is `slow` at the bottom of that band; a spent shared window makes the Fable row `empty` too. Fable at 100% does not exhaust other Claude models. Sonnet and Opus are not separate rows.
5. Qualifies **Conserve** alerts with **overage awareness**: if the account has a real or config-confirmed overage/extra-usage wallet (Claude's usage credits, Cursor's on-demand balance, or a manually confirmed OpenCode Go overage state), the message says so — the real risk there is unplanned $ spend, not lockout, which is a different situation from a genuinely hard ceiling.
6. Default pretty output is a **clock matrix** on stdout — one row per account/pool, one column per reset clock (5H/WEEK/MONTH) plus a **PACE** column for the row's highest pace, each cell displaying `used% u / left% l / reset_span pace%` (`75u/25l/4h -10%`, `89u/11l/2d14h +180%`; depleted → prepaid n/a → conserve → mid → use-soon at bottom; read bottom→top). A row gated for AI agents shows `(AI: Do not use without asking operator)` across its empty 5H/WEEK cells; JSON `requires_operator_approval` / `ai_notice` (no row is gated since 2026-10-19: omp's grant, the Claude Max plan's monthly API credits, is a normal tier; its JSON carries `credits_label: "Claude API credits"` and the row keeps the name `omp` for tools that look it up). Meta goes to stderr. Use `aiuse --full` for per-provider detail.
7. On `--full`, keeps the trailing plan within ~**23 lines × console width** when possible; if the detailed plan is taller, both detailed and **at a glance** are printed (glance last). Forecast fragments (`~lockout Sat 3:00pm, 3 days before reset`, projected waste %) appear on table/status lines when pace can project them; every lockout / run-out forecast also says how far ahead of the window's reset it lands, scaled to hours, days or weeks (JSON: `pace.exhaustion_lead_seconds`).
8. Cross-checks **all live sources** pairwise; Claude multi-account stays canonical in cswap.
9. Optional surfaces: `aiuse suggest` (single burn winner), `aiuse status` / `prompt` (one-liner), and `aiuse serve` (loopback ranking API for agents). Snapshot history can blend into pace when enabled.

This command intentionally does not report historical local-token usage or
API-equivalent cost estimates (use ccusage-class tools for local burn, not plan %).

## Claude API credits (`omp`): override and expiry nudge

CodexBar reads the Max plan's promotional API credits from claude.ai and can report "100% unused"
while the Anthropic console shows the grant half spent. The Anthropic Admin API has no
credit-balance endpoint (its `GET /v1/organizations/cost_report` returns cost-to-date only), so
record the console figures yourself:

```
aiuse set-credits omp --remaining 95.57 --total 200 --expires 2026-11-08 --note "console 2026-10-19"
```

The figures go to `~/.cache/aiuse/credit-overrides.json` with a timestamp and are applied at read
time (`--json`, `--available`, `serve`), so a cached snapshot is corrected too. Which reading wins:

1. The override wins while it is at least as new as the CodexBar reading.
2. A newer CodexBar reading that shows _more_ credits than the override is contradicted (a grant never grows), so the override still wins; the replaced reading is kept as `superseded_reading`.
3. A newer CodexBar reading that shows the same or less wins (credits were spent since).
4. A CodexBar reading older than 24 h that no override supersedes is expired: `reading_expired: true`, balance and percentages dropped, state `unknown`, and the pool leaves `--available`.
5. An override is ignored once its `--expires` time has passed (a lapsed grant; a date alone means 00:00 UTC) and once it is older than 7 days, so refresh it with `aiuse set-credits`; rules 2-4 then apply to CodexBar alone. An `observed_at` in the future is invalid.
6. A malformed override file (non-finite or non-numeric figures, remaining above total, bad timestamps, a pool other than `omp`) is ignored with one line on stderr and never stops `aiuse --available`. `set-credits` refuses the same input with exit 2.
7. An override never brings back an omp pool you hid (`[hidden_pools]`) or disabled (`[disabled_services]`): the config is checked, so it holds when the cache has no omp row. An operator override only decorates an omp row the collectors produced; it never makes one.
8. Rule 2 applies only when the override's total equals CodexBar's grant size; otherwise the fresher CodexBar reading wins. Whenever a fresher CodexBar reading wins, the account carries `superseded_override` (`{source, observed_at, remaining_usd}`).
9. Figures must be within 1e9 dollars and the file under 64 KiB; anything else is ignored.

When CodexBar gives no `updatedAt`, `observed_at` falls back to the snapshot's collection time. The plain `aiuse` report does not apply the override; `--json`, `--available` and `serve` do.

`source` (`codexbar` or `operator-override`) and `observed_at` are in the omp account and pool JSON, and
`age_seconds`/`stale` derive from `observed_at`. `expires_at` is on the account and the pool.

When an expiring grant lapses within 21 days and more than 50% is left (the bar slides down in the last 10 days: 25% at 5 days out, 5% at 1 day), the pool carries
`burn_before_reset` (`expires_at`, `days_left`, `remaining_percent`, `remaining_usd`, `suggestion`) and
`aiuse --available` prints `burn before reset: ...` under its line. `remaining_percent` still means
the share left (`used_percent` is the share consumed).

### Admin API balance (`aiuse refresh-credits omp`)

The Anthropic Admin API has no balance endpoint, but its cost report
(`GET https://api.anthropic.com/v1/organizations/cost_report`, docs:
<https://platform.claude.com/docs/en/api/beta/organization/cost_report/retrieve>) gives the spend since a date, so
the balance is derived: grant total minus spend since the grant date, clamped to 0..total. It is a lower bound on the promo balance: the cost report counts all org spend since `granted_at`, including spend the grant did not cover (the first real run read $89.56 against the console's $95.57). Give the grant
parameters once, on the override:

```
aiuse set-credits omp --remaining 95.57 --total 200 --granted 2026-10-13 --expires 2026-11-08 --note "console 2026-10-19"
sudo-secretspec run --reason "aiuse cost report" -- aiuse refresh-credits omp
```

The key is `ANTHROPIC_ADMIN_KEY`, supplied only by `sudo-secretspec run` (a missing key means nothing runs and
no request is made). The reading is stored in `~/.cache/aiuse/credit-readings.json` with `source: admin-api`
and the time it was taken. The freshest of the Admin API reading and the operator override is used, and the
override rules above (expiry, hidden pools) apply to it, except that an Admin API reading lasts 24 h (like a CodexBar reading), not 7 days; after that the pool is `unknown` until a refresh succeeds.
For the same grant a reading can only fall: it is capped at the operator's figure and at the previous reading, a rise is noted, and a report with no buckets since the grant date counts as a failure. So once the hourly job is loaded it replaces a newer `set-credits` within the hour, and can only lower the figure. A key with characters outside `[A-Za-z0-9_-]` (after trimming) is refused with exit 2 and a message that never shows it; redirects are refused so the key is never forwarded. If the API call fails (20 s timeout,
any non-200, an unexpected body) one stderr line is printed and the previous reading is kept, so it ages and
shows STALE. An Admin API reading may create the omp row when the collectors made none; an operator override
may not.

Hourly refresh with launchd (not loaded for you). The wrapper is copied out of the checkout so a branch switch cannot change what launchd runs:

```
mkdir -p ~/.local/bin ~/Library/LaunchAgents ~/Library/Logs/aiuse
install -m 755 packaging/launchd/aiuse-credits-refresh ~/.local/bin/aiuse-credits-refresh
gsed -e "s|@REFRESH_BIN_PATH@|$HOME/.local/bin/aiuse-credits-refresh|" -e "s|@AIUSE_BIN_PATH@|$(command -v aiuse)|" \
  -e "s|USER_LOCAL_BIN|$HOME/.local/bin|" -e "s|LOG_DIR|$HOME/Library/Logs/aiuse|" \
  packaging/launchd/com.djbclark.aiuse-credits-refresh.plist \
  > ~/Library/LaunchAgents/com.djbclark.aiuse-credits-refresh.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.djbclark.aiuse-credits-refresh.plist
```

Unload with `launchctl bootout gui/$(id -u)/com.djbclark.aiuse-credits-refresh`.

## Example output

```
================================================================================
AI USAGE — USE IT OR LOSE IT
Collected at … · 3 accounts · 2 alerts
================================================================================

## Per-provider usage
--------------------------------------------------------------------------------
Codex · account=you@example.com · plan=plus · selected live source: CodexBar
  quota: Codex weekly quota
    [============] 100% left   0% used   resets in 6.4d (Tue Jul 28 9:59pm)
    flex:▒ semi · pace -100%

## Cross-checks (informational)
--------------------------------------------------------------------------------
  Tools poll at different times; multi-account Claude is cswap-only. …

## Tips
--------------------------------------------------------------------------------
  • …

## Action plan — use these before they reset
--------------------------------------------------------------------------------
  6 windows with unused capacity across 5 providers.

  THIS WEEK (start now — capacity will reset or needs lead time)
  ─────────────────────────────────────────────────────────────
  .   Codex · you@example.com · Codex weekly quota: 88% left · use within 6.4 days · pace -40% — projected 52% unused@reset
      Semi-throttled — steady usage will exhaust it.
  .   OpenCode Go · default · OpenCode Go weekly quota: 98% left · use within 4.5 days · pace -95% — projected 91% unused@reset
      Burstable — one heavy session will cover it.
```

## Project layout

```
src/aiuse/
  cli.py                 # entrypoint
  collectors/            # cswap, codexbar, caut, caam, openusage, tokscale
  analysis/use_or_lose.py
  report.py
config/config.example.toml
tests/
```

## Tests

```bash
just test
just check # tests plus deterministic lint, type, spelling, and format checks
just lint  # full check plus Bandit, Semgrep, and Gitleaks
just format
```

The quality suite mirrors the applicable tools from `stayturgid`: pytest, Ruff,
mypy, yamllint, markdownlint, Prettier (including TOML support), typos, Bandit,
Semgrep, Gitleaks, pre-commit, and `just`. Ansible, shell, JavaScript/CSS, dotenv,
Caddy, and browser-page checks are omitted because this repository contains none
of those corresponding inputs.

## Pace settings

Pace knobs (in `config.toml` under `[analysis.pace]`):

- `waste_alert_fraction` (default 0.30) — project this much unused → **Burn**
- `min_elapsed_fraction` (default 0.15) — too early in the window → **On pace** unless history says otherwise
- `conserve_min_lead_hours` (default 4) — exhaust this far before reset → **Conserve**

Shared allotment: `analysis.provider_overrides.<provider>.shared_allotment: true` (Claude/Gemini default) scores only the longest window per account.

Lapsed subscriptions: `analysis.lapsed_accounts` maps `"provider/account"` to a reason (e.g. `"claude/me@mit.edu" = "not renewed"`). A not-renewed plan keeps serving stale collector cache that looks like usable quota; the entry makes that account show as `empty` instead of on-pace.

Excluded pools: `analysis.excluded_pools` maps `"provider"` or `"provider/pool_family"` to a reason (e.g. `"grok" = "reserved for another agent"`). The quota stays in every report, but `aiuse --available` leaves the pool out of its shortlist and lists it under `excluded` (JSON) or on stderr (text), so orchestrators never route to it.

Disabled services: top-level `[disabled_services]` maps `"provider"` to a reason (e.g. `"grok" = "vendor shelved until its reset"`). Stronger than an exclusion — for TUIs that must temporarily not be used even though they have headroom: provider-only collectors skip the service entirely (no subprocess/authenticated fetch), its rows are dropped from every report, snapshot and history entry, `--available` never lists it (even from a cache written before the entry), and the reason travels on the snapshot as `disabled_services` so readers know why the provider is absent. `aiuse doctor` warns when a provider-only collector override for a disabled provider silently has no effect.

Hidden pools: top-level `[hidden_pools]` maps `"provider"` or `"provider/pool_family"` to a reason (e.g. `"cursor/auto" = "not routing Auto this week"`). Display-only: matching windows are dropped at collection and omitted from human outputs and `--available`. Weaker than `[disabled_services]`; stronger than `analysis.excluded_pools` (routing-only).

Ceiling rows: `analysis.show_total_rows` (default **false**) hides total/ceiling rows in the board, `--full`, `--for-chat`, and `--available` when breakdown sub-rows exist. JSON always includes them with `role: "ceiling"`; sub-rows carry `effective_left_percent` when the ceiling binds.

## Notes / limitations

- Live quota accuracy depends on each tool's auth (browser cookies, OAuth, keychain). Errors are reported per account rather than aborting the whole run.
- All enabled collectors (cswap, CodexBar, caut, caam, OpenUsage.ai, OpenUsage.sh, tokscale, qwencloud, bailian, sipb) run concurrently; each CodexBar provider is its own subprocess. Default tool timeout is **45s**; CodexBar OpenCode Go is **60s** (`[collectors.codexbar] provider_timeouts`); `cswap`, `caam`, and `openusage_sh` are **90s**; the opt-in `sipb` probe defaults to **120s** (the MIT server is slow) (`-t` / `config.toml [timeouts]`).
- Per-window detail still shows $ value, flexibility class, and a **pace** ratio when computable.
- Duplicate live measurements are retained for cross-checking but only one copy drives recommendations.
- Dollar values use plan `monthly_price` (a per-account `[plans.<provider>."<account>"]` price beats the provider's) with waking-hours correction (default 16h/day). A provider with no price shows no dollar figure.

## Further documentation

- [`docs/attribution.md`](docs/attribution.md) — what spent the quota: token ledgers, adaptive sampling (`aiuse sample`), `aiuse attribute`.
- [`packaging/install-deps.sh`](packaging/install-deps.sh) — install the optional data-source tools.
- [`docs/json-contract.md`](docs/json-contract.md) — stable JSON fields and exit codes.
- [`docs/provider-identity.md`](docs/provider-identity.md) — canonical provider id vs config key, and window identity across collectors.
- [`docs/shared-quota-semantics/`](docs/shared-quota-semantics/) — reusable language-neutral schemas, rules, and fixtures.
- [Documentation index](docs/index.md) — categorized guides, collector notes, automation, and maintainer material.
