# Quota Algorithm Audit & Implementation Plan (2026-08-01)

**Audience:** written to be executed by a sequence of AI coding agents (or a
future instance of the orchestrating agent after a context reset), one step
at a time, each with no memory of the others or of the research session that
produced this document. Every step below is self-contained: it names exact
files/functions, states the finding precisely enough that it does not need
to be re-derived, and ends with a concrete test gate. Read Appendix A and
Appendix B once before starting Phase 1 — they're background all steps rely
on; the steps themselves don't repeat that context.

**Tracks:** GitHub issues [#19](https://github.com/djbclark/aiuse/issues/19),
[#20](https://github.com/djbclark/aiuse/issues/20),
[#21](https://github.com/djbclark/aiuse/issues/21),
[#22](https://github.com/djbclark/aiuse/issues/22) — one phase per issue
below, in recommended execution order.

**See also:** `docs/shared-quota-semantics/README.md` for the formal
schema/enum/formula spec this plan's changes should extend, not bypass;
`docs/cursor-quota.md` for the existing (partially superseded — see Phase 2)
design rationale behind current Cursor handling; `docs/fix-implementation-plan.md`
for this repo's established plan-format convention, which this document
follows.

**Naming note (2026-08-03 / aiuse 3.0.6+):** Cursor’s tertiary slot is labeled
**Cursor other models** (Cursor docs: “Other Models”), not “Cursor API”. The
independent pool key is `cursor_other_models` (legacy `"Cursor API"` labels
still match). Historical prose below may still say “API pool”; treat that as
the same monthly use-or-lose allotment — not prepaid. Canonical write-up:
[`cursor-quota.md`](cursor-quota.md).

## How this document came to exist

The operator relayed a Gemini-authored "system instruction" for cross-vendor
AI-subscription quota-pacing orchestration, with specific dollar/percentage
claims. Those claims were fact-checked against `aiuse`'s real schema and
public vendor docs, found partially wrong (see Appendix A), then verified
via three independent AI cross-checks (Gemini, Grok, Claude Opus 5,
ChatGPT — actually run across two separate rounds, four models total). That
led to the question "should `aiuse` itself get a `burn` field," which led to
a source-level audit of this repo (`~/src/aiuse`), which found the
sophisticated version already exists and is live (Appendix B), plus three
real gaps (Phases 1–3 below) and one investigation item whose answer arrived
via a second cross-AI research round (Phase 4). Full raw session history, if
ever needed beyond what's captured here, lives in the operator's private
memory system as `reference_ai_vendor_quota_structures.md` and
`project_aiuse_quota_algorithm_audit.md` (not part of this repo — ask the
operator if deeper unedited context is ever needed).

## Operating rules for every step

1. Run `.venv/bin/python -m pytest -q` **before** starting a step (confirm
   the baseline is green) and again **after** finishing it. A step is not
   done until the suite passes with new tests included and nothing else
   broken. Also run `.venv/bin/python -m pytest -q tests/test_shared_quota_semantics.py`
   specifically if the step touches `analysis/pace.py`, `analysis/use_or_lose.py`,
   or anything under `docs/shared-quota-semantics/`.
2. Do exactly one phase per work session unless a phase explicitly says its
   steps are small enough to combine.
3. **This document may be stale by the time you read it.** Vendor billing
   structures move fast (see Appendix A's own caveats about this), and the
   `aiuse` codebase may have changed since 2026-08-01. Before implementing
   any step, re-read the actual current file/function named — don't trust
   this document's line numbers or quoted code blindly. If a step's
   described bug doesn't reproduce as written, **stop and report the
   discrepancy instead of guessing** — do not silently skip or improvise.
4. Commit after each step passes, with a message naming the phase and issue
   number (e.g. `Fix #21: split Cursor API into an independent pool`).
5. Nothing in this plan should reference any specific downstream consumer's
   orchestration setup (e.g. "be more careful with Claude because it's our
   orchestrator") — `aiuse` is a universal tool. Every step here is
   vendor-structural or algorithm-general.

---

## Appendix A: Verified vendor quota knowledge

Confidence-rated, source-cited findings from two rounds of independent
cross-AI verification (round 1: Gemini, Grok, ChatGPT, Claude Opus 5 fact-checking
a Gemini-sourced orchestration instruction; round 2: the same four models
re-checking three narrower follow-up questions). Use this as reference —
don't re-derive vendor facts from scratch when implementing the phases below.

| Vendor                                | Structure                                                                                                  | Verified numbers/facts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Confidence                                                                                                                                                                                                                                                                         | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **OpenCode Go**                       | Dollar-denominated, 3 nested windows                                                                       | **$12/5h, $30/week, $60/month** (an earlier draft of the orchestration instruction wrongly said $4/$10/$20 — corrected)                                                                                                                                                                                                                                                                                                                                                                                                           | High (vendor's own docs, `opencode.ai/docs/go`)                                                                                                                                                                                                                                    | 40%/50% ratios between the windows do hold against the _real_ numbers. Subscription price ($5 first month, $10/mo after) is separate from these usage caps. "Use balance" toggle falls back to a linked Zen prepaid balance — **confirmed undetectable programmatically** (see Phase 3).                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **OpenAI Codex**                      | 5h window + "additional weekly limits may apply", explicitly no published ratio                            | 5h gives model-dependent message ranges (e.g. Plus: ~15-80 on one model, ~60-350 on another)                                                                                                                                                                                                                                                                                                                                                                                                                                      | High that there's no stable ratio                                                                                                                                                                                                                                                  | The "~12%, 42 hours to drain a week" figure from the original instruction is wrong/unverifiable — do not use it anywhere. Metering is token-credit-based (~9x spread in credits-per-message within one model). A documented carve-out: if a limit is hit mid-turn, that turn may be allowed to finish (fair use). Also (round 2, medium confidence): Codex/ChatGPT Work/Excel/Workspace Agents may share one broader agentic pool when available on the same plan, and ChatGPT Desktop Voice tasks debit _both_ a separate voice-minute meter and the shared Codex pool simultaneously — see Phase 4.                                                                                                                                                               |
| **Claude (Pro/Max)**                  | 5h session nested under a weekly limit                                                                     | 5h starts on first message, resets 5h later; weekly resets at a **fixed calendar time assigned to the account**, not a rolling 7-day lookback                                                                                                                                                                                                                                                                                                                                                                                     | High on the fixed-calendar point (Anthropic's own help center)                                                                                                                                                                                                                     | Max plans may carry a **second, model-specific weekly sub-limit** — round 1 said "Sonnet-only", round 2 responses disagreed (one said Opus-only, one described a separate "Fable 5" sub-cap bounded to 50% of the weekly allowance). **This is a genuine, unresolved discrepancy across sources — don't hardcode which model has the sub-limit; trust the account's live Settings→Usage page.** Usage credits (if enabled) make the ceiling soft — see Phase 3.                                                                                                                                                                                                                                                                                                     |
| **Gemini Apps / Google AI Pro-Ultra** | Genuinely nested 5h+weekly                                                                                 | "Refreshes every 5 hours until you reach your weekly limit" (Google's own wording)                                                                                                                                                                                                                                                                                                                                                                                                                                                | High                                                                                                                                                                                                                                                                               | Falls back to a lighter model (Flash-Lite) on limit, not a hard stop.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **Antigravity**                       | Structurally nested, but **no published numbers**                                                          | Google says baseline limits are capacity-determined, correlated with agent work done, not prompt count                                                                                                                                                                                                                                                                                                                                                                                                                            | Medium (see caveat)                                                                                                                                                                                                                                                                | `aiuse`'s `independent_pool_key()` in `analysis/pace.py` already hard-separates "gemini" from "claude/gpt" labels for this provider. **Round 2 finding: two of four models explicitly said this hard-separation "can't be verified" from Antigravity's current public docs** — the docs describe baseline quota and per-model overage transitions but don't explicitly confirm two hard pools. Not filed as a bug (the code isn't demonstrably wrong, and matches earlier/less-current sources) — just weaker-than-assumed footing if this ever needs re-justifying.                                                                                                                                                                                                |
| **Cursor**                            | **CONFIRMED (round 2): two independent monthly pools** for individual plans (Pro/Pro+/Ultra)               | **Auto+Composer pool** (first-party; size never published, just "generous included usage") and **API pool** ($20/$70/$400 for Pro/Pro+/Ultra respectively, billed at model provider prices)                                                                                                                                                                                                                                                                                                                                       | High — Cursor's own current docs (`cursor.com/docs/models-and-pricing`, `cursor.com/blog/increased-agent-usage`), independently cited by all 4 round-2 checks                                                                                                                      | This is a correction to `aiuse`'s current behavior — see Phase 2, the single most concrete/actionable finding in this document. Teams plans use a different, single shared-pool model — don't port Teams behavior to individual plans. A "Cursor Token Rate" per-token surcharge and different Max Mode billing exist on Teams but not individual plans.                                                                                                                                                                                                                                                                                                                                                                                                            |
| **GitHub Copilot**                    | Genuinely unclear/moving target as of this writing                                                         | Not modeled with confidence — see below                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Low (round 2 found 3+ _mutually inconsistent_ descriptions across the 4 models: legacy premium-request SKUs, a pure AI-credits model since ~June 2026, and a sequential base-credits-then-flex-allotment model)                                                                    | **Explicitly not turned into a ticket** — `aiuse`'s own `collectors/tokscale.py` already tracks `credit_status`/`has_credits`/`overage_limit_reached`/`reset_credits` fields alongside a "premium requests" window label, suggesting the collector already anticipates a credits-based model to some degree. Filing an issue without being sure something's actually broken would have been speculative. If this ever needs revisiting, start by reading `tokscale.py`'s current Copilot handling before assuming it's stale. Operationally interesting fact from round 1: autonomous tool calls within an agentic task historically did not count as premium requests, only the user's own explicit prompts did — may or may not still hold under a credits model. |
| **Grok / SuperGrok**                  | One shared weekly pool (per round 2; round 1 found no verifiable structure at all)                         | xAI replaced separate daily product limits with one shared weekly paid-usage pool spanning Chat/API/Build/Imagine/Voice ("Build" is xAI's official agentic coding CLI, **Grok Build** — confirmed 2026-08-02, invoked as `grok`, shares the same SuperGrok pool `aiuse` already tracks; `runner.py`'s `_PROVIDER_ALIASES` already folds `grok-build`/`supergrok` into the canonical `grok` provider); separate free-tier fallback limits and a purchasable "Extra Usage Credits" balance exist after the weekly pool is exhausted | Medium — round 2's answer (citing `docs.x.ai/grok/faq`) is more specific and consistent with round 1's "shared weekly pool" framing, but round 1 found conflicting secondary sources for the same vendor. Don't build precise pacing math around Grok; read the live account view. |
| **OpenRouter**                        | Not a nested subscription system — prepaid balance + rate limits, with an optional nested budget hierarchy | Free-tier models: per-minute and per-day request caps. Paid: no platform-side request cap (upstream provider limits still apply). Optional: organization guardrails can nest workspace/member/key-level spend budgets, with "the lower limit wins" when they overlap                                                                                                                                                                                                                                                              | High                                                                                                                                                                                                                                                                               | Already covered by the "never route to prepaid_balance without asking" financial directive regardless of any rate-limit structure question — this vendor fact doesn't change that policy.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |

### The "dual-debit" pattern (new in round 2 — see Phase 4)

Distinct from "shared pool" (one pool, multiple actions draw from it) and
"independent pools" (hard-separated, e.g. Cursor's two pools above): **some
single actions debit two independent meters simultaneously.** Two examples
surfaced, both attributed to primary vendor docs by the AI that found them
(not yet independently re-verified by a second source — this is why Phase 4
is "investigate," not "implement"):

- **OpenAI Codex Voice (desktop)**: a voice-initiated coding task debits
  both a separate voice/connected-minute meter AND the shared Codex
  local+cloud pool, at the same time.
- **GitHub Copilot Code Review**: each review is billed two ways at once —
  AI credits for the model tokens, and GitHub Actions minutes (a completely
  separate, shared-across-all-workflows allowance) for the agent
  infrastructure.

If real, a schema assuming `action → exactly one pool` will systematically
under-count exactly the most expensive/dual-metered operations.

---

## Appendix B: `aiuse`'s existing pace/burn architecture (primer)

**Key fact, confirmed live-working, not dormant:** `aiuse --json`'s output
has two top-level arrays — `accounts[]` (raw per-provider window data, has
`billing_kind` but no burn/conserve verdict) and **`alerts[]`** (the actual
suggestion output, each entry has `kind: "burn"` / `"conserve"` / `"prepaid"`).
The `kind` field already exists; it does not need to be added. An earlier
draft of this research incorrectly reported it didn't exist — that was
based on only inspecting `accounts[]`.

**Where it's computed:**

- `src/aiuse/analysis/pace.py` — pure math. `compute_pace()` derives
  `elapsed_fraction`, `used_fraction`, `pace_ratio`, `projected_used_fraction`,
  `projected_waste_fraction`, `projected_exhaust_at` from a window's live
  `remaining()`, `resets_at`, and `window_minutes` (falling back to a
  nominal duration per `classify_window_minutes`/`nominal_window_minutes`
  when `window_minutes` is `None` — e.g. Grok, which correctly degrades to
  `confidence: "low"` rather than fabricating a duration). `classify_pace()`
  turns that into a verdict, in priority order: `unknown` (missing data) →
  `on_pace` (too early in the window, no learned rate yet) → `conserve`
  (projected to exhaust before reset, with a `conserve_min_lead_hours`
  buffer) → `burn` (projected waste ≥ `waste_alert_fraction`, default 0.30)
  → `on_pace` (otherwise). Defaults live in
  `docs/shared-quota-semantics/policy/pace_defaults.yaml`.
  - **Module docstring is stale** (says "not wired into analyze_use_or_lose
    yet") — this is issue #19/Phase 1.
- `src/aiuse/analysis/use_or_lose.py` — `analyze_use_or_lose()` is the real
  entry point. `mode` defaults to `"pace"` (only `"legacy"` if
  `use_multi_dim_scoring` is explicitly set `False`), so the pace/burn path
  above is the **default**, not opt-in. This function also implements
  **shared-allotment pooling**: `governing_partition()` picks the
  longest-duration window in a pool as the one that actually gets scored
  (children are suppressed from generating their own alert — the design
  intent is "don't tell someone to burn their 5h window separately from
  the weekly window it's already part of"), and
  `partition_independent_pools()` / `independent_pool_key()` hard-separate
  windows into different pools when their _label_ matches known patterns
  (currently: `"claude/gpt"` vs `"gemini"` for Antigravity — nothing for
  Cursor's API-vs-Auto split yet, which is Phase 2).
  - Shared-allotment is opt-in per provider via
    `analysis.provider_overrides.<provider>.shared_allotment: true` in
    config (`_shared_allotment_enabled()`) — Cursor already has this
    enabled (see `docs/cursor-quota.md`), which is _why_ Phase 2 needs a
    pool-key change, not just a config flip.
- `src/aiuse/models.py` — schema. `AccountUsage.billing_kind` is one of
  `subscription_window` / `prepaid_balance` / `payg_api` / `unknown` (all
  four genuinely emitted by real collectors, not dead enum values).
  `AccountUsage.usage_credits: UsageCredits | None` already models an
  "extra-usage / pay-as-you-go spend against a subscription... the optional
  overage wallet that sits _beside_ 5h/weekly plan windows" (that's the
  class's own docstring) — populated by `collectors/cswap.py` (Claude),
  `collectors/codexbar.py`, and `collectors/openusage.py`, and **already
  displayed in the human-readable report** (`report.py::_usage_credits_lines`)
  — but **never consulted by `pace.py` or `use_or_lose.py`**. That's Phase 3.
- `docs/shared-quota-semantics/` — the formal, "dogfooded" spec: JSON
  schemas, YAML enums, normative pace formulas (`formulas/pace.md`, numbered
  rules P1–P8 + shared-allotment rules S1–S3 + prepaid rules PP1–PP2), and
  golden fixture JSON files under `fixtures/` that the test suite
  (`tests/test_shared_quota_semantics.py`) checks implementations against.
  **Any phase below that changes scoring behavior should add a golden
  fixture here**, matching the existing fixture format (see
  `fixtures/burn_near_reset.json` or `fixtures/shared_allotment_claude.json`
  for examples of the shape).

**Existing Cursor-specific context** (relevant background for Phase 2):
`collectors/codexbar.py`'s `_SLOT_LABELS["cursor"]` hardcodes
`("Cursor included", "Cursor Auto", "Cursor API")` for CodexBar's generic
`primary`/`secondary`/`tertiary` slots, with a comment: _"Cursor dashboard:
Included (overall) ⊃ Auto + API breakdowns; On-Demand is separate
(providerCost). Primary is the governing included bar."_ `docs/cursor-quota.md`
explains this was a deliberate fix (dated earlier in 2026, before Cursor's
current two-pool docs existed or were found) for a real prior bug: three
identical-looking "monthly quota" bars with invented independent
dollar-at-risk and a false CONSERVE alert on the API slot alone. **That
fix's reasoning may now be partially superseded** by Cursor's confirmed
current two-pool billing model (Appendix A) — Phase 2 needs to reconcile
the two, not just blindly flip a flag.

---

## Phase 1 — Fix the stale docstring (Issue #19)

**Estimate:** trivial, ~15 minutes.

**File:** `src/aiuse/analysis/pace.py`, line 1 (module docstring).

**Current (wrong):**

```python
"""Pure pace-math for Phase 2 scoring (not wired into analyze_use_or_lose yet)."""
```

**Fix:** replace with something reflecting that this is the live default path, e.g.:

```python
"""Pace math for use-or-lose scoring — the default `mode == "pace"` path in
analyze_use_or_lose (see use_or_lose.py)."""
```

**Also do:** `grep -rn "Phase 2\|not wired" src/aiuse/` and check for any
other comments that drifted the same way (e.g. `use_or_lose.py`'s own
docstring: `"""Detect monthly/weekly subscription allotments that will
expire unused."""` — check if this still accurately describes the file
given it now also handles the pace/burn/conserve path, and update if not).

**Test gate:** no logic changed; just confirm `pytest -q` still passes (it
will — this step touches no executable code).

---

## Phase 2 — Cursor's Included/Auto vs. API pool split (Issue #21)

**Estimate:** small-to-medium, ~2–6h. **Status: confirmed, ready to
implement** — this is not speculative; see Appendix A's Cursor row and the
comment thread on issue #21 for the exact primary-source citations from all
4 round-2 AI checks.

### The finding

Cursor's current billing model (confirmed, high confidence) for individual
plans (Pro/Pro+/Ultra) has **two independent pools**: Auto+Composer
(first-party) and API (third-party, billed at provider rates, $20/$70/$400
included per tier). `aiuse` currently treats all three CodexBar slots
(Included/Auto/API) as **one shared pool** via
`analysis.provider_overrides.cursor.shared_allotment: true`, with "Cursor
included" as the sole governing window and Auto+API fully suppressed as
children (`governing_partition()`'s tie-break: same duration → prefer the
label containing "included").

This means: if Cursor's Auto+Composer pool still has headroom but the API
pool is genuinely exhausted (a real, independent, alertable event under the
confirmed billing model), `aiuse` currently shows nothing wrong — API's
exhaustion is silently swallowed as a suppressed child of "Cursor included."
A live snapshot during this research showed exactly this: Cursor API at 0%
remaining while Included/Auto both had ~75-79% remaining, and only
Included's number surfaced.

### Why this isn't a trivial flag-flip

`docs/cursor-quota.md` explains the _current_ shared-allotment design was
itself a deliberate fix for a real prior bug (three generic "monthly quota"
bars, each independently and wrongly scored). Simply disabling
`shared_allotment` for Cursor would regress back to that original bug —
Auto and Included would go back to being scored as if fully independent
from each other too, when in fact Auto+Composer genuinely IS one pool
together with (or overlapping) Included in Cursor's model (re-verify this
distinction specifically — round 2's confirmation was clear that Auto and
API are separate pools, but less explicit about whether "Included" and
"Auto" are the same number viewed two ways, or two more things worth
distinguishing; if genuinely unclear, treat Included+Auto as one governing
pool, matching the "Auto+Composer" pool language from Cursor's own docs, and
give API its own separate pool — this is the two-pool model, not three).

### Proposed implementation

1. In `analysis/pace.py`, extend `independent_pool_key()` (currently only
   recognizes `"claude/gpt"` and `"gemini"` label substrings for
   Antigravity) with a Cursor case: labels matching `"cursor api"` (or
   whatever exact label `_SLOT_LABELS["cursor"]` produces — currently
   `"Cursor API"`) get their own pool key (e.g. `"cursor_api"`); labels
   matching `"cursor included"` / `"cursor auto"` fall through to the
   residual/default pool (so they stay one shared governing pool together,
   matching Cursor's "Auto+Composer" framing).
2. Update the comment in `collectors/codexbar.py`'s `_SLOT_LABELS["cursor"]`
   block — it currently asserts Included ⊃ Auto + API as one pool with
   On-Demand separate; correct it to describe the two-pool model instead.
3. Update `docs/cursor-quota.md` — its "What `ai` does" section
   (numbered list, step 2 specifically: _"Default
   `analysis.provider_overrides.cursor.shared_allotment: true` so only
   Included is pace-scored (Auto/API are children of the same pool)"_) is
   now inaccurate per the confirmed billing model and needs to describe the
   two-pool split instead. Keep the "Cause"/"Verify" sections' historical
   framing (the earlier bug is still real background), but correct the
   "What `ai` does" section to match the new implementation.
4. Add a golden fixture to `docs/shared-quota-semantics/fixtures/` — a
   Cursor account with Included/Auto both healthy and API exhausted,
   asserting the fixture produces a `burn`/`conserve` alert for API
   specifically (not suppressed), while Included/Auto continue to be
   pace-scored together as before.

### Test gate

- `pytest -q tests/test_shared_quota_semantics.py` passes with the new
  fixture.
- Manually verify against a live account if possible:
  `codexbar usage --provider cursor --source web --json --no-color` then
  `aiuse --full -q --no-tui` (commands from `docs/cursor-quota.md`'s own
  "Verify" section) — confirm an exhausted API slot now surfaces its own
  alert while a healthy Included/Auto doesn't falsely inherit urgency from
  it, and vice versa (an exhausted Included/Auto shouldn't be masked by a
  healthy API).

---

## Phase 3 — Overage/soft-ceiling awareness (Issue #20)

**Estimate:** medium, ~4–12h (the original issue estimate; the OpenCode Go
sub-item below turned out to have no possible collector-level fix, which
narrows scope slightly).

### The finding

`AccountUsage.usage_credits` is already collected (Claude via `cswap.py`,
others via `codexbar.py`/`openusage.py`) and already displayed in the human
report, but **never read by `analysis/pace.py` or `analysis/use_or_lose.py`**
(confirmed via `grep -rn usage_credits src/aiuse/` — only `models.py`,
`report.py`, and the three collectors reference it; the analysis modules do
not). This means an account with an enabled overage/extra-spend wallet gets
identical `"conserve"`/urgency treatment to one with a genuinely hard
ceiling — but the real risk profile is different: hard ceiling = lockout
risk, soft ceiling (overage available) = unplanned $ spend risk instead.

This generalizes across (at least) three real cases:

- **Claude's extra-usage wallet** — already modeled via `usage_credits`,
  just not consulted.
- **Cursor's "On-Demand" `providerCost`** — per `docs/cursor-quota.md`,
  already parsed into `usage_credits` when `limit > 0`. Same gap applies.
- **OpenCode Go's "Use balance" toggle** — falls back to a linked Zen
  prepaid balance. **Confirmed (round 2, 4-way agreement) that this
  enabled/disabled state cannot be detected programmatically at all** — no
  API, CLI flag, or config field anywhere in OpenCode's own docs
  (`opencode.ai/docs/go`, `/docs/zen`, `/docs/cli`, `/docs/server`) exposes
  it; it's console-only. Don't spend implementation time trying to collect
  this automatically — there is nothing to collect.

### Proposed implementation

1. When `account.usage_credits is not None` (a real, present signal that
   _some_ form of overage is available/in use for this account — Claude and
   Cursor both populate it this way already), qualify rather than suppress
   the `"conserve"`/`"burn"` verdict. Concretely: add a field (e.g.
   `pace.has_overage: bool` on `PaceProfile`, or a distinct verdict variant)
   so consumers can tell "conserve, hard ceiling" apart from "conserve, but
   overage is available — real risk is $ spend, not lockout."
2. For OpenCode Go specifically: add a config-only, manually-set field —
   e.g. `overage_state: enabled | disabled | unknown` on `UsageCredits` or
   `AccountUsage`, sourced only from user config/override (no collector can
   populate it automatically). **Default to `unknown`, never `disabled`** —
   defaulting to "disabled" would make the tool understate real spend risk
   exactly when a user has actually turned the fallback on. Only qualify
   urgency when the state is confirmed `enabled`; treat `unknown` the same
   as no overage info (i.e., don't let an unknown state silently suppress a
   legitimate hard-ceiling warning).
3. Extend `docs/shared-quota-semantics/` (schema + a golden fixture) to
   formalize the overage-qualified verdict, since this is exactly the kind
   of cross-provider concept that spec exists to standardize.

### Test gate

- New/updated golden fixture(s) under `docs/shared-quota-semantics/fixtures/`
  covering: (a) an account with `usage_credits` present and a `conserve`
  verdict, asserting the output correctly flags it as soft-ceiling; (b) the
  same without `usage_credits`, asserting it's still flagged as a normal
  hard-ceiling conserve.
- `pytest -q tests/test_shared_quota_semantics.py` and full `pytest -q` pass.

---

## Phase 4 — Investigate the "dual-debit" pattern (Issue #22)

**Estimate:** medium, ~4–10h, and **may end in "no code change" if the
primary-source check fails to confirm it** — this phase is explicitly
research-first, unlike Phases 1–3.

### The finding (unconfirmed — this is the point of this phase)

See Appendix A's "dual-debit" section. Two examples were reported by
individual AI models during round-2 cross-checking (OpenAI Codex Voice
dual-debiting a voice meter + the shared Codex pool; GitHub Copilot Code
Review dual-debiting AI credits + GitHub Actions minutes), each attributed
to a primary vendor doc URL/quote by the model that found it. **Neither has
been independently re-verified by a second source, nor checked against
`aiuse`'s own live collector output.** This is meaningfully less certain
than Phase 2's Cursor finding (which 4 independent checks converged on
using the same primary source).

### What to do

1. **Before writing any code**, verify at least one of the two examples
   against the vendor's own current primary documentation directly (not
   relying on this document's secondhand quotes, which may already be
   stale by the time this phase is executed — vendor docs change fast, per
   Appendix A's own repeated caveats).
2. If confirmed: decide whether this is common enough to warrant a general
   schema change (`action -> [pool_id, ...]`, one-to-many, replacing an
   implicit one-to-one assumption) versus handling it as a narrow
   provider-specific special case in the affected collector only. Given
   only one or two examples are confirmed at this point, **prefer the
   narrower special-case fix** unless a third example turns up — don't
   over-generalize the schema for two data points.
3. If NOT confirmed (vendor has since changed, or the original claim
   doesn't hold up under direct inspection): close the issue with a comment
   explaining what was checked and why it didn't hold, rather than
   force-implementing something speculative.
4. Either way, record the outcome — if implemented, add a golden fixture;
   if not, a one-line issue comment closing it out is sufficient.

### Test gate

- If implemented: new golden fixture(s) demonstrating the dual-debit
  behavior, `pytest -q tests/test_shared_quota_semantics.py` passes.
- If not implemented: no code change required; just document why in the
  issue.

### Phase 4 outcome (2026-08-02): confirmed real, no code change needed

Both examples were independently re-verified directly against current
primary vendor documentation (not the secondhand attributions this document
started with):

- **GitHub Copilot Code Review** — confirmed via GitHub's own changelog,
  [`github.blog/changelog/2026-04-27-github-copilot-code-review-will-start-consuming-github-actions-minutes-on-june-1-2026`](https://github.blog/changelog/2026-04-27-github-copilot-code-review-will-start-consuming-github-actions-minutes-on-june-1-2026/):
  _"Starting June 1, 2026, each Copilot code review will be billed in two
  ways: all Copilot usage (including code reviews) will be billed as AI
  Credits... GitHub Actions minutes will be consumed from your existing
  plan entitlement for each review that is run on private repositories."_
  Public repos are unaffected (Actions minutes stay free there).
- **OpenAI Codex Voice (desktop)** — confirmed via OpenAI's own docs
  (`developers.openai.com/codex/pricing`, which redirects to
  `learn.chatgpt.com/docs/pricing`): Desktop Voice is metered on its own
  rolling five-hour connected-minute allowance (~6 credits/minute on
  credit-based Business/Enterprise billing), and _"Tasks started through
  Voice use your existing Codex usage budget"_ — i.e. a voice-initiated
  coding task debits the voice-minute meter and the shared Codex
  task-usage/credit pool at the same time.

**Decision: no code change, for both examples, right now.** Confirming the
pattern is real doesn't by itself require action — the question is whether
`aiuse` has any live window that's incorrectly scored because of it. It doesn't:
`grep -rniE "actions.minute|voice" src/aiuse/collectors/*.py` returns
nothing. `aiuse` never tracks per-_action_ consumption at all — every
collector (`cswap`, `CodexBar`, `tokscale`, `OpenUsage`) reports
already-aggregated windows, and none of them currently expose GitHub
Actions minutes or ChatGPT Desktop Voice minutes as a queryable quota at
all. There is no existing window for either meter that could be wrongly
folded into the other pool, so the "action → exactly one pool" undercount
risk described in Appendix A is real in principle but has no live
consequence today. Per this phase's own guidance (prefer the narrow fix,
and only 2 examples are confirmed — not the 3+ that would justify a general
`action -> [pool_id, ...]` schema change), there's nothing narrower to fix
either: there's no code path currently merging these two meters.

**Revisit trigger, if this ever needs picking back up:** not "is dual-debit
real" (settled, yes) but "did a collector start reporting either meter as
its own window." If `CodexBar` ever adds a Voice-minutes slot, or
`tokscale`/`codexbar.py`'s Copilot handling starts surfacing Actions-minutes
consumption from code reviews, check at that point whether
`shared_allotment`/`independent_pool_key()` is folding it into the existing
governing window instead of treating it as its own independent pool — _that
specific merge_ would be the actual bug, not the dual-debit fact by itself.

Issue #22 is left open per the operator's standing preference for this
session (not auto-closed) — recommend closing with a link to this section
once reviewed.

---

## What was explicitly NOT done, and why

This section exists so a future agent (or the operator) doesn't waste time
re-investigating things that were already considered and deliberately set
aside, and so the reasoning behind each is preserved.

- **No code has been written yet for any of Phases 1–4 as of this
  document's creation.** This document is a plan, produced from research
  and a source-level audit; implementation was explicitly out of scope for
  the session that produced it (the operator asked for a plan, not
  execution). All four issues are open on GitHub as of this writing.
- **GitHub Copilot's current billing model was deliberately NOT turned into
  an issue**, despite being investigated in both research rounds, because
  the 4 AI cross-checks gave 3+ mutually inconsistent descriptions of it
  (legacy premium-request SKUs vs. pure AI-credits vs. sequential
  base+flex), and `aiuse`'s own collector already tracks several
  credits-related fields for Copilot — filing a "this might be stale"
  ticket without concrete evidence something is actually broken would have
  been speculative rather than well-grounded. If this needs revisiting,
  start from `collectors/tokscale.py`'s current Copilot handling.
- **Antigravity's Gemini-vs-Claude/GPT hard pool separation (already
  implemented in `independent_pool_key()`) was NOT changed or filed as a
  bug**, despite two of four round-2 checks saying they couldn't verify it
  from Antigravity's current public docs. The existing code isn't
  demonstrably wrong — it matches earlier, plausibly-still-accurate
  sources — so this is recorded as a lower-confidence-than-assumed caveat,
  not a confirmed defect. Re-verify if this area is ever touched for other
  reasons.
- **Claude's model-specific weekly sub-limit (Sonnet-only vs Opus-only vs
  a distinct "Fable 5" sub-cap) was NOT resolved to one specific claim.**
  Sources disagree with each other, plausibly because Anthropic's exact
  tiering has genuinely shifted across the research window. No code in
  `aiuse` currently depends on knowing which model the sub-limit applies
  to (the `usage.scoped` per-model window mechanism in `cswap.py` is
  generic, not model-specific-hardcoded), so there was nothing to fix — this
  is recorded purely so a future reader doesn't mistake the disagreement
  for a research failure.
- **The "fixed-calendar vs. rolling" question for Claude's weekly
  reset was investigated and found to be out of `aiuse`'s scope entirely**
  — `resets_at` for Claude is parsed directly from upstream `cswap`'s own
  reported timestamp (`collectors/cswap.py`), not independently computed
  by `aiuse`. If this timestamp is ever wrong, it's a `cswap` (separate,
  upstream, not-this-repo) bug, not an `aiuse` one.
- **A possible second (model-specific) Claude weekly window was checked
  and found to already have a code path** (`usage.get("scoped")` in
  `collectors/cswap.py`, producing labels like `"Claude Code weekly —
{model_name}"`) — contingent on upstream `cswap` actually populating that
  field for a given account's plan tier. Not an `aiuse` gap; nothing to fix
  here unless `cswap` itself needs updating.
- **The `payg_api` billing_kind enum value was checked and confirmed to be
  genuinely emitted** by real collector code paths (`codexbar.py`,
  `cswap.py`), not dead/aspirational enum surface — no action needed.
- **Windows with unknown duration (`window_minutes: None`, e.g. Grok) were
  checked and confirmed to correctly degrade to low-confidence/unknown pace
  rather than fabricating a duration class from label text alone** — no
  action needed, this is already handled safely by
  `classify_window_minutes()` returning `None` on `None` input.
- **The Cursor pool-independence finding (Phase 2) is high-confidence but
  not 100% vendor-guaranteed in exact wording.** Cursor's docs state the
  pools are separate and reset independently; they do not contain an
  explicit sentence like "exhausting one has zero effect on the other's
  balance." That non-offsetting behavior is a reasonable, strong inference
  from "two separate usage pools" framing, treated as high-confidence in
  this plan, but it's an inference, not a literal quote — worth knowing if
  Phase 2's implementation ever needs to be reconsidered.

## Appendix C: Additional vendor notes to investigate (not yet actioned)

Flagged for a future pass — none of these have been verified or turned into
a phase/issue yet, unlike everything above.

- **DeepSeek peak/valley time-of-day pricing** (operator-relayed, not yet
  independently verified against DeepSeek's own current pricing page):
  DeepSeek's API is expected to charge roughly 2x the regular rate during
  peak hours, applying to all billing items. Peak hours as relayed: **1:00
  AM–4:00 AM and 6:00 AM–10:00 AM UTC** (equivalently 9:00 AM–noon and
  2:00 PM–6:00 PM UTC+8). This is a genuinely different kind of concept
  from everything in Appendix A/B — it's a **time-of-day cost multiplier**,
  not a quota/pool structure, and doesn't interact with the pace/burn/conserve
  algorithm at all (DeepSeek is `prepaid_balance`, which per PP1 never gets
  burn/conserve urgency regardless of time of day). If this is worth acting
  on, it'd be a distinct feature — e.g. a cost-aware suggestion ("route
  prepaid/DeepSeek spend to off-peak hours") — not a fix to any of Phases
  1-4 above. Verify against DeepSeek's own current pricing docs before
  building anything on this; time-of-day pricing details like this are
  exactly the kind of thing that changes without much notice.
