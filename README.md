# aiuse

Aggregate live **subscription quota** and **prepaid balance** information across
your local AI tooling, then highlight allotments you should use **before they
reset** (use-it-or-lose-it).

CLI command: **`aiuse`** (stub **`ai`** → same entrypoint)

> **AI agents:** start at [`AGENTS.md`](AGENTS.md) for a map of this repo,
> active priorities, Claude/cswap reliability notes
> ([`docs/cswap-reliability.md`](docs/cswap-reliability.md)), and
> review-derived fixes in [`docs/fix-implementation-plan.md`](docs/fix-implementation-plan.md).

## Data sources

| Tool                                                                     | Purpose                                                   | Authority                                                                                                     |
| ------------------------------------------------------------------------ | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| [**cswap**](https://github.com/realiti4/claude-swap) `cswap list --json` | Live Claude Code quota for every configured email/account | Canonical multi-account Claude source; may hydrate from cswap’s local usage cache when JSON is decision-stale |
| [**CodexBar**](https://github.com/) `codexbar usage --format json`       | Live quotas and balances for enabled providers            | Preferred for non-Claude providers; Claude fallback if cswap has no live rows                                 |
| [**caut**](https://github.com/Dicklesworthstone/coding_agent_usage_tracker) `caut usage --json` | Independent multi-provider usage (CodexBar-class probes) | Cross-check peer; fill-in when CodexBar missing                                                               |
| [**OpenUsage**](https://www.openusage.ai/) CLI / `127.0.0.1:6736/v1/limits` | Menu-bar companion + loopback limits API                | Cross-check peer; optional CLI install from app Settings                                                      |
| [**tokscale**](https://www.npmjs.com/) `tokscale usage --json`           | Independent live subscription quota measurement           | Cross-checked against peers; preferred for Copilot; fill-in when others lack a live row                       |

This project shells out to tools already on your `PATH` (and optionally hits
OpenUsage’s loopback API); it does not scrape billing dashboards itself. For
Claude multi-account reliability (stale JSON vs cache), see
[`docs/cswap-reliability.md`](docs/cswap-reliability.md). caut + OpenUsage
setup: [`docs/collectors-caut-openusage.md`](docs/collectors-caut-openusage.md).

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

More channels and **release / OIDC publish** docs:
[`docs/packaging.md`](docs/packaging.md).

**Scheduled runs (macOS LaunchAgent, hourly):** [`docs/scheduling.md`](docs/scheduling.md)
(`./packaging/launchd/install.sh`). Snapshot history:
[`docs/history-learning.md`](docs/history-learning.md).

Optional config (standard location: **`~/.config/aiuse/`**, or `$XDG_CONFIG_HOME/aiuse/`):

```bash
# Create directories + default files (never overwrites existing files)
aiuse --generate-config

# Or copy examples by hand:
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/aiuse"
cp config/services.example.yaml "${XDG_CONFIG_HOME:-$HOME/.config}/aiuse/services.yaml"
cp config/config.example.toml "${XDG_CONFIG_HOME:-$HOME/.config}/aiuse/config.toml"
```

| File                            | Purpose                                                                       |
| ------------------------------- | ----------------------------------------------------------------------------- |
| `~/.config/aiuse/services.yaml` | Plans, analysis thresholds, which collectors are enabled                      |
| `~/.config/aiuse/config.toml`   | Tool settings: subprocess **timeouts** (default **45s**), room for more later |

Run `aiuse --show-config-path` to print both paths. `aiuse --generate-config` creates
missing parent dirs (`~/.config`, `~/.config/aiuse`) and writes defaults; if a file
already exists it is left alone and reported on stderr. Provider credentials stay
with cswap, CodexBar, caut, OpenUsage, and tokscale — these files do not hold tokens or emails.

## Daily workflow

```bash
# Once: create defaults under ~/.config/aiuse/ (never overwrites)
aiuse --generate-config
aiuse doctor                 # PATH tools + config presence + timeouts
# Once per machine: install data-source tools (cswap, codexbar, caut, OpenUsage, tokscale)
./packaging/install-deps.sh
# or: just -f ~/ops/site-djbclark/justfile install-aiuse-deps

# Morning / before a long coding block
aiuse                        # priority ladder on stdout (use-soon at bottom); meta on stderr
aiuse --full                 # long report: per-provider, tips, detailed plan
aiuse --brief                # same as default (compat alias)
aiuse --no-tui               # classic plain-text report (also used when piping)
aiuse -q                     # ladder only (no stderr meta / Collecting…)

# Scripting / cron (JSON on stdout; use exit codes)
aiuse -q --json
# exit 0 = ok, no burn/conserve alerts
# exit 1 = hard failure (collectors failed, no accounts)
# exit 2 = success with at least one burn/conserve alert
# field contract: docs/json-contract.md

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
aiuse --save ~/tmp/ai-snapshot.json   # also write JSON file

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

| Flag                                             | Effect                                                                           |
| ------------------------------------------------ | -------------------------------------------------------------------------------- |
| _(none)_ / `--format pretty`                     | Priority ladder on stdout (empty→n/a→slow→mid→use); meta on stderr; plain when piped |
| `--full`                                         | Long pretty report: per-provider, cross-checks, tips, detailed plan              |
| `--json` / `--format json`                       | Full snapshot + alerts as JSON                                                   |
| `--brief`                                        | Alias of default priority-ladder pretty report                                   |
| `--no-tui`                                       | Force classic plain-text pretty report                                           |
| `--no-color`                                     | Disable ANSI colors in plain-text pretty mode                                    |
| `-q` / `--quiet`                                 | Suppress progress messages on stderr                                             |
| `--alerts-only`                                  | Recommendations only (respects pretty vs json)                                   |
| `--traditional-summary`                          | Legacy flat summary format instead of unified action plan                        |
| `--print-completion bash\|zsh`                   | Print shell completion script to stdout                                          |
| `--no-tokscale` / `--no-cswap` / `--no-codexbar` / `--no-caut` / `--no-openusage` | Skip specific collectors                                          |
| `--providers copilot,grok`                       | Query specific CodexBar providers (CSV, one per subprocess)                      |
| `-t` / `--timeout SECONDS`                       | Force subprocess timeout for all external tools (default **45**)                 |
| `--generate-config`                              | Write default `~/.config/aiuse/*` files; never overwrites existing               |
| `--show-config-path`                             | Print services.yaml and config.toml paths                                        |
| `doctor` / `--doctor`                            | Check tools on PATH, config presence, effective timeouts; no collect             |
| `--min-remaining 50 --max-days 10`               | Override alert thresholds                                                        |
| `--save PATH`                                    | Also write full JSON snapshot to PATH                                            |

### Exit codes

| Code  | When                                                                                                                                                                                                                               |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **0** | Collect succeeded (or nothing to report) and there are **no** burn/conserve alerts. INFO-only notes still count as 0.                                                                                                              |
| **1** | Hard failure: collectors reported errors and **no** accounts were collected. Also used by `aiuse doctor` when an **enabled** tool is missing from `PATH`, and by `--generate-config` when nothing was written / overwrite refused. |
| **2** | Collect succeeded and at least one **burn** or **conserve** alert is present. Cross-check disagreements alone do **not** set 2. Bad `--timeout` values also use 2.                                                                 |

`aiuse doctor` checks config file presence, **config validation** (unknown keys, bad
timeouts, dead plan aliases), all five data-source tools on `PATH` (and OpenUsage
loopback HTTP when the CLI is missing), and a light **version probe**
(`cswap --version`, `codexbar -V`, `caut --version`, `tokscale --version`). It does
**not** call usage APIs or verify login sessions.

## What “use it or lose it” means

Most **subscription** coding plans (Claude Pro/Max, ChatGPT Plus/Codex, Cursor, Copilot, SuperGrok, Google AI Pro, …) grant **windows** of usage (5-hour, weekly, monthly). When the window resets, **unused capacity disappears** — you still paid for the month.

This tool:

1. Pulls **remaining %** and **reset times** from **cswap** (Claude multi-account), **CodexBar** (broad live quotas), **caut** and **OpenUsage** (cross-check peers / fill-in), and **tokscale** (independent measurement; preferred for Copilot).
2. Scores windows with **pace-based** logic (default): compare how far through the cycle you are vs how much you've used, then project waste or early lockout.
3. Classifies each window as **Burn** (will leave capacity unused), **Conserve** (on track to exhaust before reset — slow down), or **On pace** (no alert).
4. For **shared-allotment** providers (Claude, Gemini by default), scores the longest governing window only so a fresh 5-hour bar does not outrank the weekly budget it draws from.
5. Default pretty output is a **priority ladder** on stdout (depleted → prepaid n/a → conserve → mid → use-soon at bottom; read bottom→top). Meta goes to stderr. Use `aiuse --full` for per-provider detail.
6. On `--full`, keeps the trailing plan within ~**23 lines × console width** when possible; if the detailed plan is taller, both detailed and **at a glance** are printed (glance last).
7. Cross-checks **all live sources** pairwise; Claude multi-account stays canonical in cswap (with cache hydrate + fallbacks).

This command intentionally does not report historical local-token usage or
API-equivalent cost estimates.

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
config/services.example.yaml
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

## Scoring modes (`analysis.scoring_mode`)

| Mode                 | Meaning                                                                         |
| -------------------- | ------------------------------------------------------------------------------- |
| **`pace`** (default) | Burn / conserve / on-pace from projected waste and lockout                      |
| **`multi_dim`**      | Previous value + flexibility + deadline blend (escape hatch)                    |
| **`legacy`**         | Original deadline-heavy scorer (`use_multi_dim_scoring: false` still maps here) |

Pace knobs (also in `services.yaml` under `analysis.pace`):

- `waste_alert_fraction` (default 0.30) — project this much unused → **Burn**
- `min_elapsed_fraction` (default 0.15) — too early in the window → **On pace** unless history says otherwise
- `conserve_min_lead_hours` (default 4) — exhaust this far before reset → **Conserve**

Shared allotment: `analysis.provider_overrides.<provider>.shared_allotment: true` (Claude/Gemini default) scores only the longest window per account.

## Notes / limitations

- Live quota accuracy depends on each tool's auth (browser cookies, OAuth, keychain). Errors are reported per account rather than aborting the whole run.
- All enabled collectors (cswap, CodexBar, caut, OpenUsage, tokscale) run concurrently; each CodexBar provider is its own subprocess. Default tool timeout is **45s** (`-t` / `config.toml [timeouts]`).
- Per-window detail still shows $ value, flexibility class, and a **pace** ratio when computable.
- Duplicate live measurements are retained for cross-checking but only one copy drives recommendations.
- Dollar values use plan `monthly_price` with waking-hours correction (default 16h/day).

## Related reading

- [`docs/consumption-flexibility-plan.md`](docs/consumption-flexibility-plan.md) — historical multi-dimensional scoring design (**superseded** by pace-based scoring in Phase 2 of the fix plan).
- [`docs/code-review-2026-07-23.html`](docs/code-review-2026-07-23.html) — a 79-agent adversarial code review (45 findings) plus design proposals for containing tokscale's collector timeouts and fixing the rating algorithm. Open it directly in a browser for the styled version; GitHub's file viewer only shows the source.
- [`docs/fix-implementation-plan.md`](docs/fix-implementation-plan.md) — review-derived fix plan (Steps **1–32** and **34** done; Phase 7 optional 33/35 remain). Start at [`AGENTS.md`](AGENTS.md) for current priorities, not Step 1.
- [Issue #1](https://github.com/djbclark/aiuse/issues/1) — track consuming upstream cswap display-grade last-good JSON ([claude-swap#170](https://github.com/realiti4/claude-swap/issues/170)).
- [`docs/cswap-reliability.md`](docs/cswap-reliability.md) — Claude multi-account reliability: why `cswap list --json` can drop usable quota, and how cache hydration + fallbacks work.
- [`docs/opencode-go-quota.md`](docs/opencode-go-quota.md) — OpenCode Go: why CodexBar `auto`/local can show remaining % when the TUI says limit reached, and how `aiuse` prefers web.
- [`docs/cursor-quota.md`](docs/cursor-quota.md) — Cursor Included/Auto/API + on-demand vs CodexBar slots.
- [`docs/antigravity-pools.md`](docs/antigravity-pools.md) — Google AI / Antigravity: Gemini vs Claude/GPT are independent pools.
- [`docs/pretty-display.md`](docs/pretty-display.md) — why pretty output uses Rich (not Textual / not Rich `Layout`) so the full report stays in scrollback.
- [`docs/packaging.md`](docs/packaging.md) — pipx / PyPI / Homebrew; Trusted Publishing (OIDC) release flow.
- [`packaging/install-deps.sh`](packaging/install-deps.sh) — install all five data-source tools (cswap, CodexBar, caut, OpenUsage, tokscale).
- [`docs/collectors-caut-openusage.md`](docs/collectors-caut-openusage.md) — caut + OpenUsage setup and cross-check priority.
- [`docs/competitive-landscape.md`](docs/competitive-landscape.md) — monitors vs decision tools (quotabot, onWatch, CodexBar); where `aiuse` is stronger/weaker at “what pool next?”; strategy + feature issues [#2](https://github.com/djbclark/aiuse/issues/2)–[#8](https://github.com/djbclark/aiuse/issues/8).
- [`docs/shared-quota-semantics.md`](docs/shared-quota-semantics.md) — language-neutral schemas, pace formulas, and golden-vector plan for sharing ranking/prepaid/shared-allotment meaning across tools.
- [`docs/scheduling.md`](docs/scheduling.md) — LaunchAgent hourly (`persist_snapshots`).
- [`docs/history-learning.md`](docs/history-learning.md) — snapshot history vs `learn_from_history`.
- [`docs/claude-local-usage.md`](docs/claude-local-usage.md) — Local Claude Code files / ccusage (token burn) vs subscription 5h/7d % from the OAuth usage API.
- [`docs/tokscale-per-provider-investigation.md`](docs/tokscale-per-provider-investigation.md) — why tokscale cannot yet fan out per provider like CodexBar.
- [`docs/json-contract.md`](docs/json-contract.md) — stable JSON fields and exit codes for scripts.
- [`docs/collector-concurrency.md`](docs/collector-concurrency.md) — parallel collect + 45s timeout audit.
- [`docs/handoff.md`](docs/handoff.md) — latest session wrap-up and loose ends (for agents and future you).
- [`completions/`](completions/) — bash/zsh completion (`aiuse --print-completion bash`).
- [`docs/review-workflow.js`](docs/review-workflow.js) — the Claude Code Workflow script that generated the review, checked in for reproducibility.
- [`docs/memory/`](docs/memory/) — thin Claude symlink target for this project; see `AGENTS.md` for persistence policy and links to `~/ops/site-private` generic memory.
- Local quota dashboards in the same category as OpenUsage / CodexBar — see [`docs/competitive-landscape.md`](docs/competitive-landscape.md).
