# caam collector (Coding Agent Account Manager)

Related reading: [`AGENTS.md`](../AGENTS.md) · [`README.md`](../README.md) ·
[`collectors-caut-openusage.md`](collectors-caut-openusage.md) ·
[`json-contract.md`](json-contract.md)

**Deprecated (2026-10-22).** caam overlaps cswap, CodexBar, OpenUsage.ai, and
tokscale: it echoes vault profiles for claude, codex, grok, and cursor from
behind every direct source. The collector stays in the tree. The maintainer is
happy to re-add first-class support if anyone wants it. caut and OpenUsage.sh
are deprecated for the same overlap reason
([`collectors-caut-openusage.md`](collectors-caut-openusage.md)).

`caam` ([Dicklesworthstone/coding_agent_account_manager](https://github.com/Dicklesworthstone/coding_agent_account_manager))
is an **account vault**: it backs up auth files for AI coding CLIs and switches
between them. aiuse uses it as one more quota source.

## What the collector runs

- `caam limits --format json` — one call, no provider argument. It sweeps the
  providers that have a usage API: **claude, codex, grok, cursor**. No
  `--source live` / `--profile` / `--best` / `--rank` / `--cached` is passed,
  so this reads **vault profiles**, not the live login. Budget: 90 s in aiuse
  (caam's own deadline is 60 s).
- `caam status --json` — local, no network. Non-secret health
  (`caam health: …`, `refresh_due`, `login_required`, `expires_at`, `reason`)
  is copied onto the matching row's notes. A status failure never fails the
  collect.

## Reading the numbers

- `used_percent` from caam is the share **consumed** — 100 means exhausted.
  It maps straight to aiuse `used_percent`, never inverted.
- A window is emitted only when it is measured: skip when missing,
  `unmeasured`, the row's `usage.error` is set, or `quota_status` is
  `unavailable`. A missing window is never a 0%-used window.
- A `rolled` window is an honest 0% used; the window notes that it rolled.
- `resets_at` is passed through only when it is a real timestamp (caam's Go
  zero time is dropped).

## Rows it emits

- Only providers with a measured window get a row. **A tool whose login is
  live but whose vault has no quota row is skipped** — higher-priority
  collectors (cswap, CodexBar, caut, OpenUsage, tokscale, …) already cover
  those vendors. In `PROVIDER_SOURCE_PRIORITY` caam sits immediately before
  `acp` for claude, codex, grok, and cursor, behind every quota source
  including `grok_billing`. `acp` stays last because it is a context fill,
  not a quota peer.
- Provider ids follow [`provider-identity.md`](provider-identity.md): caam's
  `agy` maps to `antigravity`, `gemini` to `antigravity`, `opencode` to
  `opencode-go`; the rest are already canonical.
- `plan` comes only from the payload's `plan_type` or the paired
  `caam status` identity — never guessed. `billing_kind` stays `unknown`.
- `raw` (internal only) is redacted: keys containing `token` / `secret` /
  `authorization` / `cookie` / `refresh` / `access_token` / `id_token` /
  `api_key` and any `eyJ…` / `sk-…` string value are dropped before storage.

## The seven caam tools

caam knows seven tools: `claude`, `codex`, `gemini`, `grok`, `opencode`,
`cursor`, `agy`. **Only claude, codex, grok, and cursor have live limits
APIs** (`limitsProviders` in caam v0.1.23). `caam limits` errors on the
others, so aiuse never asks it for them and emits nothing for gemini,
opencode, or agy quota.

## Install

Optional at runtime (`[collectors.caam] enabled = false` to disable).
`packaging/install-deps.sh` installs the checksum-verified v0.1.23 release
tarball from
[the releases page](https://github.com/Dicklesworthstone/coding_agent_account_manager/releases).

### This machine runs a local build, not v0.1.23

`~/.local/bin/caam` here is a **local build** from
`~/src/coding_agent_account_manager` at commit `7c6bd5d` (built 2026-10-10),
three Grok commits ahead of v0.1.23:

1. `73f93c6` fix(auth): preserve selected credentials during switch and import
2. `b83c03c` fix(usage): never let the Grok billing probe start a browser login
3. `7c6bd5d` feat(grok): keep saved Grok profiles fresh from the live login automatically

It reports `caam dev (unknown)` because a plain `go build` embeds no version
string. `install_caam` early-returns when `caam` is already on PATH, so a
re-run of `install-deps.sh` will **not** replace it with v0.1.23. Revert to the
checksum-verified release only once **v0.1.24** ships (it formalizes these
Grok fixes); until then v0.1.23 is the floor, not what is installed.
