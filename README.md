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
| OpenCode                    | `opencode`                    |
| DeepSeek, OpenRouter        | no CLI — per-token $ API only |

## See it in action

This is real `aiuse` output from synthetic demo accounts, not a screenshot.
It's the exact table printed by plain `aiuse`, read **bottom → top**: the
pools most worth using right now sort to the bottom.

<!-- readme-demo:start -->

```diff
        ## SERVICE    ACCT  SCOPE         5H       WEEK       MONTH      $ UNUSED
- error ?? oc-zen     gmail —          No available fetch strategy for opencode-zen.
- empty  0 oc-go      gmail —                  ->  100%/4d12h         <-        —
  n/a   -- deepseek   gmail —          $4.15 (counts down)
  n/a   -- openrouter gmail —          $18.55 (counts down)
  slow  48 agy        gmail claude/gpt         ->   78%/1d5h          <-        —
  mid   50 claude     gmail —            12%/3h7m   77%/3d2h          <-    $1.59
  mid   50 cursor     gmail —                  ->          ->   71%/3d7h    $5.80
  mid   50 codex      gmail —                  ->   54%/2d7h          <-    $3.17
  mid   50 grok       gmail —                  ->    6%/6d14h         <-        —
  mid   53 agy        gmail gemini        4%/4h5m   16%/4d            <-    $5.80
+ use   88 copilot    —     —                  ->   58%/~1d           <-        —
               2d14h = until this clock resets · bold = largest unit
                            Note: 100% means 100% Used
                AI: Use `aiuse --json` for machine-readable output
```

<!-- readme-demo:end -->

Every row is measured on the same three clocks, so a column reads top to
bottom. An em-dash means that service has no window on that clock at all.
**Percentages are consumption**: `0%` is untouched, `100%` is exhausted.
The colored `##` score is the action queue from `0` (empty) to `99` (use as
soon as possible). Its action-state boundaries are contiguous: `slow` ends at
49, `mid` runs from 50–74, and an active `use` recommendation starts at 75.
`??` means usage could not be fetched; `--` means rolling prepaid/PAYG
inventory has no use-or-lose urgency.

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

| Tool                                                                                            | Purpose                                                   | Authority                                                                                                          |
| ----------------------------------------------------------------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| [**cswap**](https://github.com/realiti4/claude-swap) `cswap list --json`                        | Live Claude Code quota for every configured email/account | Canonical multi-account Claude source                                                                              |
| [**CodexBar**](https://github.com/) `codexbar usage --format json`                              | Live quotas and balances for enabled providers            | Preferred for non-Claude providers; keep its Claude source disabled on macOS until its Keychain prompting is fixed |
| [**caut**](https://github.com/Dicklesworthstone/coding_agent_usage_tracker) `caut usage --json` | Independent multi-provider usage (CodexBar-class probes)  | Cross-check peer, but leave disabled on macOS until its repeated Keychain prompting is fixed                       |
| [**OpenUsage.ai**](https://www.openusage.ai/) `openusage` / `127.0.0.1:6736/v1/limits`          | Quiet macOS menu-bar companion + live limits API          | Cross-check peer; distinct collector key: `openusage_ai`                                                           |
| [**OpenUsage.sh**](https://openusage.sh/) `openusage-sh export --output - --format json`        | Terminal dashboard, local telemetry, and quota export     | Lowest-priority backup; distinct collector key: `openusage_sh`; only explicit quota metrics affect ranking         |
| [**tokscale**](https://www.npmjs.com/) `tokscale usage --json`                                  | Independent live subscription quota measurement           | Cross-checked against peers; preferred for Copilot; fill-in when others lack a live row                            |

This project shells out to tools already on your `PATH` (and optionally hits
OpenUsage’s loopback API); it does not scrape billing dashboards itself. For
more on collector setup, reliability, and source selection, see the
[documentation index](docs/index.md).

### macOS source policy (current)

Use **cswap** as the Claude source and **CodexBar** for every other provider.
Keep CodexBar's Claude source disabled, even as a fallback. **tokscale**,
OpenUsage.ai, OpenUsage.sh, and other available collectors are useful independent backup and
cross-check sources. All collectors are enabled by default, whether installed
now or prepared for later; disable any source in `config.toml` when it is not
appropriate for your machine. On macOS, consider disabling **caut** and
CodexBar's Claude integration if their Keychain-prompting bugs affect you.

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

## Daily workflow

```bash
# Once: create defaults under ~/.config/aiuse/ (never overwrites)
aiuse --generate-config
aiuse doctor                 # PATH tools + config presence + timeouts
# Once per machine: install the data-source tools you intend to use
# (normally cswap, CodexBar, tokscale, and either or both distinct OpenUsage tools)
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
aiuse watch                # full-screen board (q/esc quit; default 10m)
aiuse watch -i 2m          # faster refresh
aiuse watch --once         # one frame on stdout (scripts / tmux)

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

Projects that want the ranking logic without depending on this CLI can reuse
the language-neutral [shared quota semantics](docs/shared-quota-semantics/):
JSON Schemas, enums, pace rules, and golden fixtures are designed to be copied
or validated from any language. `aiuse` uses those fixtures itself, but they do
not require importing its Python package.

| Flag                                                                                                   | Effect                                                                            |
| ------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| _(none)_ / `--format pretty`                                                                           | Clock matrix on stdout (empty→n/a→slow→mid→use); meta on stderr; plain when piped |
| `--full`                                                                                               | Long pretty report: per-provider, cross-checks, tips, History, detailed plan      |
| `--json` / `--format json`                                                                             | Full snapshot + alerts + `suggestion` + `history` as JSON                         |
| `--brief`                                                                                              | Alias of the default pretty report                                                |
| `--no-tui`                                                                                             | Force classic plain-text pretty report                                            |
| `--no-color`                                                                                           | Disable ANSI colors in plain-text pretty mode                                     |
| `-q` / `--quiet`                                                                                       | Suppress progress messages on stderr                                              |
| `--alerts-only`                                                                                        | Recommendations only (respects pretty vs json)                                    |
| `suggest` / `--suggest`                                                                                | Single best burn pool (or nothing urgent); pair with `--json` for structured      |
| `status` / `prompt` / `--status`                                                                       | One-line status for shell prompts / status bars                                   |
| `serve` / `--serve`                                                                                    | Loopback HTTP API for agents (`127.0.0.1`)                                        |
| `--port` / `--max-age`                                                                                 | Serve bind port (default 8787) and snapshot cache max age                         |
| `--print-completion bash\|zsh`                                                                         | Print shell completion script to stdout                                           |
| `--no-<collector>` flags: tokscale, cswap, codexbar, caut, openusage-ai, openusage-sh, muse, qwencloud | Skip specific collectors                                                          |
| `--providers copilot,grok`                                                                             | Query specific CodexBar providers (CSV, one per subprocess)                       |
| `-t` / `--timeout SECONDS`                                                                             | Force subprocess timeout for all external tools (default **45**)                  |
| `--generate-config`                                                                                    | Write default `~/.config/aiuse/config.toml`; never overwrites existing            |
| `--show-config-path`                                                                                   | Print the active config path                                                      |
| `doctor` / `--doctor`                                                                                  | Check tools on PATH, config presence, effective timeouts; no collect              |
| `trust` …                                                                                              | macOS: codesign status, sign caut, and Keychain grant guide                       |
| `credential refresh opencode-zen`                                                                      | Validate a Chrome OpenCode session and save it through SecretSpec                 |
| `--min-remaining 50 --max-days 10`                                                                     | Override alert thresholds                                                         |
| `--save PATH`                                                                                          | Also write full JSON snapshot to PATH                                             |

### Exit codes

| Code  | When                                                                                                                                                                                                                               |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **0** | Collect succeeded (or nothing to report) and there are **no** burn/conserve alerts. INFO-only notes still count as 0.                                                                                                              |
| **1** | Hard failure: collectors reported errors and **no** accounts were collected. Also used by `aiuse doctor` when an **enabled** tool is missing from `PATH`, and by `--generate-config` when nothing was written / overwrite refused. |
| **2** | Collect succeeded and at least one **burn** or **conserve** alert is present. Cross-check disagreements alone do **not** set 2. Bad `--timeout` values also use 2.                                                                 |

`aiuse doctor` checks config file presence, **config validation** (unknown keys, bad
timeouts, dead plan aliases), all six data-source tools on `PATH` (and OpenUsage.ai
loopback HTTP when the CLI is missing), and a light **version probe**
(`cswap --version`, `codexbar -V`, `caut --version`, `tokscale --version`). It does
**not** call usage APIs or verify login sessions.

## What “use it or lose it” means

Most **subscription** coding plans (Claude Pro/Max, ChatGPT Plus/Codex, Cursor, Copilot, SuperGrok, Google AI Pro, …) grant **windows** of usage (5-hour, weekly, monthly). When the window resets, **unused capacity disappears** — you still paid for the month.

This tool:

1. Pulls **remaining %** and **reset times** from **cswap** (Claude multi-account), **CodexBar** (broad live quotas), **caut**, **OpenUsage.ai**, **OpenUsage.sh** (cross-check peers / fill-in), and **tokscale** (independent measurement; preferred for Copilot).
2. Scores windows with **pace-based** logic (default): compare how far through the cycle you are vs how much you've used, then project waste or early lockout.
3. Classifies each window as **Burn** (will leave capacity unused), **Conserve** (on track to exhaust before reset — slow down), or **On pace** (no alert).
4. For **shared-allotment** providers (Claude, Gemini by default), scores the longest governing window only so a fresh 5-hour bar does not outrank the weekly budget it draws from — but genuinely **independent pools are never merged into that governing window**: Cursor's Included+Auto pool and its separate Other Models pool are scored on their own, so an exhausted Other Models pool raises its own alert instead of being masked by a healthy Included.
5. Qualifies **Conserve** alerts with **overage awareness**: if the account has a real or config-confirmed overage/extra-usage wallet (Claude's usage credits, Cursor's on-demand balance, or a manually confirmed OpenCode Go overage state), the message says so — the real risk there is unplanned $ spend, not lockout, which is a different situation from a genuinely hard ceiling.
6. Default pretty output is a **clock matrix** on stdout — one row per account/pool, one column per reset clock (5H/WEEK/MONTH), percentages **used** with that clock's reset after a slash (`75%/4h`, `89%/2d14h`; depleted → prepaid n/a → conserve → mid → use-soon at bottom; read bottom→top). Meta goes to stderr. Use `aiuse --full` for per-provider detail.
7. On `--full`, keeps the trailing plan within ~**23 lines × console width** when possible; if the detailed plan is taller, both detailed and **at a glance** are printed (glance last). Forecast fragments (`~lockout …`, projected waste %) appear on table/status lines when pace can project them.
8. Cross-checks **all live sources** pairwise; Claude multi-account stays canonical in cswap.
9. Optional surfaces: `aiuse suggest` (single burn winner), `aiuse status` / `prompt` (one-liner), and `aiuse serve` (loopback ranking API for agents). Snapshot history can blend into pace when enabled.

This command intentionally does not report historical local-token usage or
API-equivalent cost estimates (use ccusage-class tools for local burn, not plan %).

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
    [============] 100% left   0% used   resets in 6.4d (Jul 28 21:59 UTC)
    $6.90 · flex:▒ semi

## Cross-checks (informational)
--------------------------------------------------------------------------------
  Tools poll at different times; multi-account Claude is cswap-only. …

## Tips
--------------------------------------------------------------------------------
  • …

## Action plan — use these before they reset
--------------------------------------------------------------------------------
  Available capacity this cycle: $35.65 across 6 windows (5 providers).

  THIS WEEK (start now — capacity will reset or needs lead time)
  ─────────────────────────────────────────────────────────────
  .   Codex · you@example.com · Codex weekly quota: 88% left · use within 6.4 days · $6.07 at risk
      Semi-throttled — steady usage will exhaust it.
  .   OpenCode Go · default · OpenCode Go weekly quota: 98% left · use within 4.5 days · $3.37 at risk
      Burstable — one heavy session will cover it.
```

## Project layout

```
src/aiuse/
  cli.py                 # entrypoint
  collectors/            # cswap, codexbar, caut, openusage, tokscale
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

## Notes / limitations

- Live quota accuracy depends on each tool's auth (browser cookies, OAuth, keychain). Errors are reported per account rather than aborting the whole run.
- All enabled collectors (cswap, CodexBar, caut, OpenUsage.ai, OpenUsage.sh, tokscale, qwencloud) run concurrently; each CodexBar provider is its own subprocess. Default tool timeout is **45s** (`-t` / `config.toml [timeouts]`).
- Per-window detail still shows $ value, flexibility class, and a **pace** ratio when computable.
- Duplicate live measurements are retained for cross-checking but only one copy drives recommendations.
- Dollar values use plan `monthly_price` with waking-hours correction (default 16h/day).

## Further documentation

- [`packaging/install-deps.sh`](packaging/install-deps.sh) — install the optional data-source tools.
- [`docs/json-contract.md`](docs/json-contract.md) — stable JSON fields and exit codes.
- [`docs/provider-identity.md`](docs/provider-identity.md) — canonical provider id vs config key, and window identity across collectors.
- [`docs/shared-quota-semantics/`](docs/shared-quota-semantics/) — reusable language-neutral schemas, rules, and fixtures.
- [Documentation index](docs/index.md) — categorized guides, collector notes, automation, and maintainer material.
