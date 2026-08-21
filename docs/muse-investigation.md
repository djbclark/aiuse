# Muse support investigation

**Date:** 2026-08-21
**Status:** Investigation only — no code changes (concurrent agent has unrelated dirty work on `main`)
**Question:** (a) Do any upstream collectors already support Muse (Muse Code / Muse Spark)? (b) How hard would direct `aiuse` support be, modelled on an existing native collector?
**Related:** [`AGENTS.md`](../AGENTS.md), [`README.md`](../README.md), [`docs/source-coverage.md`](source-coverage.md), [`docs/provider-identity.md`](provider-identity.md), [`docs/collectors-caut-openusage.md`](collectors-caut-openusage.md), [`docs/zai-quota.md`](zai-quota.md), [`docs/clinepass-quota.md`](clinepass-quota.md), [`docs/opencode-go-quota.md`](opencode-go-quota.md)

---

## TL;DR

- **(a) No upstream provider ships Muse today.** Of the five collectors `aiuse` shells out to, plus the two extra native collectors (`openrouter`, `clinepass`/`opencode`), **none reports a `muse` / `meta` / `metamuse` provider** in any stable release. One tracking issue exists — [robinebers/openusage#1078](https://github.com/robinebers/openusage/issues/1078) ("Support new Muse Code from Meta", opened 2026-08-06) — but it has no PR, branch, fork, or beta artifact. No CodexBar provider, no `caut` probe, no `tokscale` client, and no `cswap`-class helper mention Muse in code or issues.
- **(b) Direct support is viable at "one native collector" cost — estimate 4–12 h / ~0.3–1 M tokens, same bucket as [Issue #17](https://github.com/djbclark/aiuse/issues/17) (OpenRouter native) and [Issue #16](https://github.com/djbclark/aiuse/issues/16) (DeepSeek second source).** Muse Code's local surface is narrow and stable: a single Meta Model API behind `https://api.meta.ai/v1`, a device-flow login (`muse login`) and `META_API_KEY` env override, and a billing dashboard at `https://dev.meta.ai/usage`. The unknown is the exact quota endpoint shape — it will need a 15-minute browser-network trace against an authenticated `dev.meta.ai` session, then a collector patterned on [`src/aiuse/collectors/openrouter.py`](../src/aiuse/collectors/openrouter.py) (prepaid balance) or [`src/aiuse/collectors/clinepass.py`](../src/aiuse/collectors/clinepass.py) (subscription windows). Until that trace is done, the ladder impact is `n/a` (pay-as-you-go), not burn/conserve.

The rest of this doc shows the receipts for (a) and a concrete build plan for (b).

---

## What "Muse" is

- **Product:** `muse` — Meta's terminal coding agent, announced 2026-08-05. Ships as a self-updating wrapper at `~/.local/bin/muse` → `muse-bin-0.2.1-R1215.1` (seen on this machine: `0.2.1-R1215.1`, channel `muse-stable`, manifest `https://lookaside.facebook.com/lookaside/muse/download/?channel=muse&version=0.2.1-R1215.1&file=manifest.json`).
- **Model:** Muse Spark 1.2 (1 M context), also served as `muse-spark-1.2` / `muse-spark-1.2-contributor` via the Meta API and via `opencode-go` proxy.
- **Auth:** two paths, in priority order (from `strings muse-bin-*` and `muse --help` / `muse auth set --help` / `muse login --help` on this host):
  - `META_API_KEY` env var — single-line Model API key, read verbatim, never logged.
  - `muse login` — OAuth device flow: `POST https://auth.meta.com/oidc/device/authorization` (`client_id`, `device_code`), poll `https://auth.meta.com/oidc/device/token` (`urn:ietf:params:oauth:grant-type:device_code`), mint at `https://api.meta.ai/v1` (`x-api-version 1.0.0`, `Authorization: Bearer …`). Stored locally under `~/Library/Application Support/Muse/` (`session-name-authority/authority.json` today is just the ephemeral session-name authority, not the credential — the actual token is in the OS credential store; see below). `muse auth set --provider meta --api-key-stdin` is the non-interactive key path.
- **Billing:** pay-as-you-go on the Meta Model API — not a seat subscription — managed at `https://dev.meta.ai` (`/billing`, `/usage`). Public pricing and limits (from vendor docs + press, not from a quota API):
  - Standard: $1.25 / 1 M input tokens, $4.25 / 1 M output; rate limits 3 000 req/min, 4 M tokens/min.
  - Contributor (data-sharing): $0.10 / 1 M input; 60 req/min, 2.1 M tokens/min.
  - No 5 h / weekly / monthly subscription windows are advertised. The ladder question is therefore "how much spend remains?" (`n/a` prepaid/`payg_api` band), not "burn before reset".
- **Local CLI surface:** `muse --version`, `muse login|logout`, `muse auth set`, `muse exec`, `muse resume`, `muse trace inspect`, `muse /usage` (inside the TUI — session token/cost display, not a provider quota API). No `muse usage --json` or `muse quota` subcommand exists.

This matters for ranking: if Muse stays pure PAYG, `aiuse` will show it as `n/a  muse  —  balance $X` (like `openrouter`/`deepseek`), not as 5H/WEEK/MONTH burn bars. If Meta later adds a contributor weekly credit pool, a native collector can promote to `SUBSCRIPTION_WINDOW` at that time without changing the provider id.

---

## (a) Upstream survey — does anyone already support Muse?

Method: for each collector that `aiuse` actually shells out to, we (1) listed the discoverable provider set, (2) grepped code/docs for `muse`/`meta`, (3) searched GitHub issues/PRs (including forks/betas), (4) checked the local install on this machine where possible. The five shell-out sources are authoritative for (a) — `openrouter`/`opencode`/`clinepass` are `aiuse`-native and therefore not "upstream" in the sense of the question, but we checked them anyway; they also do not mention Muse.

### Results

| Upstream                                                                                                          | Provider surface checked                                                                                                                                                                                                                                                                                                          | Muse hit?                                       | Detail                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **CodexBar** (`steipete/CodexBar`, CLI `codexbar usage --format json`, `codexbar config providers --format json`) | 69 providers enumerated on this host (`abacus` … `zoommate`): `abacus, aiand, alibaba, …, windsurf, xai, zai, zed, zenmux …` — **no `muse`, `meta`, `metamuse`, `musecode`, `spark`** substring. `codexbar usage --provider kilo --format json` returns the Kilo error (unrelated product) as proof the lookup is provider-keyed. | **No**                                          | GitHub search `steipete/CodexBar` for `muse` returns one CLOSED issue unrelated to Muse support: [#3103](https://github.com/steipete/CodexBar/issues/3103) (`opencode-go/muse-spark-1.2-contributor 502` — a truncation report when `muse-spark` is used _through_ `opencode-go`, not a CodexBar Muse provider). Search for `meta` returns only repository-hygiene / crash PRs, no Muse provider proposal. `packaging/install-deps.sh` does not list Muse as a CodexBar dependency.                                                                                  |
| **OpenUsage.ai** (`robinebers/openusage`, app + CLI `openusage --force` / `GET http://127.0.0.1:6736/v1/limits`)  | App uses provider plugins under `~/.openusage` / in-repo `plugins/`. Issue tracker is the public registration point for new providers.                                                                                                                                                                                            | **No shipped support; one open tracking issue** | [robinebers/openusage#1078](https://github.com/robinebers/openusage/issues/1078) — titled **"Support new Muse Code from Meta"**, label `provider`, opened 2026-08-06 by @hoandesign. Body: provider website `https://dev.meta.ai/`, usage data accessible via `https://dev.meta.ai/usage/`, "Will you build the provider? Yes, I will make a PR". Comments: 0. No linked PR, no branch, no fork, no `beta` tag, no closed duplicate. Web search for `openusage muse` returns only this issue plus unrelated pedagogical "Project MUSE" hits — no second-source fork. |
| **OpenUsage.sh** (`janekbaraniewski/openusage`, CLI `openusage-sh export --output - --format json`)               | Telemetry daemon + export. `openusage-sh` provider set mirrors OpenUsage.ai's plugin set plus its own `rate_limit_*` / `plan_percent_used` metric families.                                                                                                                                                                       | **No**                                          | No `muse`/`meta` provider in export docs or issue search. The only "meta" hit in this repo is noise. Collector is not a plausible first source for Muse (no OAuth dance for Meta).                                                                                                                                                                                                                                                                                                                                                                                   |
| **caut** (`Dicklesworthstone/coding_agent_usage_tracker`, CLI `caut usage --json`)                                | Rust CLI that probes ~16 providers (CodexBar-class). Installed via `cargo install --locked --git …`.                                                                                                                                                                                                                              | **No**                                          | `command -v caut` is missing on this host; `caut --help` unavailable. GitHub search `Dicklesworthstone/coding_agent_usage_tracker` for `muse` and for `meta` → zero issues/PRs. No docs page mentions Muse. `caut` provider list (from README) does not include Meta/Muse.                                                                                                                                                                                                                                                                                           |
| **tokscale** (`junhoyeo/tokscale`, CLI `tokscale usage --json`, alias `tokscale usage --json --today/--month`)    | Independent subscription-meter; supports Token Analysis across `codex`, `claude`, `copilot`, `grok`, `antigravity`, `cursor`, etc., via local JSONL / cost sync, not OAuth quota endpoints.                                                                                                                                       | **No**                                          | `tokscale usage --help` on this host is not reachable without npm cache fix (EPERM), but the provider set is documented in-repo and none is `muse`/`meta`. GitHub search `junhoyeo/tokscale` for `muse` → invalid-repo query plus zero hits; for `meta` → only unrelated `GSD` / timezone issues. No fork advertising Muse.                                                                                                                                                                                                                                          |
| **cswap** (`realiti4/claude-swap`, CLI `cswap list --json`)                                                       | Claude-only multi-account swapper — not a candidate for Muse by design, checked for completeness.                                                                                                                                                                                                                                 | **No**                                          | No Muse reference expected or found.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Native collectors already in `aiuse`**                                                                          | `openrouter` (`https://openrouter.ai/api/v1/credits`), `clinepass` (`https://api.cline.bot/api/v1/users/me/plan/usage-limits`), `opencode_go`/`opencode_zen` (cookie to `https://opencode.ai/_server`), `hermes` (local `~/.local/state/hermes/*/latest-usage.json`)                                                              | **No**                                          | None references Muse. `opencode-go` via `opencode.ai/zen/go/v1` does forward `muse-spark-1.2-contributor` _as a model through_ the Go subscription, but that is consumption _through_ `opencode-go`, not independent Muse quota. The only Muse string inside the Muse binary itself is the identity banner ("Muse Code powered by Muse Spark") and the `muse-code/key` problem body.                                                                                                                                                                                 |

### What we did not find

- No **beta** flag that enables Muse in any of the above (CodexBar has no `--beta` providers path that we saw; OpenUsage has no `beta` plugin channel).
- No **fork** that adds Muse — GitHub search across forks of `robinebers/openusage` and `steipete/CodexBar` for `muse` returned only the single OpenUsage issue above; CodexBar's one Muse-related issue is closed and unrelated.
- No **open PR** for Muse support in any of the upstream repos as of 2026-08-21 (checked via `gh search issues` and `gh issue view`).

### How to re-verify later

```bash
# CodexBar provider registry (authoritative local list)
codexbar config providers --format json | python3 -c "import json,sys; d=json.load(sys.stdin); print('\n'.join(sorted(p['provider'] for p in d)))"
# → 69 entries today; look for muse/meta/musecode/spark

# OpenUsage tracking issue
gh issue view 1078 --repo robinebers/openusage --comments
# → currently 0 comments, no PR

# Broad searches (cover forks + issues + discussions)
gh search issues --repo steipete/CodexBar "muse" --limit 20
gh search issues --repo robinebers/openusage "muse" --limit 20
gh search issues --repo Dicklesworthstone/coding_agent_usage_tracker "muse" --limit 20
```

If OpenUsage #1078 merges, `aiuse` will pick it up automatically through the existing `openusage_ai` / `openusage_sh` collectors with no `aiuse` code change — but the `aiuse` ladder will still want its own native Muse collector for parity with `openrouter` (second prepaid source).

---

## (b) How hard is direct support?

### Billing model determines the ladder slot

- **If Muse stays pure PAYG** (today's pricing): the correct `aiuse` modelling is `BillingKind.PAYG_API` or `PREPAID_BALANCE` with `balance_usd` / `credits_remaining` and `window_minutes=None`. It renders in the `n/a` band (like `openrouter` / `deepseek`), never as a `use`/`slow` burn bar. Rate-limit counters (60 or 3000 req/min) are not subscription windows and should not become `QuotaWindow`s with pace scoring — they are second-scale throttles, analogous to Claude's 5 h `refill_capacity` notice, not to a monthly allotment. The value of native Muse support in this world is **inventory visibility and cross-checking**, not urgency ranking.
- **If Meta later ships a subscription/credit pool** (e.g. a `contributor weekly` allotment like `z.ai Lite`'s 2k/10k or `clinepass`'s 5 h/weekly/monthly nesting): the same collector upgrades to `SUBSCRIPTION_WINDOW` with `window_minutes` (300 / 10080 / 43200) and `provider_overrides.<muse>.shared_allotment` if windows nest. No provider-id change required.

Plan for the PAYG case now, keep the window path open.

### Credential surface — what we know and what needs confirming

| Source                                             | How `aiuse` would read it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Precedent in-tree                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `META_API_KEY` env var (explicit)                  | `env.get("AIUSE_MUSE_API_KEY")` / `META_API_KEY` with `environ` override, `str(...).strip()` — no secretspec by default                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | [`collectors/openrouter.py`](../src/aiuse/collectors/openrouter.py) (`AIUSE_OPENROUTER_MANAGEMENT_KEY` + `OPENROUTER_MANAGEMENT_KEY` via `secretspec get`), [`collectors/clinepass.py`](../src/aiuse/collectors/clinepass.py) (`AIUSE_CLINE_API_KEY` + `sudo-secretspec get CLINE_API_KEY`)                                                                                                                                                                                                                                                                             |
| Stored login from `muse login` (device flow token) | macOS Keychain item — service.`muse-code/key` problem body observed in strings; actual storage verb not confirmed on this host because `security find-generic-password -s muse*` returned "not found" (login not performed here). Needs a one-time `muse login` on a credentialed machine + `strings` / Keychain Access inspection to confirm the exact item. Alternatively, read the token from the Muse credential helper directly if it exposes a `muse auth export` or file under `~/Library/Application Support/Muse/` (not yet observed — only `session-name-authority/` exists pre-login). | [`collectors/opencode_zen.py`](../src/aiuse/collectors/opencode_zen.py) + [`collectors/opencode_go.py`](../src/aiuse/collectors/opencode_go.py) — cookie via `AIUSE_OPENCODE_ZEN_COOKIE` or `secretspec get OPENCODE_ZEN_COOKIE`; never writes the secret to snapshots/logs/errors. If Muse's stored token ends up file-readable, the same `shutil.which("secretspec")` + `subprocess.run(["secretspec","get",…])` pattern applies; if it is Keychain-only, we follow CodexBar's approach (read through the tool) or add a `muse auth export` helper if Meta ships one. |

Open questions to close during implementation (all cheap — 15 min each):

1. What is the post-login token location and format? (Keychain service name, account, file path, JWT vs opaque key, header name `Authorization: Bearer` vs `x-api-key`).
2. What quota/billing endpoint is authoritative?
   - Web dashboard: `https://dev.meta.ai/usage` / `/billing` — likely SSR for humans, but its XHR may be `GET https://api.meta.ai/v1/billing/usage` or `GET https://api.meta.ai/v1/me/credits`. Capture in browser DevTools while logged into `dev.meta.ai` (Network → filter `api.meta.ai` → copy as cURL).
   - CLI side: does `https://api.meta.ai/v1` expose `GET /v1/usage`, `GET /v1/credits`, `GET /v1/limits`, or `GET /v1/billing` when called with the same bearer? `curl -H "Authorization: Bearer $META_API_KEY" https://api.meta.ai/v1/models` already returns `{"error":{"code":"invalid_api_key"…}}` when unauthenticated — the base is live and speaks OpenAI-compatible JSON. The quota path is the only missing piece.
   - If no JSON endpoint is public, fallback is scraping `dev.meta.ai` HTML (brittle, like early `opencode_go` page parsing — workable but worth avoiding if a JSON endpoint exists). Check `openapi.json` / `GET /v1` discovery if advertised.
3. Response shape: is it `{"data":{"total_credits":..,"total_usage":..}}` like OpenRouter (`data.total_credits` / `data.total_usage`), or `{"limits":[{"type":"five_hour",…}]}` like ClinePass (`percentUsed` / `resetsAt`), or a dollar-bucket (`available` / `remaining`)? This determines whether we emit one `balance_usd` row or multiple `QuotaWindow`s.
4. Rate-limit headers vs windows: does `429` / `x-ratelimit-*` expose `requests remaining`? If so, note it but do not rank.

### Why the difficulty is "one native collector"

Compare the three native collectors already in `aiuse`:

| Collector                      | Endpoint                                                            | Auth                                                                             | Parsing                                                            | LoC                                                  | Failure mode when absent                   |
| ------------------------------ | ------------------------------------------------------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------------ | ---------------------------------------------------- | ------------------------------------------ |
| `openrouter`                   | `GET https://openrouter.ai/api/v1/credits`                          | `AIUSE_OPENROUTER_MANAGEMENT_KEY` / `secretspec get OPENROUTER_MANAGEMENT_KEY`   | `total_credits - total_usage` → `balance_usd`                      | ~130 lines including `_resolve_key`                  | returns `[]` (absent) so ladder unaffected |
| `clinepass`                    | `GET https://api.cline.bot/api/v1/users/me/plan/usage-limits`       | `AIUSE_CLINE_API_KEY` / `sudo-secretspec get CLINE_API_KEY`                      | array of `{type, percentUsed, resetsAt}` → three `QuotaWindow`s    | ~150 lines including `_resolve_api_key` + window map | returns `[]` or single error row           |
| `opencode_zen` / `opencode_go` | `GET https://opencode.ai/_server?id=…` + `/workspace/<id>/go`       | cookie `AIUSE_OPENCODE_ZEN_COOKIE` / `secretspec get OPENCODE_ZEN_COOKIE`        | JSON + regex fallback over Solid `$R[n]=` wrappers                 | ~200 lines + workspace discovery                     | returns `[]` when cookie absent            |
| **Muse (proposed)**            | `GET https://api.meta.ai/v1/…` (usage/credits/billing/limits — TBC) | `META_API_KEY` / `AIUSE_MUSE_API_KEY` / login token via Keychain or `secretspec` | shape TBC (one of the two rows above) → `balance_usd` or `windows` | ~120–180 lines estimated                             | returns `[]` when unauthenticated          |

All follow the same lifecycle: resolve a secret (env → secretspec/Keychain), `requests.get` with `timeout`, `response.raise_for_status`, `response.json`, `CollectorError` on HTTP/JSON failures, return `list[AccountUsage]` with `source="muse_api"` (or `muse_native`), `provider="muse"`, `billing_kind`, and `notes=["Live data fetched directly from Muse …"]`. The pattern is well-trodden; the only variable is the endpoint contract.

---

## Implementation plan (no code in this patch — doc only)

Conventional file placement mirrors the existing native collectors. Every step cites the file it touches so a second agent can land it without guessing.

### 0. Provider identity (1 file, 5 min)

- **File:** [`src/aiuse/models.py`](../src/aiuse/models.py)
- **Change:** add to `PROVIDER_DISPLAY_NAMES` and to the `PROVIDER_ID_ALIASES` / `EXTERNAL_PROVIDER_ALIASES` tables if Muse has an alias (e.g. `metamuse → muse`, `muse-spark → muse`, `meta-ai → muse`). Canonical id is **`muse`** (lowercase, grep-able, like `claude`/`codex`/`grok`). Display name is **`muse`** (no family prefix needed; Muse is already the CLI you type). Keep the alias map conservative — only add what a collector actually emits.
- **Why here:** `canonical_provider()` is the identity key; every other file must use it. See [`provider-identity.md`](provider-identity.md).
- **Risk:** none; alias table is additive and all reads canonicalize.

### 1. User-visible plan + pricing (1 file, 5 min)

- **File:** [`src/aiuse/config.py`](../src/aiuse/config.py)
- **Change:** add to `DEFAULT_CONFIG["plans"]`:
  ```python
  "muse": {
      "name": "Muse Spark / Muse Code",
      "notes": "Pay-as-you-go via Meta Model API ($1.25/$4.25 per 1M; contributor $0.10/$4.25). Rate limits 3k/4M tokens/min (contributor 60/2.1M). No subscription windows.",
      "monthly_price": None,  # or 0 with notes — no subscription
  }
  ```
  Add `provider_overrides` entry only if subscription windows materialise (`shared_allotment` when 5 h ⊂ weekly ⊂ monthly like `opencode`/`zai`/`clinepass`).
- **Also:** extend `KNOWN_TIMEOUT_KEYS` and `KNOWN_COLLECTOR_KEYS` if the new collector gets its own `timeouts.muse` key (mirror `openrouter`).

### 2. Native collector (1 new file, ~120–180 lines)

- **File:** `src/aiuse/collectors/muse.py` (new)
- **Shape, mirroring `openrouter.py` / `clinepass.py`:**
  ```python
  """Collect Muse balance/quota directly via the Meta Model API."""
  _API_URL = "https://api.meta.ai/v1/…"  # TBC after trace: /usage | /credits | /billing
  _KEY_ENV = "AIUSE_MUSE_API_KEY"          # explicit override
  _FALLBACK_ENV = "META_API_KEY"           # vendor standard
  _KEY_SECRET = "MUSE_API_KEY"             # secretspec name (or META_API_KEY)
  _SECRETSPEC_TIMEOUT = 5.0
  _USER_AGENT = "aiuse Muse collector"

  def collect_muse(*, timeout: float = 45.0, environ=None) -> list[AccountUsage]:
      key = _resolve_key(env, timeout)     # AIUSE_MUSE_API_KEY → META_API_KEY → secretspec / Keychain
      if not key:
          return []                        # absent credential → no row, no error (like openrouter)
      resp = requests.get(_API_URL, timeout=timeout, headers={"Authorization": f"Bearer {key}", "User-Agent": _USER_AGENT})
      if resp.status_code in (401, 403):
          return [AccountUsage(source="muse_api", provider="muse", error="Muse API rejected the key (HTTP 401/403). Check META_API_KEY / dev.meta.ai onboarding.", billing_kind=BillingKind.PAYG_API)]
      resp.raise_for_status()
      data = resp.json()
      # Branch A (prepaid balance): data["data"]["total_credits"] / total_usage → balance_usd
      # Branch B (windows):        data["limits"] / data["data"]["limits"] → QuotaWindow per type
      # Emit one AccountUsage; BillingKind.PAYG_API or PREPAID_BALANCE or SUBSCRIPTION_WINDOW accordingly
  ```
- **Credential helper:** `_resolve_key(env, timeout, allow_secretspec=environ is None)` — identical to `openrouter._resolve_key` (env → `shutil.which("secretspec")` → `subprocess.run(["secretspec","get","--file",manifest,"--reason","aiuse Muse …", _KEY_SECRET])`), plus optional Keychain read if login-storage turns out to be Keychain-only. On this machine `muse auth set --provider meta --api-key-stdin` is the documented staging path; reuse it.
- **Error contract:** `CollectorError` on timeout / HTTP 5xx / invalid JSON (so `runner.py` records `collector_errors`), returned `AccountUsage(error=…)` only on 401/403 with an actionable message (so the table shows the misconfiguration rather than hiding it).
- **Testing:** follow `tests/test_collector_*.py` pattern — mock `requests.get` and `environ`, assert happy path (balance or three windows), 401 row, empty-key `[]`, and `CollectorError` on 5xx/timeout.

### 3. Wire into the runner (1 file, ~15 lines)

- **File:** [`src/aiuse/collectors/__init__.py`](../src/aiuse/collectors/__init__.py) — export `collect_muse`.
- **File:** [`src/aiuse/collectors/runner.py`](../src/aiuse/collectors/runner.py)
  - Add `"muse"` (or `"muse_api"`) to `DEFAULT_SOURCE_PRIORITY` and `SOURCE_LABELS` (`"muse_api": "Muse (native)"`).
  - Add `PROVIDER_SOURCE_PRIORITY["muse"] = ("muse_api", "codexbar", "caut", "openusage_ai", "tokscale", "openusage_sh")` if Muse ever appears in those peers (otherwise the default priority is fine).
  - Add a `jobs.append(("muse_api", partial(collect_muse, timeout=timeout_for(config,"muse"))))` branch inside `run_collectors`, gated by `_enabled(collectors_cfg,"muse")`. Respect `timeout_for`.
  - No change to `codexbar` / `caut` / `openusage` parsing — they will capture Muse automatically once an upstream emits `provider: "muse"`; the native collector is just the authoritative second source pattern (cf. `openrouter`'s two sources for one provider).

### 4. Config + docs (2–3 files, 10 min)

- **File:** `config/config.example.toml` — add a `[collectors.muse]` example stanza (mirrors `openrouter`'s, but for Muse):
  ```toml
  # [collectors.muse]
  # enabled = true  # uses META_API_KEY / AIUSE_MUSE_API_KEY, or: muse login / muse auth set
  ```
- **File:** `docs/muse-quota.md` (new, post-implementation) — provider note mirroring `zai-quota.md` / `clinepass-quota.md` / `opencode-go-quota.md`: table of billing model, rate limits, what `aiuse` shows (ladder row `n/a  muse  —  balance $X` vs windows), `analysis.provider_overrides.muse.shared_allotment` note if needed, verify commands (`AIUSE_MUSE_API_KEY=… aiuse --json | jq '…'`).
- **File:** `docs/source-coverage.md` — add a row for `Muse (Spark)  | native muse_api | prepaid/payg, rate-limited` once the second source exists.
- **File:** `docs/index.md` — list the new `muse-quota.md` under "Provider and collector notes". This is doc-only; keep the concurrent feature agent's index edits conflict-free by appending, not rewriting.
- **File:** `README.md` — add Muse to the vendor table (`muse` CLI, `muse` provider) and to the "five external data sources" count when the collector ships.

### 5. Quality gates

```bash
.venv/bin/python -m pytest -q                      # no regressions; new collector tests pass in isolation
.venv/bin/python -m pytest tests/test_collector_muse.py -q
just ci                                            # same as GitHub Actions (lint + types + tests)
aiuse --json -q | python3 -m json.tool | head -n 80  # without key: no muse row, no collector_errors spike
AIUSE_MUSE_API_KEY=sk_test aiuse --json -q          # with key: one muse row, correct billing_kind
```

### Effort and sequencing

- **Discovery spike before coding (0.5 h, blocks the rest):** log into `https://dev.meta.ai`, open Network, visit `https://dev.meta.ai/usage`, copy the XHR that populates the balance/limits tile, `curl` the same path with `Authorization: Bearer $META_API_KEY` to confirm it works outside the browser. If it is GraphQL, note the operation name. This determines Branch A vs B.
- **Collector + wiring (3–6 h):** files above, following `openrouter.py` line-for-line. Most time is endpoint-shape handling, not plumbing.
- **Tests + docs (1–2 h):** `tests/test_collector_muse.py`, `docs/muse-quota.md`, `config.example.toml`, `source-coverage.md` row.
- **Manual verification (0.5–1 h):** two runs (no key / valid key), `aiuse doctor` probe if an HTTP `probe_url` is appropriate, ladder snapshot with `n/a` placement check.
- **Total:** **4–12 h**, same class as Issues #16/#17. No new dependencies (`requests` already required), no schema migration, no `PROVIDER_CONFIG_ALIASES` change unless an alias collision is discovered.

### Alternatives considered

- **Wait for OpenUsage #1078 to land and skip native:** cheapest, but leaves Muse single-sourced and pay-walled behind the OpenUsage app. The `openrouter` precedent shows the value of a second native client of the same billing source for cross-checking and for headless CI.
- **Add a `muse` provider to CodexBar instead:** out of scope for this repo and slower (requires Swift change, release, cask update). The native collector is parallelizable and does not block on upstream cadence.
- **Treat Contributor tier rate limits as subscription windows:** deliberately not done. A 60 req/min throttle is a burst gate, not a use-or-lose allotment. Modelling it as `QuotaWindow` would create false urgency (`use` every minute). If Meta documents a true weekly credit pool, revisit.

---

## What to do next (operator pick)

1. **If Muse is not yet in the portfolio:** no action — the PAYG spend will not burn before reset, so missing it does not distort the ladder. Revisit when either (a) OpenUsage #1078 merges and Muse appears in `GET /v1/limits`, or (b) `dev.meta.ai/usage` shows a subscription pool worth tracking.
2. **If Muse is active and billed:** run the 15-minute trace above and, if the endpoint is clean JSON, implement the collector per the plan. File it as a normal `enhancement` issue (like #17) so the concurrent feature branch does not collide — this investigation doc is the spec.
3. **If the endpoint requires HTML scraping:** either ship a minimal `dev.meta.ai` scrape (pattern: `opencode_go.py`'s `requests.get` + `re.search` with `_USER_AGENT` + `CollectorError` on redirect off-host) or park until Meta documents a quota endpoint. The scraper is workable but carries the same drift risk as early OpenCode Go.

---

## Evidence collected 2026-08-21 (this machine)

- `muse --version` → `Muse Code 0.2.1 (0.2.1-R1215.1)`, stable channel manifest `https://lookaside.facebook.com/…`
- `muse --help` / `muse auth set --help` / `muse login --help` — device-flow hint (`META_API_KEY` priority, `muse login` browser code), `auth set --provider meta --api-key-stdin` shape, base `https://api.meta.ai/v1`.
- `strings muse-bin-*` — `https://api.meta.ai/v1`, `https://auth.meta.com/oidc/device/{authorization,token}`, `https://dev.meta.ai`, `META_API_KEY`, `TBH_MINT_BASE_URL`, `muse-code/key` problem body, `x-api-version 1.0.0`. No `/usage`/`/quota`/`/credits` literal — endpoint name is still unknown.
- `codexbar config providers --format json` → 69 providers, zero with `muse` substring (sorted list in §a).
- `gh search issues` + `gh issue view 1078 --repo robinebers/openusage` → one open Muse tracking issue, zero PRs/branches/forks.
- `curl https://api.meta.ai/v1/models` without key → `{"error":{"code":"invalid_api_key"…}}` — base is live, auth is bearer.
- `~/Library/Application Support/Muse/` pre-login contains only `session-name-authority/{authority.json,session-names.db}` — no credential file until `muse login` or `muse auth set`.
- Dirty working tree `git status` shows concurrent work (`README.md`, `config/config.example.toml`, `src/aiuse/models.py`, `src/aiuse/config.py`, `src/aiuse/report.py`, `src/aiuse/collectors/runner.py`, tests) — this doc is intentionally additive-only.

---

## Checklist for the implementing agent (when green-lit)

- [ ] Run the `dev.meta.ai/usage` XHR trace and paste the endpoint + sample JSON into `docs/muse-quota.md` (redact the bearer).
- [ ] Create `src/aiuse/collectors/muse.py` per §2, with `_resolve_key` and `collect_muse`.
- [ ] Register `muse` in `src/aiuse/models.py` (`PROVIDER_DISPLAY_NAMES`, `PROVIDER_ID_ALIASES` if needed), `src/aiuse/config.py` (`plans.muse`, `KNOWN_*` sets), `src/aiuse/collectors/{__init__.py,runner.py}`.
- [ ] Add `tests/test_collector_muse.py` (mocked `requests.get`; happy + 401 + empty-key + invalid-JSON cases) and `docs/muse-quota.md` + `docs/source-coverage.md` row.
- [ ] `just ci` green, `aiuse --json` shows `provider:"muse"` with `n/a` band when keyed, hidden when not.

Methodology: local file reads + `codexbar`/`muse` CLI probes + `gh` issue searches + web searches for pricing/limits + `strings` on the Muse binary. All upstream claims are scoped to 2026-08-21; re-run `gh issue view 1078` before building.
