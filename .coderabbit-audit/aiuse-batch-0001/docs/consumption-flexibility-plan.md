# Multi-Dimensional Use-It-or-Lose-It Scoring

> **Status update (2026-07-23):** a code review found several correctness bugs in
> this design's implementation — most importantly, a self-cancelling formula that
> pins throttled windows (e.g. Claude's 5-hour quota) at maximum urgency regardless
> of actual usage. See `docs/code-review-2026-07-23.html` (§1–2) for the findings and
> `docs/fix-implementation-plan.md` (Phase 2) for the pace-based scoring redesign
> that supersedes the model below. The problem framing here is still accurate
> background; the scoring mechanics it led to are being replaced.

## Problem

Current scoring (`use_or_lose.py:_score`) is one-dimensional — it weights **deadline
proximity** heavily and applies a blunt binary filter that discards all short-refill
windows (≤ 360 min). This hides real plan value being wasted daily and fails to
distinguish between:

- **Burstable** quotas (Codex monthly, OpenCode Go monthly): can consume 100% in one
  session. Deadline proximity is the only real constraint.
- **Throttled** quotas (Claude 5h, Gemini 5h): rate-limited per refill cycle. A user
  literally _cannot_ burn through allotment faster than the refill rate. Missing
  one cycle wastes little; habitually missing them wastes significant plan value
  over time.

A throttled window with 80% remaining and 2h left is _more urgent in calendar
terms_ than a burstable monthly window with 80% remaining and 10 days left —
because you only have one shot at the throttled window before it resets, while
the burstable window can wait.

When describing what a user should do _now_ versus _later_, and how to allocate
coding time across providers, all three dimensions matter.

## Three Dimensions

### 1. Value at Risk (stake size)

> _How much money is at stake if this window resets fully unused?_

Computed automatically from config — no manual math in YAML:

```
active_cycles_per_month = (waking_hours_per_day * 30.44 days) / (window_duration_minutes / 60)
value_per_refill = monthly_price / active_cycles_per_month
value_at_risk_usd = value_per_refill * (remaining_percent / 100) * value_multiplier
```

Waking-hours correction: a $20/month plan split across ~16 waking hours/day yields ~90
usable 5h windows (3/day × 30), not 146 (5h × 24/7 math). Each 5h window is worth
~$0.22, not ~$0.14. This matters for the Accumulating Waste metric over a month.

An optional `value_multiplier` (per provider, per window duration) allows tuning when
a short window on a high-capability model is worth more per dollar than the same
duration on a weaker model (e.g., Claude 5h at 1.4× vs Gemini 5h at 1.0×). Defaults
to 1.0 everywhere.

**Data needed:**

- `window_duration_minutes` (already captured as `window_minutes` on `QuotaWindow`)
- plan `monthly_price` (from config)
- `waking_hours_per_day` (config, default 16)
- `remaining_percent` / `used_percent`
- Optional: `value_multiplier` per provider/window (config, default 1.0)
- Optional: per-token pricing for token-based quotas

### 2. Consumption Flexibility (burstability)

> _How fast can you physically consume the remaining allocation?_

| Flexibility           | Description                          | Example                            |
| --------------------- | ------------------------------------ | ---------------------------------- |
| 1.0 (fully burstable) | Use entire window in one session     | Codex monthly, OpenCode Go monthly |
| 0.5 (semi-throttled)  | Burst possible but some daily gating | Grok weekly, Cursor monthly        |
| 0.0 (fully throttled) | Max consumption = refill rate × time | Claude 5h, Gemini 5h               |

**Data needed:**

- `window_duration_minutes` (shorter window → lower flexibility)
- `refill_capacity` + `refill_capacity_unit` (tokens vs. requests — see below)
- Provider-specific overrides from config
- Phase 4: observed burn rates from snapshots

**Tokens vs. requests distinction:**

For API quotas (cswap, scripts, automated runs), the bottleneck is token throughput.
For web UI quotas (Claude 5h, Gemini 5h, Grok web), the bottleneck is _messages_ or
_requests_ — a human hits the 40-message limit long before hitting the token ceiling.

The burn-rate math must respect `refill_capacity_unit`:

- `"tokens"` → divide by `max_tokens_per_minute` (config, default 200k for API)
- `"requests"` → divide by an assumed human interaction rate (config, default 0.5
  requests/minute — one prompt every 2 minutes)
- `"usd"` → divide by estimated $/minute from known pricing

**Derived metric — effective burn minutes:**

```
if unit == "tokens":
    max_rate = max_sustained_tokens_per_minute
elif unit == "requests":
    max_rate = 0.5  # human prompts per minute
elif unit == "usd":
    max_rate = 0.05  # approximate $ burned per minute at heavy API usage
else:
    max_rate = 1.0   # generic fallback

refill_burn_minutes = refill_capacity / max_rate
cycles_needed = max(1, ceil((remaining_percent / 100) * refill_capacity / refill_capacity))
earliest_start_minutes = cycles_needed * window_duration_minutes
earliest_start_calendar = resets_at - timedelta(minutes=earliest_start_minutes)
```

If `earliest_start_calendar < now`: you can't finish — urgency is high.

If `earliest_start_calendar > now`: you have time, but the window is short — you must
start by that calendar time.

### 3. Deadline Pressure (calendar proximity)

> _How soon does it reset?_ (Already modeled.)

This dimension remains important but interacts with flexibility:

- Burstable quota: pure calendar pressure — urgency rises as reset approaches.
- Throttled quota: calendar pressure is dampened because most of the value can't
  be saved regardless. The effective deadline is `earliest_start_calendar`, not
  `resets_at`.

**Existing:** `days_until_reset` — keep as-is.

### Calendar math edge cases

- **Timezones / DST:** All reset times are already stored as UTC in `QuotaWindow`.
  Compute everything in UTC, display in local timezone.
- **Overlapping windows from same provider:** The `earliest_start_calendar` for
  each window is independent. If a provider has both 5h and weekly windows, the
  user needs to track the tightest constraint (earliest start).
- **Partial cycle in progress:** If `now > resets_at - window_duration`, the user
  is already partway through the current cycle. `cycles_needed` counts the
  _current_ cycle as well — adjust by `ceil((resets_at - now) / window_duration)`.
- **Awkward reset times:** A 3am local reset is less actionable than a 10am reset.
  Consider displaying reset time in local TZ in the action plan, but don't adjust
  scoring — user configures waking hours for cycle-count math already.

## Data Model Changes

### QuotaWindow (extensions)

```python
@dataclass
class QuotaWindow:
    # ... existing fields ...

    # NEW: per-refill capacity when known
    refill_capacity: float | None = None       # e.g. Claude 5h block ≈ $5
    refill_capacity_unit: str | None = None    # "tokens" | "requests" | "usd"

    # NEW: is this window rate-limited internally? (Some providers enforce
    # additional throttles within a quota window, e.g. Grok rate-limits)
    internal_throttle: bool = False
```

### Flexibility scoring fields — kept separate from QuotaWindow (derived, not raw)

```python
@dataclass
class FlexibilityProfile:
    """Derived per-window consumption characteristics (not raw data)."""
    flexibility_class: FlexibilityClass
    consumption_flexibility: float           # 0.0–1.0 continuous
    value_at_risk_usd: float | None
    cycles_needed: int | None
    earliest_start_calendar: datetime | None
    effective_burn_minutes: float | None
```

### UseOrLoseAlert (extensions)

```python
@dataclass
class UseOrLoseAlert:
    # ... existing fields ...

    flexibility_profile: FlexibilityProfile | None = None

    # Convenience accessors (delegate to profile)
    @property
    def value_at_risk_usd(self) -> float | None:
        return self.flexibility_profile.value_at_risk_usd if self.flexibility_profile else None

    @property
    def consumption_flexibility(self) -> float | None:
        return self.flexibility_profile.consumption_flexibility if self.flexibility_profile else None
```

### New enum: FlexibilityClass

```python
class FlexibilityClass(str, Enum):
    BURSTABLE = "burstable"         # use all at once
    SEMI_THROTTLED = "semi"         # burst possible but day-capped
    THROTTLED = "throttled"         # strictly rate-limited per refill
```

## Scoring Algorithm

### Weighted composite with continuous redistribution

Static weights fail: a fully burstable window (flexibility=1.0) would cap at
`w_value=0.35 + w_deadline=0.35 = 0.70` max score — HIGH, never CRITICAL, even
with $20 at risk and 10 minutes left in the month.

Continuous redistribution: as flexibility increases, flex weight smoothly transfers
to value and deadline.

```
base_w_value   = 0.35
base_w_flex    = 0.30
base_w_deadline = 0.35

flex = consumption_flexibility  # 0 (throttled) → 1 (burstable)

redistributed = base_w_flex * flex          # freed weight
w_flex    = base_w_flex - redistributed     # shrinks as burstability rises
w_value   = base_w_value + redistributed * 0.50  # half to value
w_deadline = base_w_deadline + redistributed * 0.50  # half to deadline

# At flex=0.0: w_value=0.35, w_flex=0.30, w_deadline=0.35  (all dimensions active)
# At flex=0.5: w_value=0.425, w_flex=0.15, w_deadline=0.425
# At flex=1.0: w_value=0.50, w_flex=0.00, w_deadline=0.50  (CRITICAL reachable)
```

Composite score:

```
urgency_score = w_value * value_urgency + w_flex * flexibility_urgency + w_deadline * deadline_urgency
```

### Per-dimension urgency functions (0–100)

**value_urgency:**

```
# Value-at-risk as fraction of user's most expensive plan, normalized to 0–100
max_plan_price = max(plan.monthly_price for plan in configured plans, default=20)
value_urgency = clamp(value_at_risk_usd / max_plan_price * 100, 0, 100)
```

**flexibility_urgency:**

```
if flex >= 0.9:
    urgency = 0
elif flex >= 0.5:
    urgency = 30 * (1 - flex) + 20 * clamp(0.5, 0, 1)  # semi: moderate pressure
else:
    if earliest_start_calendar < now:
        urgency = 100  # already behind
    else:
        ratio = (earliest_start_calendar - now) / max((resets_at - now), timedelta(minutes=1))
        urgency = 60 + 40 * (1 - clamp(ratio, 0, 1))
```

**deadline_urgency:**

```
# Days-until-reset urgency curve, dampened for throttled windows
raw_deadline = 100 if days <= 0.5 else 80 if days <= 1 else 60 if days <= 3 else 40 if days <= 7 else 20 if days <= 14 else 5
deadline_urgency = raw_deadline * (1 - flex * 0.4)  # throttled: deadline panic matters less
```

### Tier assignment

Map composite score to `Urgency` enum:

| Score range | Urgency                                                              |
| ----------- | -------------------------------------------------------------------- |
| ≥ 80        | CRITICAL                                                             |
| ≥ 60        | HIGH                                                                 |
| ≥ 40        | MEDIUM                                                               |
| ≥ 20        | LOW                                                                  |
| < 20        | INFO (suppressed unless `value_at_risk_usd > min_value_at_risk_usd`) |

### Alert fatigue controls

Two thresholds prevent noisy output from tiny windows:

- `min_value_at_risk_usd: 0.50` — suppress alerts where absolute stake is trivial.
- `min_value_fraction: 0.05` — suppress alerts where the window is worth ≤ 5% of
  the plan's monthly price (per-provider noise floor). A 5h window at $20/month is
  ~1% — below the floor. A weekly window at $20/month is ~23% — above it.

Together: a window must pass _both_ thresholds to generate an alert. A 5h window
with 100% remaining on a $20 plan has $0.22 at risk — below both thresholds,
effectively silent. The same window on a $200 plan has $2.20 at risk — above the
dollar threshold, and might show up.

## Collector Changes

### cswap

Already provides `windowMinutes` in the `usage` schema. Add parsing for:

- `refillCapacity` if cswap adds it in the future.
- Infer per-refill amount from plan type + price config.

### CodexBar

Already provides `windowMinutes` in quota blocks. Add:

- Detect `internal_throttle` from provider-specific behavior patterns.
- Use `extraRateWindows` capacity info for refill_amount.
- Derive `refill_capacity_unit` from provider type (web UI → "requests", API → "tokens").

### tokscale

Already provides usage percent + label. Add:

- Parse `window_minutes` if available from metrics.
- Cross-reference with config for plan details.

### Provider-specific flexibility overrides (Phase 1 config)

```yaml
analysis:
  consumption_flexibility_defaults:
    5h: 0.0
    daily: 0.1
    weekly: 0.7
    monthly: 1.0

  provider_overrides:
    claude:
      5h:
        flexibility: 0.0 # fully throttled
        refill_capacity_unit: "requests"
      weekly:
        flexibility: 0.8 # somewhat burstable within week
    grok:
      weekly:
        flexibility: 0.5 # rate-limited internally
      monthly:
        flexibility: 0.8
    opencode-go:
      weekly:
        flexibility: 1.0 # fully burstable
      monthly:
        flexibility: 1.0
```

## Display Design

### Per-window detail (pretty report)

Each window line gains a **three-bar micro-chart**. Throttled windows use `░` to
visually distinguish from burstable `█`:

```
Codex · djbclark@gmail.com · Codex weekly quota
  value  ████████░░  80% at risk ($3.70 of $4.62)
  flex   ██████████  100% burstable — 1 heavy session will cover it
  clock  ██████░░░░  60% urgency · resets in 3.0d (Fri 10:00)

Claude 5h · djbclark@mit.edu
  value ░░░░░░░░░░  <1% at risk   ($0.11 of $0.22)
  flex  ░░░░░░░░░░   0% burstable — use it now, or accept losing it
  clock ░░░░░░░░░░  80% urgency · resets in 0.2d (10:00)
  →  below alert threshold — no action needed
```

"What would I need to do" estimate for windows above threshold:

```
  ·  ~1.8 hours of focused use remaining at your typical pace
  ·  or ~3 prompts at current burn rate
```

### Unified action plan section

Time-bucketed with a framing that treats burstable capacity as opportunity, not
crisis:

```
## Unified Action Plan
----------------------------------------------------------------------
Available capacity this cycle: $42.18 across 8 windows (3 providers).

  THIS WEEK (start now — capacity will reset or needs lead time)
  ─────────────────────────────────────────────────────────────
  !!! OpenCode Go weekly · 99% left · 4.5d · $14.85 available
      Burstable — excellent window for a heavy scripting session.
  !!  Grok usage limit   · 22% left · 0.8d · $0.92 available
      Throttled — single shot, use it or it's gone.
  ·   Claude 5h (mit)    · 0% used  · 0.2d
      Fully used this cycle. No action needed.

  THIS WEEKEND (plan ahead)
  ─────────────────────────
  !!  Codex weekly        · 88% left · 6.4d · $4.07 available
      Burstable — Saturday deep-work session will cover it.

  LATER THIS MONTH
  ────────────────
  ·   Cursor monthly      · 41% left · 11.5d · $8.20 available
      Semi-throttled — steady daily usage will exhaust it naturally.

  THROTTLED — ACCUMULATING WASTE
  ──────────────────────────────
  These windows refill so fast you can't use them all. The system
  estimates how much plan value is silently wasted each month.

  ·   Gemini 5h: 12% used per cycle (7-day rolling average across 18 cycles).
      At this rate ~$1.10/month of your $20 plan is wasted.
      Consider: is this subscription worth it for your actual usage pattern?
  ·   Claude 5h (gmail): 68% used per cycle (7-day rolling average across 18 cycles).
      Efficiently used. No waste concern.
```

### JSON output

```json
{
  "urgency": "critical",
  "provider": "opencode-go",
  "window_label": "OpenCode Go weekly quota",
  "remaining_percent": 99.0,
  "days_until_reset": 4.5,
  "consumption_analysis": {
    "value_at_risk_usd": 14.85,
    "flexibility_class": "burstable",
    "consumption_flexibility": 1.0,
    "cycles_needed": 1,
    "earliest_start_calendar": null,
    "effective_burn_minutes": 120.0,
    "burn_estimate": "~1 heavy session"
  },
  "score": 88.5,
  "message": "..."
}
```

## Phased Implementation

### Phase 1 — Surface data (no scoring changes)

**Goal:** Collect and expose all three dimensions without changing urgency output.

1. Add `FlexibilityProfile` dataclass and `FlexibilityClass` enum to `models.py`.
2. Add `refill_capacity`, `refill_capacity_unit`, `internal_throttle` to `QuotaWindow`.
3. Add `flexibility_profile` to `UseOrLoseAlert`.
4. Add new config schema:
   - `plans.<provider>.monthly_price` (required for value-at-risk)
   - `plans.<provider>.value_multiplier` (optional, default 1.0)
   - `analysis.waking_hours_per_day` (default 16)
   - `analysis.consumption_flexibility_defaults` (per-duration defaults)
   - `analysis.provider_overrides` (per-provider flexibility + capacity overrides)
   - `analysis.max_sustained_tokens_per_minute` (default 200k)
   - `analysis.max_requests_per_minute` (default 0.5)
   - `analysis.min_value_at_risk_usd` (default 0.50)
   - `analysis.min_value_fraction` (default 0.05)
5. Implement `_compute_value_at_risk()` — auto-calculate from monthly_price + duration,
   no manual dollar values in YAML.
6. Implement `_compute_flexibility_profile()` — derive flexibility from config defaults
   and provider overrides.
7. Implement `_compute_burn_estimate()` — tokens vs. requests awareness.
8. Show dimensions in pretty report behind `--show-consumption` flag.
9. Include `consumption_analysis` in JSON output.
10. **No scoring changes.** Existing urgency logic unchanged.

### Phase 2 — New scoring (opt-in)

**Goal:** Full 3D scoring formula with continuous weight redistribution.

1. Implement `_score_multi_dimension()` with continuous weight blending.
2. Gate behind `analysis.use_multi_dim_scoring: true` config flag.
3. Implement `_throttled_waste_summary()` with rolling 7-day average utilization
   (requires Phase 1 snapshot persistence — add `~/.cache/aiuse/snapshots/`).
4. `--alerts-only` supports both old and new scoring.
5. Add tests:
   - Continuous weight redistribution at flex=0.0, 0.5, 1.0.
   - Burstable window with $20 at risk, 0.01 days → CRITICAL (verifies bug fix).
   - Throttled window with earliest_start in past → elevated urgency.
   - Zero remaining → score 0, suppressed.
   - Missing plan price → graceful degradation, no value dimension.

### Phase 3 — Unified action plan (default on)

**Goal:** Replace flat summary with time-bucketed narrative.

1. Implement `render_action_plan()` in `report.py`:
   - Group into THIS WEEK / THIS WEEKEND / LATER THIS MONTH / THROTTLED WASTE.
   - Each item shows: urgency icon, provider, window, remaining%, days, $ at risk,
     flexibility class, one-line prose recommendation.
   - Throttled waste section: compute from 7-day rolling data.
2. Frame burstable items as "available capacity" / "excellent time for X" — never
   as crisis.
3. Add `--traditional-summary` flag for old format.
4. Remove `use_multi_dim_scoring` gate — new scoring is default.
5. Remove `--show-consumption` gate — dimensions are always visible.
6. Update README examples.

### Phase 4 — Consumption rate learning (experimental)

**Goal:** Replace configured flexibility estimates with observed burn rates.

1. Persist snapshots to `~/.cache/aiuse/snapshots/` (started in Phase 2 for waste tracking).
2. Diff consecutive snapshots to compute _actual_ per-window burn rate.
3. Feed learned rates back into flexibility scoring.
4. Detect chronic waste patterns and suggest config tuning or subscription changes.
5. Suggest `provider_overrides` entries based on observed data.

**Phase 4 is optional / long-term.** Phases 1–3 deliver the main practical benefit.
Treated as experimental — gated behind `analysis.learn_from_history: true`.

## Testing Strategy

### Unit tests

- `_compute_value_at_risk(monthly_price, window_minutes, remaining_pct, waking_hours)` — correct dollar amounts with waking-hour adjustment.
- `_compute_value_at_risk()` with `value_multiplier: 1.4` → correct scaling.
- `_classify_flexibility(window_minutes, config)` — maps duration → class using defaults + overrides.
- `_redistribute_weights(flexibility)` — verifies sums to 1.0 at flex=0.0, 0.5, 1.0.
- `_score_multi_dimension()` → CRITICAL for burstable $20 risk + 0.01d deadline (bug fix verification).
- `_consumption_cycles(remaining, refill_capacity, unit)` — correct for tokens vs requests vs usd.
- `_earliest_start(cycles, window_duration, resets_at, now)` — correct calendar math, handles past deadline.
- `_throttled_waste_summary(snapshots)` — 7-day rolling average math.
- `_burn_estimate(values)` — correct prose for different units ("~3 prompts" vs "~1.8 hours").
- Alert fatigue: window below both thresholds → suppressed. Above one → passes.

### Integration tests

- Full snapshot with mixed burstable + throttled + semi windows → correct sort order.
- Config `plans.claude.monthly_price: 20` + 5h window → `value_at_risk_usd ≈ $0.22` (waking-hours corrected).
- Missing plan config → `value_at_risk_usd = None` (graceful degradation, score without value dimension).
- Throttled window with `earliest_start_calendar < now` → elevated flexibility urgency.
- Burstable window with 90% remaining and 20 days → INFO or suppressed.

### Golden-file tests

- Captured snapshots from real cswap/CodexBar/tokscale runs → compare alert ordering and action plan output.

## Configuration Reference

```yaml
# Historical YAML sketch (pre-config.toml migration)

plans:
  claude:
    monthly_price: 20 # USD — used to auto-compute per-window value
    value_multiplier: # optional quality adjustment (default 1.0)
      5h: 1.4 # Claude 5h blocks are higher-value per dollar
      weekly: 1.0
      monthly: 1.0
  codex:
    monthly_price: 20
  grok:
    monthly_price: 30
  cursor:
    monthly_price: 20
  gemini: # alias for antigravity
    monthly_price: 20
  opencode:
    monthly_price: 10

analysis:
  # ... existing keys (min_remaining_percent, max_days_until_reset, etc.) ...

  # Enable multi-dimensional scoring (Phase 2: opt-in, Phase 3: always-on)
  use_multi_dim_scoring: false

  # How many hours per day you're typically awake and able to use AI tools.
  # Used to compute active cycles for throttled window value-at-risk.
  waking_hours_per_day: 16

  # Base weights for the three urgency dimensions. These are redistributed
  # continuously based on consumption_flexibility (see Scoring Algorithm).
  weight_value: 0.35
  weight_flexibility: 0.30
  weight_deadline: 0.35

  # Alert fatigue: both thresholds must be met for an alert to appear.
  min_value_at_risk_usd: 0.50 # absolute dollar floor
  min_value_fraction: 0.05 # per-provider fraction of monthly plan price

  # Default flexibility per window duration. Overridden by provider_overrides.
  consumption_flexibility_defaults:
    5h: 0.0
    daily: 0.1
    weekly: 0.7
    monthly: 1.0

  # Per-provider tuning. Overrides duration-based defaults.
  # Phase 4 can suggest entries based on observed burn rates.
  provider_overrides:
    claude:
      5h:
        flexibility: 0.0
        refill_capacity_unit: "requests"
      weekly:
        flexibility: 0.8
    grok:
      weekly:
        flexibility: 0.5
        refill_capacity_unit: "requests"

  # Burn-rate assumptions. "tokens" unit uses max_sustained_tokens_per_minute;
  # "requests" unit uses max_requests_per_minute.
  max_sustained_tokens_per_minute: 200000 # API throughput
  max_requests_per_minute: 0.5 # human prompt rate (~1 per 2 min)

  # Phase 4: learn actual consumption rates from persisted snapshots.
  learn_from_history: false
  snapshot_retention_days: 90
```
