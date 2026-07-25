# caut + OpenUsage collectors

**Date:** 2026-07-25  
**Status:** Enabled by default for multi-source cross-checks (correctness over speed).

## Why

`aiuse` prefers ranking quality over a single data path. In addition to
**cswap**, **CodexBar**, and **tokscale**, it can also collect:

| Source | Role |
| ------ | ---- |
| **[caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker)** | Cross-platform CLI usage probe (`caut.v1` JSON) |
| **[OpenUsage](https://www.openusage.ai/)** | Menu bar app + CLI and/or `http://127.0.0.1:6736/v1/limits` |

Primary selection still follows priority (Claude → cswap; Copilot → tokscale;
others → CodexBar first). **All live sources are pair-wise cross-checked.**

## Machine install (this operator: site-djbclark)

From `~/ops/site-djbclark`:

```bash
just install-ai-quota-tools   # OpenUsage cask + caut via cargo
just ai-quota-status
just brew-project            # claim openusage cask in Merged-Brewfile
```

### caut

```bash
just install-caut
# or:
cargo install --locked --git https://github.com/Dicklesworthstone/coding_agent_usage_tracker
ln -sfn ~/.cargo/bin/caut ~/.local/bin/caut
```

Requires Rust/`cargo` on PATH. Binary: `caut`.

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
    providers: all          # correctness: query every caut-supported provider
  openusage:
    enabled: true
    force_refresh: true
    try_launch_app: true
    base_url: "http://127.0.0.1:6736"
```

CLI skips: `--no-caut`, `--no-openusage`.

## Selection priority (generalized)

| Provider | Order (first live wins for the ladder) |
| -------- | -------------------------------------- |
| Claude | cswap → CodexBar → caut → OpenUsage → tokscale |
| Copilot | tokscale → CodexBar → caut → OpenUsage |
| Default | CodexBar → caut → OpenUsage → tokscale |

Adding another source later: implement `collect_*`, append to `run_collectors`
jobs, add to `PROVIDER_SOURCE_PRIORITY` / `DEFAULT_SOURCE_PRIORITY` and
`SOURCE_LABELS`. Cross-checks are all-pairs among live sources.

## Secrets

No new secrets for caut/OpenUsage in normal operation — they reuse local
cookies/keychain/CLI auth like CodexBar. Do **not** put provider tokens into
aiuse config. Site `secretspec` is not required for these two tools.
