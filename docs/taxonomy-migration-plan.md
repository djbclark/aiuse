# Plan: Migrating `aiuse` from TUI-Centric Display to the `djbclark-ade` 4-Axis Taxonomy

**Status:** Proposed plan (2026-11-16)  
**Sources:** `~/src/djbclark-ade` (`skills/model-routing/SKILL.md`, `config/routing.toml`, `docs/pi-services.md`, `docs/dynamic-model-effort.md`, `bin/route_agent.py`, `bin/quota_signals.py`, `bin/ade-net-resume`), `~/src/aiuse` (`src/aiuse/models.py`, `src/aiuse/report.py`, `src/aiuse/analysis/selfdescribe.py`, `docs/provider-identity.md`, `docs/json-contract.md`), and open issues/PRs in `djbclark/aiuse` and `djbclark/djbclark-ade`.

---

## 1. Why Change

`aiuse` still presents and keys several quotas by **TUI / harness** rather than by **billing vendor/pool**:

- The matrix header prints `TUI` (`src/aiuse/report.py:2368`).
- `PROVIDER_CLI_BINARIES` (`src/aiuse/models.py`) couples each provider to a single TUI binary.
- Display names and canonical IDs still reflect retired or multi-purpose TUIs:
  - `pi` (`SCOPE: codex-api`) for OpenAI organization API credits, even though **Pi** is now the general-purpose third-party TUI driving Cursor Composer, Z.ai, OpenCode Go, ClinePass, OpenAI API, and Anthropic API.
  - `omp` (`SCOPE: claude-api`) for Anthropic API credits, named after the retired `omp` TUI.
  - `clinepass/cline` and `opencode` / `oc-zen` named after retired TUIs.
  - `hermes` listed as a provider even though Hermes is a client/assistant consuming `clinepass`, `opencode-go`, or `xai-oauth`.

Meanwhile, `djbclark-ade` (`skills/model-routing/SKILL.md`, `docs/pi-services.md`, `config/routing.toml`) routes work along **4 independent axes**:

1. **Vendor × Pool** — who owns the billing allotment / rate window (`anthropic`, `openai`, `google`, `cursor`, `zai`, `clinepass`, `opencode-go`, `deepseek`, `bedrock`, `sipb`).
2. **Model / Sublimit** — model-scoped caps and tiers within a pool (e.g. Claude `Fable` 50% weekly sublimit vs `Opus`/`Sonnet`/`Haiku`; Codex `gpt-6-astra` vs `gpt-6.1-sol`; Antigravity `gemini-argon` vs `gem` vs `c/gpt`; Cursor `auto` vs `third_party` vs `grok_bot`).
3. **Effort** — `low` / `medium` / `high` / `xhigh` / `max`, which scales burn rate and carries operator gates (e.g. `fable:xhigh`).
4. **Context Window** — default vs 1M, cost multipliers (e.g. Cursor 2× above 256k), and live ACP / `context-watch` fill.

External industry context (e.g., Anthropic 1M standard pricing on Claude 4.6+, Cursor 2× >256k context billing, multi-provider gateways like LiteLLM/OmniRoute) reinforces keeping **billing pool** separate from **client harness** and **model/context multipliers**.

---

## 2. Target Taxonomy in `aiuse`

### 2.1 Separate Pool from Transport

- **`vendor`** (`str`): billing authority (`anthropic`, `openai`, `google`, `cursor`, `zai`, `clinepass`, `opencode`, `deepseek`, `aws`, `sipb`).
- **`pool_id`** (`str`): canonical `<vendor>:<pool_family>` identifier:
  - `anthropic:subscription`, `anthropic:claude_api` (replacing `omp`)
  - `openai:codex`, `openai:openai_api` (replacing `pi`)
  - `google:gemini`, `google:gemini_argon`, `google:claude_gpt`
  - `cursor:auto`, `cursor:third_party`, `cursor:grok_bot`, `cursor:total`
  - `opencode:go`, `opencode:zen`
  - `clinepass:default`, `zai:default`, `deepseek:default`, `aws:bedrock`, `sipb:default`
- **`transports`** (`list[str]`): clients/harnesses that can spend that pool (e.g. `["claude"]`, `["pi"]`, `["codex"]`, `["cursor-agent", "pi"]`, `["paseo"]`). Replaces the 1:1 `PROVIDER_CLI_BINARIES` assumption.

### 2.2 Mapping the 4 Axes

| Axis                    | Where it lives in `aiuse`                                                           | Board display                                    |
| ----------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------ |
| **1. Vendor × Pool**    | `AccountUsage.vendor`, `AccountUsage.pool_id`, `billing_kind`                       | `POOL` column + `ACCT` column                    |
| **2. Model / Sublimit** | `QuotaWindow.quota_scope` (`pool` \| `model_sublimit` \| `ceiling`) + `models_hint` | `SCOPE` column (`fable`, `argon`, `auto`, `3rd`) |
| **3. Effort**           | `PaceProfile` burn scaling + `semantics.operator_gated_efforts`                     | `OTHER` caveat only when gated                   |
| **4. Context**          | `ContextUsage` (ACP fill) + window/pool notes (e.g. `>256k 2×`, `1M`)               | `OTHER` caveat only when relevant                |

---

## 3. Matrix UI Redesign (`src/aiuse/report.py` & `src/aiuse/models.py`)

1. **Rename `TUI` header → `POOL`** in `src/aiuse/report.py` (`_line(..., f"{'POOL':<{w_service}}", ...)`).
2. **Clean up `PROVIDER_DISPLAY_NAMES` (`src/aiuse/models.py`)**:
   - `omp` → `anthropic-api` (alias `omp` kept for config/history backwards compatibility)
   - `pi` → `openai-api` (alias `pi` kept for config/history backwards compatibility)
   - `clinepass/cline` → `clinepass`
   - `zai/zcode` → `zai`
   - `deepseek/codewhale` → `deepseek`
   - `opencode` / `oc-zen` → `opencode-go` / `opencode-zen`
3. **Preserve compact width (`TABLE_MAX_WIDTH = 110`)**: keep `BAND ## POOL ACCT SCOPE PACE 5H WEEK MONTH OTHER` without adding Effort or Context columns.

---

## 4. JSON Schema Evolution (1.6 Bridge → 2.0)

`~/src/djbclark-ade` consumers (`bin/route_agent.py`, `bin/quota_signals.py`, `bin/ade-net-resume`) currently key on `row["provider"]` and `row.get("pool_family")`. A breaking rename would break routing and auto-resume.

- **Schema 1.6 (additive bridge):**
  - Keep `provider` with legacy canonical values (`claude`, `codex`, `antigravity`, `cursor`, `zai`, `omp`, `pi`, `clinepass`, `opencode-go`).
  - Add `vendor`, `pool_id`, `quota_scope`, and `transports` to each account/`available` entry.
  - Publish `semantics.taxonomy_version = "1.6"`, `semantics.provider_to_vendor`, and `semantics.pool_id_map` in `src/aiuse/analysis/selfdescribe.py`.
- **Schema 2.0 (target):**
  - Promote `pool_id` as primary key; deprecate `omp` and `pi` as `provider` names in favor of `anthropic-api` and `openai-api` (keeping `PROVIDER_ID_ALIASES` for config/CLI inputs).
  - Migrate `route_agent.py`, `quota_signals.py`, and `ade-net-resume` to read `pool_id` / `vendor` with `provider` fallback.

---

## 5. Open `aiuse` & `djbclark-ade` Issues/PRs We Own

| Repo & Item                                                                        | Summary                                                   | How it fits this plan                                                                                                                                         |
| ---------------------------------------------------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`djbclark/aiuse#51`](https://github.com/djbclark/aiuse/issues/51)                 | Non-resettable OpenAI API org spend-limit refusals        | Parse enforced spend-limit refusals in `src/aiuse/quota_refusals.py` (`state: "blocked"`, `usable_now: false`, `resets_at: null`) for `openai:openai_api`.    |
| [`djbclark/aiuse#33`](https://github.com/djbclark/aiuse/issues/33)                 | `agy` CLI 429 burst lockout invisible while ACP works     | Decouple **transport** availability from **pool** quota (`transports.cli.usable: false`, `transports.acp.usable: true`) in `quota_refusals.py` / `models.py`. |
| [`djbclark/aiuse#34`](https://github.com/djbclark/aiuse/issues/34)                 | Sampler spawns `agy` 3× every 180s (~500/day)             | Per-provider sample cadence in `src/aiuse/sampler.py` + empty-probe backoff.                                                                                  |
| [`djbclark/aiuse#35`](https://github.com/djbclark/aiuse/issues/35)                 | `--generate-config` missing `[collectors.*]`              | Verify `_generated_collector_defaults()` covers all `KNOWN_COLLECTOR_KEYS` and `KNOWN_TIMEOUT_KEYS`, then close `#35`.                                        |
| [`djbclark/aiuse#36`](https://github.com/djbclark/aiuse/issues/36)                 | `clinepass` logs error row when unconfigured              | Return `[]` when unconfigured (no env var and no secret/broker), matching `muse`/`qwencloud`/`bailian`.                                                       |
| [`djbclark/aiuse#37`](https://github.com/djbclark/aiuse/issues/37)                 | `test_doctor_*` fails on unpatched `which`                | Thread `which_fn` into `resolve_app_cli(which_fn=which_fn)` in `openusage.py` / `cli.py`.                                                                     |
| [`djbclark/djbclark-ade#299`](https://github.com/djbclark/djbclark-ade/issues/299) | `session-guard` misses Claude 5h window if Paseo lacks it | Fall back to `aiuse`'s measured `fiveHour` window in `quota_signals.py` / `session-guard`.                                                                    |
| [`djbclark/djbclark-ade#141`](https://github.com/djbclark/djbclark-ade/issues/141) | Decide how to gate Amazon Bedrock access                  | Keep `aws:bedrock` as `BillingKind.PAYG_API` in `aiuse` and `never_route` in `djbclark-ade` until gateway budget caps exist.                                  |
| [`djbclark/djbclark-ade#762`](https://github.com/djbclark/djbclark-ade/pull/762)   | Emergency manual ClinePass access in Pi                   | Align `aiuse` display (`clinepass`) and `transports: ["pi"]`.                                                                                                 |
| [`djbclark/djbclark-ade#763`](https://github.com/djbclark/djbclark-ade/pull/763)   | Three named OpenCode Go accounts in Pi                    | Align `aiuse` `ACCT` multi-account display (`djbclark`, `dclark`, `t-e`) and `transports: ["pi"]`.                                                            |

---

## 6. Phased Rollout & Verification

1. **Phase 0 — Baseline Hygiene (`aiuse`):** Fix `#37`, `#36`, verify/close `#35`. Gate: `.venv/bin/python -m pytest -q`.
2. **Phase 1 — Refusal & Transport Decoupling (`aiuse`):** Implement `#51` (spend-limit block), `#34` (per-provider sampler cadence), `#33` (per-transport burst lockout). Gate: unit tests in `tests/test_rate_gates.py` + `tests/test_sampler.py`.
3. **Phase 2 — Schema 1.6 & Matrix Redesign (`aiuse`):** Add `vendor`, `pool_id`, `transports` to `AccountUsage` and `selfdescribe.py`; rename `TUI` → `POOL` and clean `PROVIDER_DISPLAY_NAMES` in `report.py`/`models.py`. Gate: full `pytest` + `COLUMNS=100 AIUSE_SKIP_HOMEBREW_FORMULA=1 ./aiuse-test.sh watch --once`.
4. **Phase 3 — `djbclark-ade` Consumer Alignment:** Land PR `#762` & PR `#763`; update `route_agent.py`, `quota_signals.py`, and `ade-net-resume` to consume `pool_id`/`vendor` (with `provider` fallback); wire `#299` 5h cross-check and `#141` Bedrock policy. Gate: `djbclark-ade` pytest + `route_agent.py plan` / `quota_signals.py` smoke checks.
5. **Phase 4 — Schema 2.0 Promotion:** Promote `pool_id` as primary key in `docs/json-contract.md` and `selfdescribe.py` once all `djbclark-ade` consumers are on Phase 3.
