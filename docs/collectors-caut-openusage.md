# caut + OpenUsage collectors

**Date:** 2026-07-25  
**Status:** Enabled by default for multi-source cross-checks (correctness over speed).

## Why

`aiuse` prefers ranking quality over a single data path. In addition to
**cswap**, **CodexBar**, and **tokscale**, it can also collect:

| Source                                                                      | Role                                                        |
| --------------------------------------------------------------------------- | ----------------------------------------------------------- |
| **[caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker)** | Cross-platform CLI usage probe (`caut.v1` JSON)             |
| **[OpenUsage](https://www.openusage.ai/)**                                  | Menu bar app + CLI and/or `http://127.0.0.1:6736/v1/limits` |

Primary selection still follows priority (Claude → cswap; Copilot → tokscale;
others → CodexBar first). **All live sources are pair-wise cross-checked.**

## Machine install

**All five aiuse data sources** (cswap, CodexBar, caut, OpenUsage, tokscale):

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

### OpenUsage

```bash
just install-openusage
# brew install --cask openusage
```

**Human step (once):**

1. Open **OpenUsage.app** (already under `/Applications` after cask install).
2. Complete any first-run provider enablement / keychain prompts.
3. **Settings → Command Line → Install…** so `openusage` is on PATH  
   (optional if you always leave the app running — `aiuse` falls back to HTTP).

`aiuse` collector order for OpenUsage:

1. `openusage --force` when CLI is on PATH (fresh read).
2. Else `GET http://127.0.0.1:6736/v1/limits`.
3. Else try `open -ga OpenUsage` and retry HTTP.

## Config (`~/.config/aiuse/services.yaml`)

```yaml
collectors:
  caut:
    enabled: true
    # both = claude+codex (only pair caut fills windows for reliably)
    # all  = every name caut knows — most error "unsupported source Auto"
    providers: both
  openusage:
    enabled: true
    force_refresh: true
    try_launch_app: true
    base_url: "http://127.0.0.1:6736"
    # Doctor / preflight probe path (payload collect still uses base_url + /v1/limits).
    health_path: "/v1/limits"
    # Or full URL override:
    # probe_url: "http://127.0.0.1:6736/v1/limits"
```

`health_path` / `probe_url` apply to doctor and optional HTTP preflight: “is the
loopback up?” without treating a 404 on `/` as collector death when the payload
path still returns 200. Other collectors accept the same keys when they expose
HTTP; PATH-only tools ignore them.

CLI skips: `--no-caut`, `--no-openusage`.

## Selection priority (generalized)

| Provider | Order (first live wins for the ladder)         |
| -------- | ---------------------------------------------- |
| Claude   | cswap → CodexBar → caut → OpenUsage → tokscale |
| Copilot  | tokscale → CodexBar → caut → OpenUsage         |
| Default  | CodexBar → caut → OpenUsage → tokscale         |

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
If you do not want caut at all: `collectors.caut.enabled: false` in
`services.yaml`. CodexBar is Team-signed (different failure mode — use its
Settings / “Avoid Keychain prompts” class of options; `aiuse trust status`
reports related prefs read-only).

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

| Source                      | Need for correct ladder?                                        |
| --------------------------- | --------------------------------------------------------------- |
| cswap + CodexBar + tokscale | **Yes** (primary selection)                                     |
| OpenUsage                   | Strong cross-check; keep app running or install CLI             |
| caut                        | Best-effort; valuable when Claude windows land; safe when empty |

---

## Operator policy: full releases

**Do not** cut PyPI + Homebrew releases unless the operator **explicitly** asks
for a full release (or “ship everywhere” / “publish to PyPI and brew”). Git
commits and pushes to `main` remain fine per standing policy.
