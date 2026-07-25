# Shared quota semantics (language-neutral)

**Date:** 2026-07-25  
**Status:** Design note — proposed shared surface for multi-project reuse  
**Related:** [`competitive-landscape.md`](competitive-landscape.md),
[`json-contract.md`](json-contract.md), in-tree models in
[`src/aiuse/models.py`](../src/aiuse/models.py) and pace math in
[`src/aiuse/analysis/pace.py`](../src/aiuse/analysis/pace.py)

This document answers:

> Which ideas from `aiuse` (and peers) are worth **sharing with competitors**,
> and how do we abstract them so **Python, Go, Dart, Rust, Swift** projects can
> all consume the same definitions without forking Python source?

It is deliberately **not** a proposal to merge repos or publish a heavyweight
shared runtime. The goal is a thin, versioned **spec + fixtures** package that
any tool can embed.

---

## Why share at all?

Layer 1 monitors (CodexBar, OpenUsage, caut, …) and Layer 2 decision tools
(`aiuse`, quotabot, onWatch) repeatedly reinvent the same *semantic* mistakes:

| Failure mode | Harm |
| ------------ | ---- |
| Prepaid / pay-as-you-go shown as “100% left, burn before reset” | False urgency; users waste attention or burn the wrong pool |
| Nested 5h + weekly bars treated as independent quotas | Short window greeds while weekly is already on pace |
| Remaining % without elapsed fraction | No burn vs conserve distinction |
| No stable vocabulary for “use next” vs “pace yourself” vs “inventory” | Agents and scripts cannot interoperate |

If several products agree on **billing kind**, **window kind**, **shared
allotment**, and **pace verdicts**, humans and agents get consistent advice even
when they switch tools. Collection adapters and UI chrome stay proprietary.

**Do not share** product identity: menu-bar UX, MCP routing, LiteLLM leases,
PATH-aggregator selection priority, or credential stores. Share **meaning**, not
**product**.

---

## What is worth sharing vs keeping private

### Share (portable semantics)

| Concept | Why competitors benefit | `aiuse` home today |
| ------- | ----------------------- | ------------------ |
| **Billing kind enum** | Prepaid ≠ subscription | `BillingKind` |
| **Normalized account + window JSON** | Interop / test fixtures | `AccountUsage`, `QuotaWindow` |
| **Window duration buckets** | 5h / weekly / monthly | `classify_window_minutes` |
| **Shared-allotment rule** | Governing window only | `governing_partition` + config |
| **Pace formulas** | Burn / conserve / on_pace | `compute_pace`, `classify_pace` |
| **Decision band labels** | Ladder vocabulary | report bands: error→empty→n/a→slow→mid→use |
| **Alert kind enum** | `burn` \| `conserve` \| `prepaid` | `UseOrLoseAlert.kind` |
| **Cross-check status words** | Multi-source honesty | `consistent` \| `warning` \| `unavailable` |
| **Health / payload path split** | Loopback honesty | proposed [#8](https://github.com/djbclark/aiuse/issues/8) |
| **Golden test vectors** | Prevent semantic drift | tests under `tests/` (to extract) |

### Keep private (product / architecture)

| Concept | Why not a shared project |
| ------- | ------------------------ |
| Five-source collector orchestration | `aiuse`-specific trust model |
| cswap authority / source priority | Policy choice, not universal truth |
| LaunchAgent + snapshot learning | Operator workflow |
| Priority-ladder **pretty** UX | Presentation |
| MCP / `suggest` CLI / agent router | Product surface ([#2](https://github.com/djbclark/aiuse/issues/2), [#5](https://github.com/djbclark/aiuse/issues/5)) |
| Local runtime probes (Ollama, …) | Host-specific; optional note only ([#7](https://github.com/djbclark/aiuse/issues/7)) |
| Ambient menubar companion stack | Compose with CodexBar/OpenUsage ([#4](https://github.com/djbclark/aiuse/issues/4)) |
| Deep history BI dashboard | onWatch-class product ([#6](https://github.com/djbclark/aiuse/issues/6)) |

### Ideas to **push outward** to Layer 1 tools (issues/docs, not code merge)

These are the “move ideas into competitors” items from the strategy note:

1. **Prepaid = no use-or-lose urgency** — display inventory, not a green “full” burn bar.
2. **Shared allotment / governing window** — when 5h ⊂ weekly, do not dual-rank.
3. **Optional “use-or-lose sort mode”** — remaining% × time-to-reset, or import a
   decision band if the monitor ever ranks.
4. **Stable `--json` window fields** — `resets_at`, `window_minutes`,
   `remaining_percent`, explicit billing kind when known.
5. **Health path ≠ payload path** for loopback HTTP (e.g. OpenUsage).

Shared specs make those upstream conversations concrete: a short issue can link
to schema + one golden vector instead of re-explaining policy in prose.

---

## Abstraction layers (recommended stack)

Prefer **data + pure math** over executable DSLs. Competitors will re-implement
in their language; they need unambiguous inputs/outputs, not a Python package.

```text
┌─────────────────────────────────────────────────────────────┐
│  L3  Product (private): CLI, TUI, MCP, menubar, routing     │
├─────────────────────────────────────────────────────────────┤
│  L2  Decision engine (per project): scoring weights, UX     │
│      MAY implement shared formulas from L1                  │
├─────────────────────────────────────────────────────────────┤
│  L1  SHARED: schemas, enums, formulas, golden vectors       │
│      (this doc’s proposed package)                          │
├─────────────────────────────────────────────────────────────┤
│  L0  Collectors / adapters (private or per-tool)            │
└─────────────────────────────────────────────────────────────┘
```

### Layer 1 deliverables (language-neutral)

| Artifact | Format | Consumed how |
| -------- | ------ | ------------ |
| **JSON Schema** for accounts, windows, pace, verdicts | Draft 2020-12 JSON Schema | codegen, validators (ajv, jsonschema, …) |
| **Enum + constant tables** | YAML or JSON | load at startup |
| **Pace & classify formulas** | Markdown with numbered equations + pseudocode | human reimplementation; optional formal tests |
| **Provider policy snippets** | YAML (shared_allotment, prepaid providers) | optional defaults; projects may override |
| **Golden vectors** | JSON files: `input` → `expected` | any language’s test harness |
| **JSON Schema for decision output** | optional `verdict` / `band` / `suggestion` | agent interop |

Avoid as the *primary* shared form:

- **Python wheel** as the only source of truth (Dart/Go peers will ignore it).
- **OPA/Rego or CEL** as required runtime (powerful, but raises adoption cost).
- **Protobuf** alone (fine as an *optional* encoding later; start with JSON).

Optional later: a **tiny** reference implementation in one systems language
(e.g. Rust `cdylib` + WASM) *if* golden vectors prove insufficient. Ship the
spec first; code second.

---

## Core vocabulary (normative sketch)

Field names below align with `aiuse`’s public JSON where possible
([`json-contract.md`](json-contract.md)). A shared package would freeze a
**semver’d** subset under a neutral name (working title: **`quota-semantics`**
or **`use-or-lose-spec`**).

### 1. Billing kind

```yaml
# enums/billing_kind.yaml
billing_kind:
  - id: subscription_window
    description: >
      Allotment resets on a schedule; unused capacity is lost.
      Eligible for burn / conserve / on_pace ranking.
  - id: prepaid_balance
    description: >
      Purchased balance that rolls until spent; no plan reset urgency.
      Must not rank as use-or-lose "burn now".
  - id: payg_api
    description: Pay-as-you-go with no fixed allotment window.
  - id: unknown
    description: Collector did not establish billing semantics.
```

**Rule P1:** If `billing_kind` is `prepaid_balance` or `payg_api`, decision
band is **inventory** (`n/a`), never `use` / `slow` from remaining%.

### 2. Window measurement

```json
{
  "$id": "https://example.invalid/quota-semantics/window.schema.json",
  "type": "object",
  "required": ["label"],
  "properties": {
    "label": { "type": "string" },
    "used_percent": { "type": ["number", "null"] },
    "remaining_percent": { "type": ["number", "null"] },
    "resets_at": { "type": ["string", "null"], "format": "date-time" },
    "window_minutes": { "type": ["integer", "null"], "minimum": 0 },
    "reset_description": { "type": ["string", "null"] }
  }
}
```

**Derived remaining:** if only `used_percent` is set,
`remaining_percent = max(0, 100 - used_percent)`.

### 3. Window duration class

Constants (minutes) — match current `aiuse` buckets:

| Class | Max minutes | Nominal minutes (when duration missing) |
| ----- | ----------- | --------------------------------------- |
| `5h` | 360 | 300 |
| `weekly` | 10080 | 10080 |
| `monthly` | 44640 | 43800 |

**Rule W1:** Classify by `window_minutes` against the max bounds above; if
`window_minutes` is null, class is unknown and pace confidence is `low` unless
a nominal is inferred from label conventions (project-local, not shared).

### 4. Shared allotment

```yaml
# policy snippet — defaults, not hard law
shared_allotment_defaults:
  claude: true
  gemini: true      # antigravity / Google AI
  opencode: true
  cursor: true
```

**Rule S1 (governing window):** Among windows with a known remaining%, the
**longest** `window_minutes` (or nominal) is the *governing* window. Children
are display-only for ranking when shared allotment is enabled for that provider.

**Rule S2:** When durations tie, prefer a label containing `included` (Cursor-
style), else stable list order — document this tie-break so implementations match.

**Rule S3:** If pace cannot be computed for the governing window, fall back to
scoring windows independently (do not invent urgency).

### 5. Pace profile (pure functions)

Inputs: `remaining_percent`, `resets_at`, `window_minutes` (or nominal), `now`,
optional `learned_rate_per_day` and `learned_sample_count`.

Let:

- `used_fraction = (100 - remaining_percent) / 100`
- `d_days = window_minutes / 1440`
- `t_left_days = max(0, (resets_at - now) / 1 day)`
- `elapsed = clamp(1 - t_left_days / d_days, 0, 1)`
- `e_min = 0.05` (avoid divide-by-zero early in the window)
- Instantaneous rate (fraction per day):  
  `r_now = used_fraction / (max(elapsed, e_min) * d_days)`
- Optional history blend:  
  `λ = n / (n + 2)`, `r_hat = (1-λ)·r_now + λ·learned_rate_per_day`
- `projected_used = min(1, used_fraction + r_hat * t_left_days)`
- `projected_waste = 1 - projected_used`
- `pace_ratio = used_fraction / max(elapsed, e_min)`
- `projected_exhaust_at = now + (1 - used_fraction) / r_hat` days if `r_hat > ε`

**Classify** (thresholds are *parameters*, not hard-coded product chrome):

| Verdict | Condition (priority order) |
| ------- | -------------------------- |
| `unknown` | Missing waste and exhaust projections |
| `on_pace` | `elapsed < min_elapsed_fraction` and no learned rate |
| `conserve` | `projected_exhaust_at < resets_at - conserve_min_lead` |
| `burn` | `projected_waste >= waste_alert_fraction` |
| `on_pace` | otherwise |

Default parameters used by `aiuse` today (overridable):

```yaml
pace_defaults:
  waste_alert_fraction: 0.30
  min_elapsed_fraction: 0.15
  conserve_min_lead_hours: 4.0
  e_min: 0.05
```

Projects may differ on thresholds; **formulas** should not.

### 6. Decision bands (display / sort lanes)

Ordered for human “read bottom → top, burn soon”:

| Band id | Tag (aiuse) | Meaning |
| ------- | ----------- | ------- |
| `error` | error | Collect failed / unusable row |
| `empty` | empty | No remaining capacity |
| `n_a` | n/a | Non-expiring inventory (prepaid / payg) |
| `slow` | slow | Conserve — pace yourself |
| `mid` | mid | On pace / advisory |
| `use` | use | Burn — waste projected before reset |

**Rule B1:** Sort key is band lane first, then score/urgency within lane.  
**Rule B2:** Never promote prepaid into `use`/`slow` solely from remaining%.

### 7. Optional suggestion object (agent-facing)

Portable shape for “single winner” without implying a proxy:

```json
{
  "suggestion": {
    "provider": "claude",
    "account": "user@example.com",
    "window_label": "Claude Code weekly",
    "kind": "burn",
    "band": "use",
    "score": 87.2,
    "reason": "…"
  }
}
```

Null suggestion = nothing urgent. This is what [#2](https://github.com/djbclark/aiuse/issues/2)
implements inside `aiuse`; the **schema** can be shared even if routing stays private.

### 8. Health probe convention (collectors)

```yaml
# conceptual collector descriptor
collector:
  base_url: "http://127.0.0.1:6736"
  payload_path: "/v1/limits"
  health_path: "/v1/limits"   # may differ from product root
```

**Rule H1:** “Up” checks use `health_path` (or full `probe_url`); quota parse
uses `payload_path`. Do not treat HTTP 404 on `/` as collector death if the
payload path returns 200.

---

## Golden vectors (contract tests)

Shared projects should lead with **fixtures**, not libraries.

Example layout for a future repo or an in-tree `docs/shared-quota-semantics/`:

```text
quota-semantics/
  README.md                 # this document’s normative subset
  schemas/
    window.schema.json
    account.schema.json
    pace.schema.json
    verdict.schema.json
  enums/
    billing_kind.yaml
    alert_kind.yaml
    band.yaml
  policy/
    shared_allotment_defaults.yaml
    pace_defaults.yaml
  formulas/
    pace.md                 # equations + pseudocode
  fixtures/
    prepaid_deepseek.json
    shared_allotment_claude.json
    conserve_fast_burn.json
    burn_near_reset.json
    early_window_on_pace.json
```

Fixture shape:

```json
{
  "id": "prepaid_deepseek",
  "now": "2026-07-25T12:00:00Z",
  "account": {
    "provider": "deepseek",
    "billing_kind": "prepaid_balance",
    "balance_usd": 42.0,
    "windows": []
  },
  "expected": {
    "band": "n_a",
    "alert_kind": "prepaid",
    "suggestion_eligible": false
  }
}
```

Any implementation that passes the fixture set is “semantics-compatible” even
if UI strings differ.

---

## Mapping to open `aiuse` feature issues

| Issue | Product work in `aiuse` | Shared-spec angle |
| ----- | ----------------------- | ----------------- |
| [#2 suggest](https://github.com/djbclark/aiuse/issues/2) | CLI/JSON single winner | `suggestion` schema + eligibility rules |
| [#3 forecast](https://github.com/djbclark/aiuse/issues/3) | Louder exhaust/waste UX | Pace fields already shareable; copy is private |
| [#4 ambient companion](https://github.com/djbclark/aiuse/issues/4) | Docs / one-line status | **Not shared** — compose Layer 1 |
| [#5 MCP/loopback](https://github.com/djbclark/aiuse/issues/5) | Agent surface | Optional transport; payload = shared JSON |
| [#6 deeper History](https://github.com/djbclark/aiuse/issues/6) | Teach from snapshots | Learned rate field only; storage private |
| [#7 local runtime](https://github.com/djbclark/aiuse/issues/7) | INFO note when empty | Optional enum value; probes private |
| [#8 health_path](https://github.com/djbclark/aiuse/issues/8) | Doctor/config | Collector descriptor schema (shareable) |

---

## Suggested shared project shape

**Name (working):** `quota-semantics` (or `use-or-lose-spec`)  
**Home:** either a small independent GitHub repo under the same org, **or**
initially a directory in this repo (`docs/shared-quota-semantics/`) published
as a tagged subtree until a peer wants to co-maintain.

**License:** permissive (MIT/Apache-2.0) so CodexBar-class and quotabot-class
tools can embed without friction.

**Versioning:** semver on the **schema package**. Breaking renames of enums or
formula defaults bump major. Adding optional fields is minor.

**Non-goals for v0.1:**

- Shipping collector adapters
- OAuth / cookie scraping
- A full scoring engine with plan prices (prices are local config)
- Requiring WASM/FFI

**v0.1 checklist:**

1. Freeze enums + window/account JSON Schema from this doc.
2. Publish pace formulas + default thresholds as numbered rules.
3. Extract 8–12 golden vectors from `aiuse` tests (prepaid, shared allotment,
   conserve, burn, early window, empty, error, multi-account labels).
4. Document how `aiuse` maps internal Python models → shared schema (1:1 for
   most fields already).
5. Open short upstream issues on 1–2 Layer 1 tools linking fixtures (prepaid,
   `window_minutes`), not demanding they adopt ranking.

---

## How each language would ingest

| Language / project class | Ingestion path |
| ------------------------ | -------------- |
| **Python (`aiuse`)** | Validate with `jsonschema`; optionally generate TypedDicts; keep current pure functions but assert against fixtures in CI |
| **Go (onWatch-class)** | `go generate` from JSON Schema or hand structs + fixture tests |
| **Dart (quotabot-class)** | Freezed/json_serializable models from schema; fixture unit tests |
| **Rust (caut-class)** | `schemars` / serde structs; fixtures as `include_str!` |
| **Swift (menubar apps)** | Codable structs; XCTest over JSON fixtures |
| **Shell / jq only** | Consume decision JSON only; no need for formulas |

No project must call a shared binary. **CI green on fixtures** is the
compatibility proof.

---

## What `aiuse` should do next (local, low risk)

1. **Keep implementing** product issues #2–#8 in-tree (Python remains source of
   UX truth).
2. **Extract fixtures** gradually: when a test encodes a semantic rule (prepaid
   → n/a, governing window only, …), also write it under
   `docs/shared-quota-semantics/fixtures/` in the neutral shape.
3. **Do not** block features on a separate org/repo; grow the directory until a
   peer wants co-ownership, then split.
4. When opening upstream issues on monitors, link **one fixture + one rule id**
   (P1, S1, …) rather than “please read our Python.”

---

## Summary

| Question | Answer |
| -------- | ------ |
| Share product code with competitors? | **No** |
| Share ranking/prepaid/shared-allotment **meaning**? | **Yes** |
| Best medium? | **JSON Schema + YAML enums + formula doc + golden vectors** |
| DSL? | Light **policy YAML** (shared_allotment, thresholds), not a new language |
| Heavy runtime (Rego/WASM)? | Optional later; fixtures first |
| Where to start? | In-repo `docs/shared-quota-semantics/` + CI vectors; promote to a shared repo when a second implementer appears |

Composition over absorption remains the product strategy
([`competitive-landscape.md`](competitive-landscape.md)). Shared semantics make
that composition **honest across tools**, not a monorepo.
