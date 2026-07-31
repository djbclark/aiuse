# OpenCode Zen balance: source audit

## Status

**OpenCode Zen and OpenCode Go are totally different services** for billing and
usage. Do not treat a Zen balance as Go headroom, or a spent Go monthly window
as “OpenCode empty” in a way that erases Zen inventory (or the reverse).

|                   | **OpenCode Go**                                    | **OpenCode Zen**                                        |
| ----------------- | -------------------------------------------------- | ------------------------------------------------------- |
| Product           | Subscription / plan allotment                      | Prepaid wallet                                          |
| Meter             | 5h / weekly / monthly % windows (shared allotment) | USD balance (no expiry cycle)                           |
| `aiuse` provider  | `opencode-go`                                      | `opencode-zen`                                          |
| Ladder band       | use-or-lose / empty from windows                   | `n/a` inventory, or `empty` if balance ≤ 0              |
| Exhausted meaning | TUI _Go limit reached_ / monthly limit             | No prepaid credits left                                 |
| Relationship      | Primary included Go usage                          | Optional overage when Go is spent (“available balance”) |

`aiuse` records Zen as the distinct `opencode-zen` provider whenever CodexBar
returns a `Zen balance` value (or the native Zen collector succeeds).

As of 2026-07-30, `aiuse` has two local client implementations that return the
actual Zen balance: CodexBar and its optional native collector. Both query the
same authenticated OpenCode billing service, so they are a useful transport
cross-check but not independent financial authorities. Its value can be absent
from one refresh and present on a later refresh because the OpenCode web billing
response itself omits it; retain the missing state as missing rather than
reusing a stale balance as if it were live.

## What was checked

| Candidate                    | What it provides                                                                    | Suitable as a Zen-balance source?                       |
| ---------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------- |
| CodexBar, web source         | Authenticated OpenCode billing data, including Zen balance when OpenCode returns it | Yes; current authoritative source                       |
| OpenUsage.ai                 | OpenCode Go 5-hour/weekly/monthly dollar-cap estimates from local activity          | No; it is not a Zen wallet and may diverge from billing |
| OpenUsage.sh                 | Local telemetry and API-equivalent costs                                            | No; it does not query the Zen wallet                    |
| CodexBar `opencode` provider | OpenCode subscription endpoint, not the Go/Zen billing endpoint                     | No; current live check returned an OpenCode API error   |

CodexBar's [OpenCode notes](https://github.com/steipete/CodexBar/blob/main/docs/opencode.md)
and [Go usage fetcher](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Providers/OpenCodeGo/OpenCodeGoUsageFetcher.swift)
show that it separately fetches the authenticated workspace billing data for
Zen. This is server-side information, unlike the local estimates.

## Recommended path

`aiuse` now includes an optional native `opencode_zen` collector that
independently implements OpenCode's authenticated workspace billing request.
It automatically resolves `OPENCODE_ZEN_COOKIE` from
`~/.config/aiuse/secretspec.toml` (or an explicit `SECRETSPEC_FILE`); this is
the standard per-user manifest for an installed CLI. Store an existing OpenCode
console Cookie header there if you need a manual setup:

```bash
secretspec set --file ~/.config/aiuse/secretspec.toml OPENCODE_ZEN_COOKIE
aiuse -q --json
```

An `AIUSE_OPENCODE_ZEN_COOKIE` process environment variable is an explicit
override for automation or a temporary session:

```bash
export AIUSE_OPENCODE_ZEN_COOKIE='session=...; other-cookie=...'
aiuse -q --json
```

Optionally set `AIUSE_OPENCODE_ZEN_WORKSPACE_ID` to select a workspace;
otherwise the collector uses the first workspace in the authenticated response.
The cookie value is never stored in TOML, snapshots, output, or error messages.
The `OPENCODE_ZEN_API_KEY` available to the OpenCode client is intentionally
not used: OpenCode documents it for model requests, not a wallet-balance API.

### Refresh from Chrome (interactive, optional)

Install the optional reader, then explicitly refresh the SecretSpec value from
one signed-in Chrome profile:

```bash
pipx inject aiuse browser-cookie3
aiuse credential refresh opencode-zen --from chrome --profile Default
```

The command reads only cookies for `opencode.ai`, validates an authenticated
workspace and a live Zen balance before asking to replace SecretSpec, and never
prints the cookie. It creates the standard manifest if it does not exist. Use
`--dry-run` to check without saving or `--yes` for a confirmed non-interactive
replacement. It is never called by normal collection or the scheduled snapshot
agent. See `aiuse credential refresh --help` for the provider-generic command
interface.
`aiuse` runs this collector alongside CodexBar and records a cross-check when
both produce a balance.

This gives us **two independent client implementations** and makes
collection more resilient to a CodexBar regression, cache failure, or release
delay. It would **not** be two independent upstream providers: both values
would come from OpenCode's billing service and should be described as a
transport/implementation consistency check, not two corroborating financial
measurements.

Do not use OpenUsage's local dollar estimates as a Zen fallback or merge them
with Zen; they measure different services. A true second upstream provider
would require an official public Zen billing API or another tool that queries
that same official balance and exposes its provenance. None was found in the
installed collector set during this audit.
