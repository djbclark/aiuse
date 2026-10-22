# caut + OpenUsage.ai / OpenUsage.sh collectors

**Date:** 2026-07-25 (updated 2026-10-22)

## Deprecated: caut, caam, OpenUsage.ai, OpenUsage.sh

Support for **caut**, **caam**, **OpenUsage.ai** (`openusage_ai`), and
**OpenUsage.sh** (`openusage_sh`) is deprecated. Each one overlaps a collector
that stays in the supported set: cswap, CodexBar, tokscale, and paseo, plus the
native OpenCode Go collector. The collector code remains in the tree and still
runs when the binary is on `PATH` and the config leaves it enabled. The
maintainer is happy to re-add first-class support if anyone wants one of them
back.

OpenUsage.ai was checked on 2026-10-22 before it was removed. Its live
`/v1/limits` cards were a subset of another source, or empty:

- Antigravity returned only the Claude/GPT windows and omitted the Gemini
  weekly the agy client enforces. CodexBar reports that window.
- Claude matched cswap. Codex, Cursor, Copilot, and Grok matched CodexBar or
  tokscale (same percents and the same resets).
- The OpenCode card was empty. The native OpenCode Go collector has those
  windows.
- z.ai and OpenRouter came back as missing-key errors. CodexBar already
  reports z.ai.

Two OpenUsage fields never reached the board: a Copilot extra-usage count with
no limit (the mapper drops it) and a Codex "rate limit resets" balance (a note
on a row that loses to CodexBar, which already shows the weekly percent and
zero credits).

| Collector        | Overlaps                                                                            |
| ---------------- | ----------------------------------------------------------------------------------- |
| **caut**         | cswap, CodexBar, and tokscale (Claude and Codex)                                    |
| **caam**         | the same direct sources, as a vault-profile echo behind all of them                 |
| **OpenUsage.ai** | cswap, CodexBar, tokscale, and the native OpenCode Go collector                     |
| **OpenUsage.sh** | the same set (it was first deprecated for overlap with OpenUsage.ai, now also gone) |

**Status:** Multi-source cross-checks prioritize correctness, but **not all sources are suitable for ambient polling**. Recommended collector posture:

- **CodexBar**: **Highly recommended** as primary multi-provider source (<2ms read via group container `widget-snapshot.json` cache).
- **cswap**: **Highly recommended** as authoritative source for multi-account Claude Code.
- **tokscale**: **Recommended** for lightweight Copilot and Cursor token tracking.
- **OpenUsage.ai**: **Deprecated** (2026-10-22). Overlaps cswap, CodexBar, tokscale, and the native OpenCode Go collector. Its Antigravity card omits Gemini. The app was removed from this machine.
- **OpenUsage.sh**: **Deprecated** (overlap). Heavy (~40s CPU) export latency, yields mostly all-time session logs rather than quota rate limits, and poses terminal raw-mode corruption risks.
- **caut**: **Deprecated** (overlap with cswap, CodexBar, and tokscale). Ad-hoc cargo binary triggers recurring macOS Keychain dialogs; Claude credential detection is flaky.
- **caam**: **Deprecated** (overlap; vault echo behind every direct source). Guarded build required (to prevent unexpected browser consent/login popups).

## Why

`aiuse` prefers ranking quality over a single data path. In addition to
**cswap**, **CodexBar**, and **tokscale**, it can also collect:

| Source                                                                        | Role                                                                       | Recommendation                             |
| ----------------------------------------------------------------------------- | -------------------------------------------------------------------------- | ------------------------------------------ |
| **[CodexBar](https://github.com/steipete/CodexBar)**                          | Primary multi-provider usage probe (instant widget snapshot cache)         | **Recommended**                            |
| **[cswap](https://github.com/realiti4/claude-swap)**                          | Authoritative multi-account Claude Code rate limits                        | **Recommended**                            |
| **[OpenUsage.ai](https://www.openusage.ai/)**                                 | macOS menu bar app + CLI and/or `http://127.0.0.1:6736/v1/limits`          | **Deprecated** (overlap)                   |
| **[tokscale](https://github.com/Dicklesworthstone/tokscale)**                 | Copilot & Cursor token quotas                                              | **Recommended**                            |
| **[caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker)**   | Cross-platform CLI usage probe (`caut.v1` JSON)                            | **Deprecated** (overlap; keychain prompts) |
| **[caam](https://github.com/Dicklesworthstone/coding_agent_account_manager)** | Account vault; `caam limits` reads vault profiles (see [caam.md](caam.md)) | **Deprecated** (overlap)                   |
| **[OpenUsage.sh](https://openusage.sh/)**                                     | Terminal dashboard; versioned JSON export through `openusage-sh`           | **Deprecated** (overlap)                   |

Primary selection still follows priority (Claude → cswap; Copilot → tokscale;
others → CodexBar first). **All live sources are pair-wise cross-checked.**

## Machine install

**Default install** (cswap, CodexBar, tokscale). caut, caam, OpenUsage.ai, and
OpenUsage.sh are deprecated and are no longer in that default:

```bash
# From an aiuse checkout:
./packaging/install-deps.sh
./packaging/install-deps.sh --check
just install-deps              # same script

# From site-djbclark (preferred on this Mac):
just install-aiuse-deps        # execs packaging/install-deps.sh when AIUSE_ROOT exists
just aiuse-deps-status
just brew-project              # claim openusage + codexbar casks in Merged-Brewfile
```

**caut + OpenUsage only** (subset):

```bash
just install-ai-quota-tools
just ai-quota-status
```

### caut

```bash
just install-caut
# or:
cargo install --locked --git https://github.com/Dicklesworthstone/coding_agent_usage_tracker
ln -sfn ~/.cargo/bin/caut ~/.local/bin/caut
```

Requires Rust/`cargo` on PATH. Binary: `caut` (currently **0.1.0**).

### OpenUsage.ai

```bash
just install-openusage
# brew install --cask openusage
```

**Human step (once):**

1. Open **OpenUsage.app** (already under `/Applications` after cask install).
2. Complete any first-run provider enablement / keychain prompts.
3. **Settings → Command Line → Install…** so `openusage` is on PATH  
   (optional if you always leave the app running — `aiuse` falls back to HTTP).

`aiuse` collector order for OpenUsage.ai:

1. `openusage --force` when CLI is on PATH (fresh read).
2. Else `GET http://127.0.0.1:6736/v1/limits`.
3. Else try `open -ga OpenUsage` and retry HTTP.

## Config (`~/.config/aiuse/config.toml`)

```toml
[collectors.caut]
enabled = true
# both = claude+codex (only pair caut fills windows for reliably)
# all  = every name caut knows — most error "unsupported source Auto"
providers = "both"

[collectors.openusage_ai]
enabled = true
force_refresh = true
try_launch_app = true
base_url = "http://127.0.0.1:6736"
# Doctor / preflight probe path (payload collect still uses base_url + /v1/limits).
health_path = "/v1/limits"
# Or full URL override:
# probe_url: "http://127.0.0.1:6736/v1/limits"
```

`health_path` / `probe_url` apply to doctor and optional HTTP preflight: “is the
loopback up?” without treating a 404 on `/` as collector death when the payload
path still returns 200. Other collectors accept the same keys when they expose
HTTP; PATH-only tools ignore them.

OpenUsage.sh is installed separately without shadowing the app CLI. The normal
`install-deps.sh` helper performs these steps and creates the `openusage-sh`
wrapper; they are shown here for a manual install:

```bash
brew tap janekbaraniewski/tap
brew install janekbaraniewski/tap/openusage
# Keep Homebrew's formula unlinked when OpenUsage.ai owns `openusage` on PATH.
brew unlink openusage
# Create a local `openusage-sh` wrapper to /opt/homebrew/opt/openusage/bin/openusage.
```

**If the formula is linked anyway** (`/opt/homebrew/bin/openusage` ahead of
`/usr/local/bin/openusage`), aiuse still finds the right one. Since 3.3.1 the
OpenUsage.ai collector never trusts the first `openusage` on PATH: it walks every
candidate (PATH order, then `/usr/local/bin/openusage` and the app bundle's
`Contents/Helpers/openusage`) and runs the first that proves to be the app CLI,
either by resolving into `OpenUsage.app` or by answering `--help` with the app
CLI's banner (a detached 5 s probe). The reason this matters: openusage.sh's
binary run with no subcommand opens its full-screen dashboard on `/dev/tty`
even when stdin is `/dev/null`, never exits, and once killed at the collector
timeout it leaves the terminal in raw mode, so the report that follows prints
as a staircase (each line starting where the previous ended). Collector
children now also run in a new session (`start_new_session`), which makes
`/dev/tty` unopenable for them, and the runner restores termios before anything
is rendered. `aiuse doctor` names any same-named binary it skipped.

`aiuse` invokes `openusage-sh export --output - --format json`; it accepts only
explicit rate-limit and plan-percent metrics as quota windows, never local
token/cost estimates. Identical metric aliases are collapsed, while Cursor's
Cursor total/auto/third-party pools remain distinct. CLI skips: `--no-caut`, `--no-openusage-ai` (legacy
`--no-openusage`), and `--no-openusage-sh`.

### Why OpenUsage.sh is NOT recommended for ambient polling

While `openusage-sh` provides a standalone terminal dashboard, running `openusage-sh export` in automated background polling (such as `aiuse watch` or an hourly LaunchAgent) is **strongly discouraged**:

1. **High CPU load and long latency (~40s):** `openusage-sh export` walks local transcript logs, sqlite files, and session directories (Pi, Zed, Qwen, etc.), consuming ~30–40 seconds of 90%+ CPU per call. Under concurrent load, it frequently hits the 90.0s collector timeout.
2. **Minimal quota yield:** Nearly all data returned by `openusage-sh` consists of cumulative all-time token/request counters, which `aiuse` intentionally ignores (only percentage rate-limit windows drive `aiuse` ranking). The heavy compute cost yields almost zero actionable quota data.
3. **Terminal corruption hazard:** Invoking the binary without subcommands or with improper TTY detachment can hijack `/dev/tty` and leave the terminal in raw mode, garbling subsequent output into staircases.

**Recommended posture:** Disable `openusage_sh` in `~/.config/aiuse/config.toml`:

```toml
[collectors.openusage_sh]
enabled = false
```

Rely instead on `CodexBar` (which reads the instant `<2ms` widget snapshot cache) and `tokscale`.

## Selection priority (generalized)

| Provider | Order (first live wins for the ladder)                           |
| -------- | ---------------------------------------------------------------- |
| Claude   | cswap → CodexBar → caut → OpenUsage.ai → tokscale → OpenUsage.sh |
| Copilot  | tokscale → CodexBar → caut → OpenUsage.ai → OpenUsage.sh         |
| Default  | CodexBar → caut → OpenUsage.ai → OpenUsage.sh → tokscale         |

Adding another source later: implement `collect_*`, append to `run_collectors`
jobs, add to `PROVIDER_SOURCE_PRIORITY` / `DEFAULT_SOURCE_PRIORITY` and
`SOURCE_LABELS`. Cross-checks are all-pairs among live sources.

## Secrets

No new secrets for caut/OpenUsage in normal operation — they reuse local
cookies/keychain/CLI auth like CodexBar. Do **not** put provider tokens into
aiuse config. Site `secretspec` is not required for these two tools.

## macOS Keychain / “Always Allow” (caut)

Cargo-installed **caut** is adhoc-signed, so Keychain **Always Allow** often
does not survive reinstalls (hourly LaunchAgent → repeated dialogs).

```bash
aiuse trust setup          # cert steps + sign if identity exists + grant guide
aiuse trust sign-caut      # after every cargo install
aiuse doctor               # WARN when caut enabled + still adhoc
```

Full guide: [`macos-keychain-trust.md`](macos-keychain-trust.md).  
If you do not want caut at all: set `enabled = false` in `[collectors.caut]`.

**CodexBar:** Team-signed app; if **CodexBarCLI** prompts for **CodexBar Cache**,
run `aiuse trust fix-codexbar-cache` ([#679](https://github.com/steipete/CodexBar/issues/679)).
Claude OAuth “Avoid Keychain prompts” prefs are separate (`aiuse trust status`
reports them read-only).

---

## caut issues (observed 2026-07-25) and workarounds

Investigated against **caut 0.1.0** (`d6dc03d`) on this operator Mac.

### 1. Claude: “Auth missing! Run: claude auth login” but windows sometimes appear

**Symptom:** `authWarning` always present. Rate-limit `primary`/`secondary` appear
on some runs and are **null** on others. `caut doctor` reports no Claude credentials
file, while `claude auth status` shows **logged in** via claude.ai.

**Cause (upstream):** caut’s credential detection does not fully match the modern
Claude CLI / claude.ai OAuth layout. The `claude-oauth` strategy can still fill
windows sometimes; it is flaky.

**Workarounds:**

| Action                                  | Notes                                                                                                                                     |
| --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **Do nothing for ranking**              | cswap remains Claude authority; OpenUsage also reports Claude. caut is a soft peer.                                                       |
| **Retry (aiuse does this)**             | `collect_caut` retries once when no live windows — recovers intermittent oauth hits.                                                      |
| **`claude auth login`**                 | May create the credential path caut expects; worth trying if you want caut Claude cross-checks. Does **not** replace cswap multi-account. |
| **Ignore authWarning when % look sane** | aiuse notes when windows were returned despite the warning.                                                                               |
| **Disable caut**                        | `collectors.caut.enabled: false` or `aiuse --no-caut` if noise bothers you.                                                               |

### 2. Codex: identity only, no weekly %

**Symptom:** caut returns email/org but `primary`/`secondary` null. Verbose log:
`codex-web-dashboard` → “Web dashboard scraping **not yet implemented**”;
`codex-cli-rpc` → “identity only — no rate-limit data”.

**Cause (upstream):** incomplete Codex strategies in caut 0.1.0.

**Workarounds:** Rely on **CodexBar** / **OpenUsage** / **tokscale** for Codex
quotas (already preferred for selection). No local config fixes this until caut
implements web or a richer CLI RPC.

### 3. “unsupported source for provider X: Auto” for most names

**Symptom:** With `--provider all`, gemini, antigravity, cursor, opencode, copilot,
etc. all error. `caut doctor` only exercises Codex + Claude.

**Cause (upstream):** provider descriptors exist; Auto fetch strategies are stubs.

**Workarounds:**

| Action                              | Notes                                        |
| ----------------------------------- | -------------------------------------------- |
| **Default `providers: both`**       | aiuse default — only claude+codex (partial). |
| **Do not use `all` expecting data** | Only increases error notes and runtime.      |
| **Use CodexBar/OpenUsage**          | Full multi-provider coverage.                |

### 4. Concurrent collect sometimes empty while solo claude works

**Symptom:** First `caut usage --provider claude` gets windows; next `both`/full
aiuse run gets identity-only.

**Workaround:** aiuse **one automatic retry** when zero live quota windows.
If still empty, ranking continues without caut (other sources unchanged).

### 5. Optional: `caut serve` for a warm cache

caut can run `caut serve --port 19485` and `caut query` against a background
cache. **aiuse does not use this yet** — would need a separate collector path.
Useful if you want a local caut daemon later; not required for OpenUsage/cswap.

### Diagnostic commands

```bash
caut doctor
caut usage --provider claude --json -v
caut usage --provider both --json
claude auth status          # compare with caut doctor Claude section
```

### What “good enough” looks like for aiuse

| Source           | Need for correct ladder?                                    | Posture / Recommendation                         |
| ---------------- | ----------------------------------------------------------- | ------------------------------------------------ |
| **cswap**        | **Yes** (authoritative Claude multi-account selection)      | **Highly recommended**                           |
| **CodexBar**     | **Yes** (primary multi-provider selection via widget cache) | **Highly recommended** (<2ms via snapshot cache) |
| **tokscale**     | **Yes** (primary Copilot & Cursor selection)                | **Recommended**                                  |
| **OpenUsage.ai** | Overlaps cswap, CodexBar, tokscale, and native OpenCode Go  | **Deprecated** (2026-10-22; app removed)         |
| **caut**         | Best-effort; Claude credential detection flaky              | **Avoid / Disable** (adhoc keychain prompts)     |
| **OpenUsage.sh** | Unnecessary; heavy ~40s CPU export, minimal rate limits     | **Avoid / Disable** (`enabled = false`)          |

---

## Operator policy: full releases

**Do not** cut PyPI + Homebrew releases unless the operator **explicitly** asks
for a full release (or “ship everywhere” / “publish to PyPI and brew”). Git
commits and pushes to `main` remain fine per standing policy.
