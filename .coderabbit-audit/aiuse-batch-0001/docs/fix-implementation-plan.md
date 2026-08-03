# Implementation Plan: Correctness Fixes + Rating Algorithm Redesign

**Audience:** this plan is written to be executed by a sequence of AI coding agents,
one step at a time, each with no memory of the others. Every step is self-contained:
it names exact files/functions, states the bug, states the fix precisely enough that
it does not need to be re-derived, and ends with a concrete test gate.

**See also:** [`../AGENTS.md`](../AGENTS.md) for a map of this repo, and
[`../README.md`](../README.md) for what the `ai` CLI does and how to run it.

**Background reading** (do not re-derive what's already investigated):

- `docs/code-review-2026-07-23.html` — the full review report this plan is derived from
  (open the file directly in a browser for the styled version; GitHub's file viewer
  renders it as source, not HTML). Every step below traces back to a finding or design
  section in that report.
- `docs/consumption-flexibility-plan.md` — design rationale for the existing multi-dimensional scoring.

## Operating rules for every step

1. Run `.venv/bin/python -m pytest -q` **before** starting a step (confirm the baseline
   is green) and again **after** finishing it. A step is not done until the suite passes
   with the new tests included and nothing else broken.
2. Do exactly one step per work session. Do not combine steps, even small ones — each
   step is sized so a step's fix and its test fit in one focused change.
3. Do not skip ahead. Steps within Phase 1 fix independent bugs but are ordered by
   blast radius; Phase 2 steps are NOT independent — they build on each other in order.
4. The repo has a pre-existing uncommitted working-tree diff (`git diff` shows changes
   to `tokscale.py`, `use_or_lose.py`, `report.py`). Treat it as part of the starting
   baseline — Step 8 explicitly modifies part of it; don't revert it first.
5. Add tests in the existing test file for the module you're touching unless a step
   says to create a new file. Follow the existing test style in that file (plain
   `pytest`, dataclass construction, no fixtures/mocks framework beyond what's already
   used there).
6. If a step's fix reveals the described bug doesn't reproduce as written (behavior
   already differs from this plan's description), stop and report the discrepancy
   instead of guessing — do not silently skip the step.
7. Commit after each step passes, with a message naming the step number and the bug
   fixed (e.g. `Fix #4: value-at-risk can exceed plan price for monthly windows`).

---

## Phase 1 — Showstopper bugs (silent data loss / silently wrong numbers)

These are independent of each other and of the rating-algorithm redesign. Each closes
a way the tool currently reports something confidently wrong with no error printed.

### Step 1 — JSON-recovery fallback can silently return the wrong fragment

**File:** `src/ai/collectors/base.py`, function `run_json` (lines ~48–68).

**Bug:** when a tool's stdout isn't clean JSON, the fallback tries every `{`/`[`
position in stdout (capped at the first 5) and returns the **first** one that parses
successfully. A banner like `"Fetched [1] provider\n[{...real data...}]"` causes it to
return `[1]` — the real payload is discarded, with no exception raised. Two concatenated
JSON documents (e.g. a warning object printed before the data array) hit the same bug:
the first one wins.

**Fix:** replace the "first successful candidate" logic with "prefer the candidate that
consumes the most of the remaining stdout, with an immediate return for one that cleanly
consumes to end-of-string." Remove the `[:5]` cap (use all candidates — stdout from
these CLIs is at most a few KB, this is cheap).

```python
decoder = json.JSONDecoder()
best_obj, best_consumed, last_err = None, -1, None
for start in start_candidates:
    try:
        obj, end = decoder.raw_decode(stdout[start:])
    except json.JSONDecodeError as err:
        last_err = err
        continue
    if not stdout[start + end:].strip():
        return obj  # consumes the rest of stdout — this is the payload
    if end > best_consumed:
        best_obj, best_consumed = obj, end
if best_obj is not None:
    return best_obj
raise CollectorError(f"invalid JSON from {' '.join(argv)}: {last_err or 'parse failed'}") from last_err
```

**Test** (`tests/test_cli.py` or wherever `run_json` is already tested — check first;
if untested today, add `tests/test_base_run_json.py`):

- stdout `'Fetched [1] provider\n[{"a": 1}]'` → returns `[{"a": 1}]`, not `[1]`.
- stdout `'{"warning": "x"}\n[{"a": 1}]'` → returns `[{"a": 1}]`.
- stdout with only banner noise and no valid trailing JSON → still raises `CollectorError`.
- existing "clean JSON" and "banner then single JSON value" cases from before this
  change still pass unchanged.

**Done when:** new tests pass, full suite green.

---

### Step 2 — tokscale's single 120s call has no partial-result path

**File:** `src/ai/collectors/tokscale.py`, `collect_tokscale` (line ~25).

**Bug:** one bundled `tokscale usage --json` call with `timeout=120`. If tokscale's own
internal (serial) per-provider querying hangs on any one provider, the whole call times
out and every tokscale-sourced provider is lost at once. `codexbar.py` solves this exact
problem for its own source with per-provider concurrent subprocesses — that pattern
requires a `--provider` flag tokscale's CLI does not currently have (confirmed via
`tokscale usage --help`; only `--json`/`--light`/`--home` exist). True per-provider
fan-out is deferred to Phase 6. This step is the safe, immediate mitigation.

**Fix (superseded):** the 2026-07-23 review suggested 180s to match an old CodexBar
bundled budget. That number was **not** empirically justified (warm tokscale ~1s).
Current code uses a shared **45s** default for all tools, overridable via CLI
`--timeout` / `-t` and optional `config.toml` `[timeouts]` (see
`ai.config.timeout_for`). Step 2's "raise timeout" intent is closed by that
unified budget rather than by matching 180s.

**Done when:** full suite green (timeouts unified at 45s + configurable).

---

### Step 3 — All live Claude data disappears when cswap is enabled but returns nothing

**Status:** done as part of the 2026-07-23 cswap reliability work (cache hydrate +
selection fallback + tokscale in Claude cross-checks). See
[`cswap-reliability.md`](cswap-reliability.md) and tests in
`tests/test_runner_consolidation.py`. Do not re-implement; if re-running the plan,
treat this step as already green.

**File:** `src/ai/collectors/runner.py`, function `_select_and_cross_check` (the
`claude`/`cswap_authoritative` branch, ~line 98) and `_claude_cross_checks` (~line 143).

**Bug:** when `cswap_authoritative` is true (cswap enabled in config — the default),
selection for the `claude` provider takes _only_ `source == "cswap"` rows, with no
fallback. If cswap raises (binary missing, keychain locked, unsupported schema version)
or returns only its no-accounts error placeholder, CodexBar's and tokscale's live Claude
rows — which may well exist and be healthy — are discarded from the report entirely.
Separately, tokscale's Claude rows are never included in the Claude cross-check at all,
even when cswap succeeds, so a real discrepancy between cswap and tokscale would never
be flagged.

**Fix:** in `_select_and_cross_check`, when handling `provider == "claude" and
cswap_authoritative`:

```python
cswap_rows = [a for a in rows if a.source == "cswap"]
codexbar_rows = [a for a in rows if a.source == "codexbar"]
tokscale_rows = [a for a in rows if a.source == "tokscale"]
cswap_live = [a for a in cswap_rows if _has_live_data(a)]

if cswap_live:
    selected.extend(cswap_rows)  # unchanged from today
    checks.extend(_claude_cross_checks(cswap_rows, codexbar_rows, tokscale_rows))
else:
    codexbar_live = [a for a in codexbar_rows if _has_live_data(a)]
    tokscale_live = [a for a in tokscale_rows if _has_live_data(a)]
    if codexbar_live:
        selected.extend(codexbar_live)
    elif tokscale_live:
        selected.extend(tokscale_live)
    else:
        selected.extend(cswap_rows)  # keep the error row so at least the failure is visible
    checks.append(CrossCheck(
        provider="claude", account=None, status="warning",
        sources=["cswap", "CodexBar", "tokscale"],
        message=(
            "cswap (the canonical multi-account Claude source) produced no usable "
            "data this run; falling back to a non-canonical source. Multi-account "
            "Claude Code data may be incomplete or attributed to the wrong account."
        ),
    ))
continue
```

`_claude_cross_checks` gains a `tokscale_rows` parameter; include tokscale's live rows
in the same matched/unmatched accounting it already does for `codexbar_rows` (match by
lowercase email, same as the existing codexbar matching), and update its call sites and
docstring accordingly. If threading a third source through that function is awkward,
it's acceptable to add tokscale-vs-cswap comparison as a second, parallel loop inside
the same function rather than fully generalizing it — correctness matters more here
than elegance.

**Test** (`tests/test_runner_consolidation.py`):

- cswap raises / returns only its error placeholder; codexbar has live Claude rows →
  `selected` contains the codexbar rows for `claude`, and a `warning`-status cross-check
  exists explaining the fallback. (This is the exact scenario the finding verified.)
- cswap succeeds with live rows; tokscale also has a live Claude row with a differing
  `remaining_percent` → a cross-check is produced comparing cswap against tokscale (not
  silently dropped).
- cswap succeeds, no codexbar/tokscale Claude data at all → behavior unchanged from
  today (only cswap rows selected, existing cross-check messages for "no independent
  check available" still fire correctly).

**Done when:** new tests pass, full suite green.

---

### Step 4 — Monthly windows can report more dollar value than the plan costs

**File:** `src/ai/analysis/use_or_lose.py`, `_compute_value_at_risk` (~line 41).

**Bug:** `active_cycles = active_minutes_per_month / window_minutes` can be less than 1
for a monthly-bucketed window (e.g. `43800` min inferred by tokscale vs. ~29,222 waking
minutes/month → `active_cycles ≈ 0.65`). Then `value_per_refill = monthly_price /
active_cycles` exceeds `monthly_price`. Verified: a $10/mo Copilot plan reports "$13.75
at risk" for one 90%-remaining monthly window.

**Fix:** a single window's per-refill value can never exceed the whole plan's monthly
price. Clamp the divisor to at least 1:

```python
def _compute_value_at_risk(
    *, remaining, window_minutes, monthly_price, waking_hours_per_day, value_multiplier=1.0,
) -> float:
    active_minutes_per_month = waking_hours_per_day * DAYS_PER_MONTH * 60
    if active_minutes_per_month <= 0 or window_minutes <= 0:
        return 0.0
    active_cycles = active_minutes_per_month / window_minutes
    value_per_refill = monthly_price / max(active_cycles, 1.0)
    return value_per_refill * (remaining / 100.0) * value_multiplier
```

This is the only line that changes. It's a no-op for any window shorter than a month
(`active_cycles >= 1` already) and caps monthly-or-longer windows at exactly
`monthly_price * remaining_fraction * value_multiplier`.

**Test** (`tests/test_use_or_lose.py`):

- `window_minutes=44640` (monthly), `monthly_price=10`, `remaining=90` →
  `value_at_risk_usd <= 10.0 * 0.9` (was previously ~13.75).
- existing 5h/weekly test cases (where `active_cycles > 1`) produce identical numbers
  to before this change.

**Done when:** new tests pass, full suite green. (`report.py`'s `_consumption_line` calls
the same function directly, so this fix applies there too with no separate change needed.)

---

### Step 5 — "Monthly waste" figure is off by up to 10x in either direction

**File:** `src/ai/report.py`, `_throttled_waste_line` (~line 373); `src/ai/models.py`,
`UseOrLoseAlert`; `src/ai/analysis/use_or_lose.py`, `analyze_use_or_lose`.

**Bug:** `monthly_waste = value_usd * 30` assumes every window refills once per day.
`value_usd` is a **per-cycle** figure (`value_per_refill` from Step 4's function). Actual
cycles/month vary hugely by window duration (with default 16 waking hours: ~97 for a 5h
window, ~2.9 for weekly, ~0.65 for monthly). The `* 30` constant understates 5h-window
waste by roughly 3x and overstates weekly/monthly-window waste by up to ~46x.

**Fix:** this needs the window's actual duration, which `UseOrLoseAlert` doesn't
currently carry. Add it:

1. In `src/ai/models.py`, add `window_minutes: int | None = None` to `UseOrLoseAlert`
   (additive field, default `None` — no existing `to_dict()` consumer breaks; add it to
   `to_dict()`'s output dict too).
2. In `src/ai/analysis/use_or_lose.py::analyze_use_or_lose`, when constructing each
   `UseOrLoseAlert`, pass `window_minutes=window.window_minutes`.
3. In `src/ai/report.py::_throttled_waste_line`, compute the real monthly multiplier
   instead of the hardcoded `30`, using the same constants `_compute_value_at_risk`
   already uses (import `DAYS_PER_MONTH` from `ai.analysis.use_or_lose`, and read
   `waking_hours_per_day` from the `analysis` config passed into `render_report` —
   thread it down to this function the same way `_consumption_line` already receives
   `analysis: dict[str, Any]`):

```python
def _throttled_waste_line(alert: UseOrLoseAlert, s: _Style, *, waking_hours_per_day: float) -> str:
    who = alert.account or "default"
    profile = alert.flexibility_profile
    value_usd = profile.value_at_risk_usd if profile else None
    remaining = alert.remaining_percent

    if value_usd is not None and value_usd > 0.01 and alert.window_minutes:
        active_cycles = (waking_hours_per_day * DAYS_PER_MONTH * 60) / alert.window_minutes
        monthly_waste = value_usd * active_cycles
        return s.dim(
            f"  · {provider_display_name(alert.provider)} · {who} · "
            f"{alert.window_label}: {remaining:.0f}% left per cycle "
            f"(~${value_usd:.2f}/cycle ≈ ~${monthly_waste:.2f}/month wasted at this pace)"
        )
    return s.dim(
        f"  · {provider_display_name(alert.provider)} · {who} · {alert.window_label}: {remaining:.0f}% left per cycle"
    )
```

Update the one call site in `_render_action_plan` to pass
`waking_hours_per_day=float((config.get("analysis") or {}).get("waking_hours_per_day", 16))`
(mirror how `_consumption_line` already reads this same key).

**Test** (`tests/test_report.py` — this file is currently only 30 lines, meaning the
whole action-plan renderer is essentially untested; this is also where Step 30/31 adds
more coverage, but write at least these two cases now):

- 5h window (`window_minutes=300`), `value_usd=$0.18`, 16 waking hours → monthly waste
  ≈ `$0.18 * 97.4 ≈ $17.53`, not `$5.55` (previously understated).
- monthly window (`window_minutes=43800`), same `value_usd` → monthly waste ≈
  `$0.18 * 0.65 ≈ $0.12`, not `$5.55` (previously wildly overstated).
- `monthly_waste` for any window must never exceed `monthly_price` in a realistic plan
  config — assert this as a property in the test, not just the two point checks.

**Done when:** new tests pass, full suite green.

---

### Step 6 — Claude's cswap-sourced windows never get a real window duration

**File:** `src/ai/collectors/cswap.py`, `_named_window` (~line 131) and
`_window_from_block` (~line 142).

**Bug:** cswap's named blocks (`fiveHour`/`sevenDay`/`monthly`) only get
`window_minutes` populated when cswap's own JSON happens to include a `windowMinutes`
field — schema v1 doesn't guarantee it. Without `window_minutes`, multi-dimensional
scoring can't classify the window's duration bucket at all for the one provider
(Claude) where this data is most load-bearing.

**Fix:** since `_named_window` already knows _semantically_ which duration each call
site represents (it's called three times with fixed label strings for 5-hour, weekly,
monthly), backfill `window_minutes` with the known nominal duration when the block
didn't supply one:

```python
_NOMINAL_MINUTES = {"Claude Code 5-hour": 300, "Claude Code weekly": 10080, "Claude Code monthly": 43800}

def _named_window(usage: dict[str, Any], keys: tuple[str, ...], label: str) -> list[QuotaWindow]:
    for key in keys:
        block = usage.get(key)
        if isinstance(block, dict):
            window = _window_from_block(label, block)
            if window and window.window_minutes is None:
                window.window_minutes = _NOMINAL_MINUTES.get(label)
            return [window] if window else []
        if isinstance(block, (int, float)):
            return [QuotaWindow(label=label, used_percent=float(block), window_minutes=_NOMINAL_MINUTES.get(label))]
    return []
```

Leave `_generic_label`'s primary/secondary/tertiary and `scoped` paths alone — those
already infer duration bucket from `window_minutes` when present, and have no reliable
nominal label to fall back to.

**Test** (`tests/test_cswap_parse.py`):

- cswap block for `fiveHour` with no `windowMinutes` key → resulting `QuotaWindow.window_minutes == 300`.
- cswap block for `sevenDay` with an explicit `windowMinutes: 10079` → the explicit
  value is kept (backfill only applies when `window_minutes is None`).

**Done when:** new tests pass, full suite green.

---

### Step 7 — `cycles_needed` formula cancels itself out, pinning Claude 5h urgency at 100

**File:** `src/ai/analysis/use_or_lose.py`, `_compute_flexibility_profile` (~line 92–183).

**Bug:** `cycles_needed = max(1, int(round((remaining/100.0) * capacity / capacity)))` —
`capacity` divides itself out; the expression is just `round(remaining/100)`, always `1`
for any `remaining <= 149%`. This makes `earliest_start_calendar = resets_at -
1*window_minutes`, which is always in the past for an active window, which pins
`flexibility_urgency = 100.0` unconditionally in `_score_multi_dimension`. This is the
root cause of the "Claude 5h always tops the report" behavior — verified: a freshly
refilled 5h window at 95% remaining scores MEDIUM/71.8 every single run.

**This is an interim, minimal fix** — Phase 2 replaces this scoring path more
thoroughly with pace-based logic and a proper shared-allotment model. This step just
stops the formula from being nonsense in the meantime, using the already-computed
`burn_minutes` (time to consume the _full_ window's capacity at the configured rate):

```python
if capacity is not None and capacity > 0 and window.window_minutes:
    ...  # existing rate/burn_minutes computation unchanged
    burn_minutes = capacity / max(rate, 0.001)
    burn_minutes = round(burn_minutes, 1)

    burn_minutes_for_remaining = burn_minutes * (remaining / 100.0)
    cycles_needed = max(1, int(-(-burn_minutes_for_remaining // window.window_minutes)))  # ceil div

    now_dt = now or utcnow()
    if window.resets_at and isinstance(window.resets_at, type(now_dt)):
        earliest = window.resets_at - timedelta(minutes=burn_minutes_for_remaining)
```

Replaces the two lines currently computing `cycles_needed` and `earliest` inside that
`if capacity is not None...` block — nothing else in the function changes.

**Test** (`tests/test_use_or_lose.py`):

- Claude 5h override (`flexibility=0.0`, `capacity=45`, `unit="requests"`), window at
  95% remaining with 295/300 minutes left → `flexibility_urgency` must be well below
  100 (not pinned), and the resulting alert urgency must not be MEDIUM-or-above purely
  from this term (assert the specific score is meaningfully lower than the previously
  broken 71.8 — pick a concrete new expected number by running the fixed code and
  hard-coding the result, so this test locks in the corrected value).
- capacity 1, 45, and 100000 with the same `remaining` now produce **different**
  `cycles_needed` (regression check — previously all three produced exactly `1`).

**Done when:** new tests pass, full suite green.

---

### Step 8 — Uncommitted diff's new gates silently kill real multi-dim alerts

**File:** `src/ai/analysis/use_or_lose.py`, `analyze_use_or_lose`, the `if multi_dim:`
branch (~line 299–303, part of the pre-existing uncommitted diff).

**Bug:** the two new lines

```python
if days is not None and days > max_days:
    continue
if remaining < min_remaining:
    continue
```

apply `min_remaining_percent` (default 40) as a hard gate to the multi-dimensional
scoring path. A weekly window at 64% used / 36% remaining — exactly the "weekly nearly
exhausted, several days left" case this whole review started from — is filtered out
_before it is ever scored_, rather than being scored low or (per Phase 2) surfaced as a
conserve advisory.

**Fix (interim — Phase 2 replaces this gate with real conserve logic):** drop the
`remaining < min_remaining` gate from the multi-dim branch; keep `max_days` (a
reasonable horizon cap that both scoring paths should honor):

```python
if multi_dim:
    if flex_profile is None:
        continue
    if days is not None and days > max_days:
        continue
    # NOTE: the min_remaining gate is intentionally NOT applied here — see Phase 2.
    # It remains applied in the legacy (non-multi-dim) branch below, unchanged.
    urgency, score = _score_multi_dimension(...)
```

Do not touch the legacy (`else:`) branch a few lines below — it keeps its own
`remaining < min_remaining` check exactly as today.

**Test** (`tests/test_use_or_lose.py`):

- weekly window, 64% used / 36% remaining, 2 days until reset, multi-dim scoring
  enabled → an alert IS produced (previously: none). Assert its urgency/score are
  reasonable given the existing (not-yet-pace-based) scoring math — don't assert a
  specific target urgency tier here, Phase 2 will change what tier this lands in;
  just assert an alert exists and its `remaining_percent == 36.0`.
- an existing test that relied on the min_remaining gate filtering a multi-dim window
  below 40% remaining will need updating — search `tests/test_use_or_lose.py` for one
  and update its expectation rather than deleting it.

**Done when:** new/updated tests pass, full suite green.

---

## Phase 2 — Rating algorithm redesign (pace-based scoring)

Phase 1 stopped the scoring model from being actively broken. This phase replaces the
model itself so window ranking reflects real pacing instead of "how soon does this
particular window happen to reset." **These steps are sequential and depend on each
other — do not reorder.** Each step still ends in a fully passing suite; later steps
build new behavior on top without removing the legacy/multi-dim paths (both are kept as
explicit escape hatches, selectable by config).

### Step 9 — Data model additions (no behavior change yet)

**File:** `src/ai/models.py`.

Add, purely additively (nothing existing changes shape):

```python
WINDOW_NOMINAL_MINUTES = {"5h": 300, "weekly": 10080, "monthly": 43800}

def nominal_window_minutes(kind: str | None) -> int | None:
    return WINDOW_NOMINAL_MINUTES.get(kind) if kind else None

@dataclass
class PaceProfile:
    elapsed_fraction: float | None
    used_fraction: float
    pace_ratio: float | None
    projected_used_fraction: float | None
    projected_waste_fraction: float | None
    projected_waste_usd: float | None
    projected_exhaust_at: datetime | None
    governing: bool = True
    gated_by: str | None = None       # label of the enclosing window, set on children
    confidence: str = "measured"       # measured | inferred | low

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.projected_exhaust_at:
            d["projected_exhaust_at"] = self.projected_exhaust_at.isoformat()
        return d
```

In `UseOrLoseAlert`, add two fields (both optional, both additive):

```python
kind: str = "burn"                 # burn | conserve
pace: PaceProfile | None = None
```

Update `UseOrLoseAlert.to_dict()` to include `d["kind"] = self.kind` unconditionally,
and `d["pace"] = self.pace.to_dict()` when `self.pace` is set. Do not change any
existing key already in `to_dict()`.

**Test:** a plain construction/serialization test — build a `PaceProfile`, build a
`UseOrLoseAlert` with `kind="conserve"` and a `pace` set, call `.to_dict()`, assert the
new keys are present and every pre-existing key from before this change is still
present with its old value. This is the JSON-backward-compatibility guarantee for
everything that follows.

**Done when:** new test passes, full suite green (nothing else should be affected —
these are unused fields until Step 11).

---

### Step 10 — Pure pace-math functions, tested in isolation

**File:** `src/ai/analysis/pace.py` (new file — no wiring into `analyze_use_or_lose`
yet, so nothing about today's report output changes in this step).

```python
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any

from ai.models import PaceProfile, QuotaWindow, classify_window_minutes, nominal_window_minutes


def compute_pace(
    window: QuotaWindow,
    *,
    now: datetime,
    learned_rate_per_day: float | None = None,  # fraction/day, e.g. 0.30 == 30%/day
    learned_sample_count: int = 0,
    e_min: float = 0.05,
) -> PaceProfile | None:
    remaining = window.remaining()
    if remaining is None:
        return None
    used_fraction = (100.0 - remaining) / 100.0

    kind = classify_window_minutes(window.window_minutes)
    duration_minutes = window.window_minutes or nominal_window_minutes(kind)
    confidence = "measured" if window.window_minutes else ("inferred" if duration_minutes else "low")

    if not window.resets_at or not duration_minutes:
        return PaceProfile(
            elapsed_fraction=None, used_fraction=used_fraction, pace_ratio=None,
            projected_used_fraction=None, projected_waste_fraction=None,
            projected_waste_usd=None, projected_exhaust_at=None, confidence="low",
        )

    t_left_days = max(0.0, (window.resets_at - now).total_seconds() / 86400.0)
    d_days = duration_minutes / 1440.0
    elapsed = min(1.0, max(0.0, 1.0 - t_left_days / d_days))

    r_now = used_fraction / (max(elapsed, e_min) * d_days)  # fraction/day
    if learned_rate_per_day is not None and learned_sample_count > 0:
        lam = learned_sample_count / (learned_sample_count + 2.0)
        r_hat = (1 - lam) * r_now + lam * learned_rate_per_day
    else:
        r_hat = r_now

    projected_used = min(1.0, used_fraction + r_hat * t_left_days)
    waste = 1.0 - projected_used
    exhaust_at = now + timedelta(days=(1.0 - used_fraction) / r_hat) if r_hat > 1e-9 else None

    return PaceProfile(
        elapsed_fraction=elapsed, used_fraction=used_fraction,
        pace_ratio=used_fraction / max(elapsed, e_min),
        projected_used_fraction=projected_used, projected_waste_fraction=waste,
        projected_waste_usd=None,  # filled in by the caller once it knows the plan price
        projected_exhaust_at=exhaust_at, confidence=confidence,
    )


def classify_pace(
    pace: PaceProfile, *, resets_at: datetime | None, waste_alert_fraction: float,
    min_elapsed_fraction: float, conserve_min_lead_hours: float, has_learned_rate: bool,
) -> str:
    """Returns 'conserve' | 'burn' | 'on_pace' | 'unknown'."""
    if pace.projected_exhaust_at and resets_at:
        if pace.projected_exhaust_at < resets_at - timedelta(hours=conserve_min_lead_hours):
            return "conserve"
    if pace.projected_waste_fraction is None:
        return "unknown"
    if (
        pace.elapsed_fraction is not None
        and pace.elapsed_fraction < min_elapsed_fraction
        and not has_learned_rate
    ):
        return "on_pace"  # too early in the window to trust the projection
    if pace.projected_waste_fraction >= waste_alert_fraction:
        return "burn"
    return "on_pace"


def governing_partition(windows: list[QuotaWindow]) -> tuple[QuotaWindow | None, list[QuotaWindow]]:
    """Longest-duration window with usable remaining()+resets_at governs; the rest are children."""
    scored = [
        (w.window_minutes or nominal_window_minutes(classify_window_minutes(w.window_minutes)) or 0, w)
        for w in windows if w.remaining() is not None
    ]
    if not scored:
        return None, list(windows)
    scored.sort(key=lambda pair: pair[0], reverse=True)
    governing = scored[0][1]
    children = [w for w in windows if w is not governing]
    return governing, children
```

**Test** (`tests/test_pace.py`, new file):

- Live-shaped case: weekly window 64% used, elapsed ≈ 71% (2 days left of a 7-day
  window) → `classify_pace(...) == "on_pace"`.
- Weekly 90% used, elapsed ≈ 57% (3 days left of 7) → `projected_exhaust_at` is before
  `resets_at`, `classify_pace(...) == "conserve"`.
- Weekly 10% used, elapsed = 50% → `projected_waste_fraction >= 0.30` →
  `classify_pace(...) == "burn"`.
- No `resets_at` → `PaceProfile.confidence == "low"`, `classify_pace` returns
  `"unknown"`.
- `window_minutes=None` but a classifiable label bucket exists → nominal duration is
  used, `confidence == "inferred"`.
- `governing_partition` on a Claude-shaped `[5h window, weekly window]` list returns the
  weekly window as governing and the 5h window as the sole child.
- Very early in a window (`elapsed_fraction < min_elapsed_fraction`) with no learned
  rate → `"on_pace"` even if raw usage looks high (confidence gate working).

**Done when:** all new tests in `tests/test_pace.py` pass. This step touches no other
file, so the rest of the suite is unaffected by construction — confirm it's still green
anyway.

---

### Step 11 — Wire a new `"pace"` scoring mode into `analyze_use_or_lose`

**Files:** `src/ai/config.py`, `src/ai/analysis/use_or_lose.py`.

**Config** — add to `DEFAULT_CONFIG["analysis"]`:

```python
"scoring_mode": "pace",
"pace": {
    "waste_alert_fraction": 0.30,
    "min_elapsed_fraction": 0.15,
    "conserve_min_lead_hours": 4.0,
},
```

**Mode resolution** in `analyze_use_or_lose`, replacing the current
`multi_dim = bool(analysis_cfg.get("use_multi_dim_scoring", False))` line:

```python
mode = analysis_cfg.get("scoring_mode")
if mode is None:
    mode = "legacy" if analysis_cfg.get("use_multi_dim_scoring", True) is False else "pace"
# mode is one of: "legacy" (today's `_score`), "multi_dim" (today's `_score_multi_dimension`,
# kept verbatim as a frozen escape hatch), "pace" (new, default).
```

This preserves back-compat: anyone with `use_multi_dim_scoring: false` in their config
keeps getting the legacy path unchanged; everyone else moves to `"pace"` unless they
explicitly set `scoring_mode: "multi_dim"` to keep today's (Phase-1-patched) behavior.

**New branch** (add alongside the existing `if multi_dim: ... else: ...`, restructured
as `if mode == "pace": ... elif mode == "multi_dim": ... else: ...`) — **for this step
only, without shared-allotment gating yet** (that's Step 12): compute pace per window
independently.

```python
if mode == "pace":
    pace_cfg = analysis_cfg.get("pace") or {}
    pace = compute_pace(window, now=now, learned_rate_per_day=None, learned_sample_count=0)
    if pace is None:
        continue
    verdict = classify_pace(
        pace, resets_at=window.resets_at,
        waste_alert_fraction=float(pace_cfg.get("waste_alert_fraction", 0.30)),
        min_elapsed_fraction=float(pace_cfg.get("min_elapsed_fraction", 0.15)),
        conserve_min_lead_hours=float(pace_cfg.get("conserve_min_lead_hours", 4.0)),
        has_learned_rate=False,
    )
    if verdict in ("on_pace", "unknown"):
        continue
    if days is not None and days > max_days and verdict != "conserve":
        continue

    v_cycle = _compute_value_at_risk(
        remaining=100.0, window_minutes=window.window_minutes or 0,
        monthly_price=monthly_price or 0.0, waking_hours_per_day=waking, value_multiplier=window_value_mult,
    ) if monthly_price and window.window_minutes else 0.0
    pace.projected_waste_usd = round((pace.projected_waste_fraction or 0.0) * v_cycle, 2) if pace.projected_waste_fraction else None

    if verdict == "burn":
        if pace.projected_waste_usd is not None and pace.projected_waste_usd < min_value_usd:
            plan_price = float(monthly_price or 0)
            if plan_price <= 0 or (pace.projected_waste_usd / plan_price) < min_value_fraction:
                continue
        score = min(100.0, 30.0 + 70.0 * (pace.projected_waste_fraction or 0.0))
        urgency = Urgency.CRITICAL if score >= 90 else Urgency.HIGH if score >= 75 else Urgency.MEDIUM if score >= 50 else Urgency.LOW
        kind = "burn"
    else:  # conserve
        t_left = window.days_until_reset(now) or 0.0
        t_ex = (pace.projected_exhaust_at - now).total_seconds() / 86400.0 if pace.projected_exhaust_at else t_left
        score = 60.0 + 40.0 * max(0.0, min(1.0, (t_left - t_ex) / t_left)) if t_left > 0 else 60.0
        urgency = Urgency.HIGH if (t_left - t_ex) >= 1.0 else Urgency.MEDIUM
        kind = "conserve"

    message = _pace_message(account=account, window=window, verdict=verdict, pace=pace, days=days)
    alerts.append(UseOrLoseAlert(
        urgency=urgency, provider=account.provider, account=account.account,
        window_label=window.label, remaining_percent=remaining, days_until_reset=days,
        plan=account.plan or plan_meta.get("name"), message=message, source=account.source,
        score=score, flexibility_profile=flex_profile, window_minutes=window.window_minutes,
        kind=kind, pace=pace,
    ))
    continue
```

Write `_pace_message`, a small new function mirroring the
existing `_message` helper: for `kind == "burn"`, phrase it like today's messages; for
`kind == "conserve"`, phrase it as "pace yourself — projected to run out before reset,
resets {when}", with no `$` figures inside the message string itself (dollars live only
in the structured `pace`/`flexibility_profile` fields and in `report.py`, matching how
`value_at_risk_usd` is already kept out of message text today).

This step does **not** yet do shared-allotment gating (Claude's 5h and weekly still
score independently under `"pace"` mode) — that's Step 12, and it changes the outcome
of the canonical Claude scenario. Land this step first so pace math and mode dispatch
are proven independently of the gating logic.

**Test** (`tests/test_use_or_lose.py`, add a `pace mode` test section):

- Reproduce the three canonical Step-10 scenarios end-to-end through
  `analyze_use_or_lose` with `scoring_mode: "pace"` (weekly 64%/71%-elapsed → no alert;
  weekly 90%/57%-elapsed → one `kind=="conserve"` alert; weekly 10%/50%-elapsed → one
  `kind=="burn"` alert).
- `scoring_mode: "multi_dim"` still reproduces exactly today's (Phase-1-patched)
  multi-dim numbers — pin down at least one existing multi-dim test's expected score
  and confirm it's unchanged by this step.
- `use_multi_dim_scoring: false` (no `scoring_mode` key) still uses the legacy `_score`
  path unchanged.

**Done when:** new tests pass, full suite green.

---

### Step 12 — Shared-allotment gating (this is what actually fixes "Claude 5h always wins")

**File:** `src/ai/analysis/use_or_lose.py`, `analyze_use_or_lose`'s pace branch;
`src/ai/config.py`.

**Config** — add `"shared_allotment": true` to the top level of
`provider_overrides.claude` (alongside its existing `"5h": {...}` sub-dict), and add a
new `provider_overrides.opencode: {"shared_allotment": true}` entry. Both go inside
the already-existing `provider_overrides` dict — these are new sibling keys, not
replacing anything there.

**Logic change:** restructure the per-window loop in the `"pace"` branch to operate
per-account instead of per-window when `shared_allotment` is set for that provider:

1. Before iterating `account.windows`, check
   `bool((cfg.get("provider_overrides") or {}).get(provider_key, {}).get("shared_allotment"))`.
   If true, call `governing_partition(account.windows)` (Step 10) once for the account.
2. Only the **governing** window goes through the scoring logic from Step 11.
3. **Children never produce their own alert** — `continue` past them entirely in the
   pace branch when `shared_allotment` is on and they are not the governing window.
4. When the governing window's verdict is `"burn"`, use the child's `refill_capacity`/
   `effective_burn_minutes` (from the governing window's OWN `flexibility_profile`,
   which is unaffected by this — it's about the governing window's own throttle
   characteristics if it has any, not the child's) to phrase burn logistics. Simpler
   and sufficient for this step: append to the burn message a note naming the
   suppressed child window(s), e.g. `"(this also covers your 5-hour window — no need
to burn it separately)"`. Do not attempt to recompute the child's own
   `cycles_needed` against the governing window's duration in this step — that
   refinement is optional polish, not required to fix the core complaint.
5. When the governing window's verdict is `"conserve"`, the message must explicitly
   name the child: `"Avoid burning 5-hour sessions — they draw the same weekly budget
you're already close to exhausting."`
6. If the governing window itself has no computable pace (`compute_pace` returns
   `None`, e.g. missing `remaining()`), fall back to scoring each window
   independently as if `shared_allotment` were off for this account this run, and add
   a one-line note (via the existing `notes` mechanism on the account, or folded into
   any alert message produced) that the governing window's status was unavailable.
7. A lone window with no siblings of a different duration bucket is unaffected — it's
   simply its own governing window with no children.

**Test** (`tests/test_use_or_lose.py`):

- **The core regression test**: an account with a 5h window at 97% remaining (fresh)
  and a weekly window at 64% used/36% remaining (elapsed ≈71%, "on pace") →
  `analyze_use_or_lose` with `scoring_mode: "pace"` produces **zero** alerts for this
  account (weekly is on-pace, 5h is suppressed as a child) — this is the exact
  live-data scenario from the original complaint, now silent instead of the 5h window
  topping the list.
- Same account, weekly at 90% used/3 days left → exactly ONE alert, `kind=="conserve"`,
  for the weekly window; the 5h window produces no separate alert; the conserve
  message mentions avoiding 5-hour sessions.
- Same account, weekly at 10% used/50% elapsed → exactly ONE alert, `kind=="burn"`, for
  the weekly window, ranked with a non-trivial `projected_waste_usd`; no separate 5h
  alert.
- `shared_allotment` explicitly set to `false` for a test provider → both windows score
  independently (two possible alerts, not one) — confirms the override actually
  disables gating rather than being ignored.
- A lone 5h window with no weekly sibling on the account still can alert normally.

**Done when:** new tests pass (especially the core regression test above — this is the
one the user explicitly asked for), full suite green.

---

### Step 13 — Blend in learned burn rates from snapshot history (optional signal, not required for correctness)

**File:** `src/ai/analysis/history.py`, `src/ai/analysis/use_or_lose.py`.

**Refactor** (no behavior change to the existing `learn_from_history` feature): extract
the burn-rate computation that `compute_learned_flexibility` already does internally
before collapsing it to a flexibility score, into a reusable function:

```python
def compute_learned_burn_rates(
    *, current: Snapshot, retention_days: int = _DEFAULT_RETENTION_DAYS, min_snapshots: int = _DEFAULT_MIN_SNAPSHOTS,
) -> dict[str, tuple[float, int]]:
    """Returns {'provider:duration_kind': (avg_fraction_per_day, sample_count)}."""
    # Body: identical loop to the existing compute_learned_flexibility (lines ~84-135),
    # but instead of collapsing provider_window_burns to a flexibility score at the end,
    # return {pk: (avg_burn / 100.0, len(burns)) for pk, burns in provider_window_burns.items() if len(burns) >= 2}.

def compute_learned_flexibility(*, current, retention_days=90, min_snapshots=2) -> dict[str, float]:
    rates = compute_learned_burn_rates(current=current, retention_days=retention_days, min_snapshots=min_snapshots)
    return {k: _burn_rate_to_flexibility(rate * 100.0) for k, (rate, _n) in rates.items()}
```

**Wiring:** in `analyze_use_or_lose`, when `analysis_cfg.get("learn_from_history")` is
true and `mode == "pace"`, call `compute_learned_burn_rates(...)` once per run
(alongside the existing `compute_learned_flexibility` call — both can coexist; do not
remove the existing flexibility-learning feature, this is additive). Look up
`f"{provider}:{duration_kind}"` for the governing window and pass its `(rate,
sample_count)` into `compute_pace(..., learned_rate_per_day=rate,
learned_sample_count=sample_count)` and into `classify_pace(...,
has_learned_rate=True)`.

**Test** (`tests/test_history.py` and `tests/test_use_or_lose.py`):

- `compute_learned_burn_rates` on the same fixture data already used to test
  `compute_learned_flexibility` returns rates whose sign/magnitude ordering matches
  (a window `compute_learned_flexibility` scored as more burstable should have a higher
  raw rate here) — a consistency check between old and new, not a new independent
  behavior.
- `compute_learned_flexibility`'s existing tests pass unchanged after the refactor
  (this is a pure refactor of that function's internals).
- In `analyze_use_or_lose` with `learn_from_history: true` and a history fixture
  showing a high burn rate for a window still very early in its cycle
  (`elapsed_fraction < min_elapsed_fraction`), `classify_pace` now returns `"burn"`
  instead of `"on_pace"` (confidence gate bypassed by real historical evidence) —
  this is the one behavior change this step introduces, and it should be covered
  explicitly.

**Done when:** new/updated tests pass, full suite green. If there is no existing
snapshot history on the machine this runs on, this whole feature is inert
(`learn_from_history` defaults to `false`) — do not let its tests depend on real
`~/.cache/aiuse/snapshots` data; construct fixtures in-memory as the existing history
tests already do.

---

### Step 14 — Report rendering: Conserve section, burn-only buckets, pace detail

**File:** `src/ai/report.py`.

1. In both `_render_action_plan` and `_render_traditional_summary`, partition the
   `action` list by `alert.kind` before doing anything else:
   ```python
   conserve = [a for a in action if a.kind == "conserve"]
   action = [a for a in action if a.kind == "burn"]  # existing bucket logic below only ever sees burn alerts now
   ```
2. Render a new **Conserve** section — in `_render_action_plan`, place it _before_ the
   THIS WEEK / THIS WEEKEND / LATER THIS MONTH buckets (it's the anti-footgun case,
   it should be the first thing a user reads):
   ```python
   if conserve:
       lines.append(f"  {s.bold('CONSERVE — pace yourself, avoid lockout before reset')}")
       lines.append(s.dim(f"  {'─' * (width - 4)}"))
       for alert in sorted(conserve, key=lambda a: (-a.score,)):
           lines.append(_conserve_line(alert, s))
       lines.append("")
   ```
   Add `_conserve_line`, modeled on the existing `_action_plan_line`:
   ```python
   def _conserve_line(alert: UseOrLoseAlert, s: _Style) -> str:
       icon = URGENCY_ICON.get(alert.urgency, "   ")
       who = alert.account or "default"
       when = _human_deadline(alert.days_until_reset)
       pace = alert.pace
       lockout = f", locked out ~{pace.projected_exhaust_at.strftime('%a %H:%M UTC')}" if pace and pace.projected_exhaust_at else ""
       return (
           f"  {s.urgency(alert.urgency, icon)} {s.bold(provider_display_name(alert.provider))} · "
           f"{who} · {alert.window_label}: {alert.remaining_percent:.0f}% left · resets {when}{lockout}\n"
           f"      {s.dim(alert.message)}"
       )
   ```
3. Existing burn buckets (`_action_buckets`, `_action_plan_line`, the THROTTLED section)
   operate unchanged on the now-burn-only `action` list — no code change needed there
   beyond receiving the filtered list.
4. `_action_plan_line`: when `alert.pace` is set, append a pace fragment after the
   existing value-at-risk fragment: `f" · pace {alert.pace.pace_ratio:.1f}x — projected
{alert.pace.projected_waste_fraction:.0%} unused"`.
5. `_consumption_line` (per-window detail, shown for every window regardless of
   whether it alerted): if `compute_pace(...)` (imported from `ai.analysis.pace`)
   returns a profile, append `f"pace {profile.pace_ratio:.1f}x"` so on-pace windows
   remain visible in the detail view even though they raise no alert. This function
   already receives `analysis: dict[str, Any]` — read `scoring_mode`/`pace` config
   from it the same way it already reads `waking_hours_per_day`.
6. `_render_traditional_summary`: same `conserve`/`action` split; render conserve items
   under a new `"Conserve — pace until reset"` sub-list, positioned before the existing
   "Action plan" list.

**Test** (`tests/test_report.py` — expand this file significantly; it's currently only
30 lines and none of this rendering logic has coverage today):

- `render_report` with one burn alert and one conserve alert (construct both directly
  as `UseOrLoseAlert` objects, don't run the full pipeline) → output contains a
  `"CONSERVE"` section before `"THIS WEEK"`/etc., and the conserve alert does NOT
  appear in any burn bucket.
- A burn alert with `pace` set → its rendered line contains a `"pace"` fragment.
- `--json`/`to_dict()` output (from Step 9) round-trips through `render_report`'s
  sibling JSON path in `cli.py` without needing any change there — confirm by
  inspecting `cli.py`'s JSON payload construction, not by modifying it.

**Done when:** new tests pass, full suite green.

---

### Step 15 — Flip the default, update docs, full regression

**Files:** `src/ai/config.py` (confirm `"scoring_mode": "pace"` is the shipped default
from Step 11 — this step is mostly verification, not new code), `README.md`,
`docs/consumption-flexibility-plan.md` (add a short "superseded by pace-based scoring,
see below" pointer rather than rewriting it — it's still useful background).

1. Confirm `DEFAULT_CONFIG["analysis"]["scoring_mode"] == "pace"`.
2. Update `README.md`'s description of scoring/urgency to describe the three pace
   states (Burn / Conserve / On pace) and the shared-allotment behavior for Claude,
   replacing whatever it currently says about the multi-dimensional model (check
   current README content first — don't guess at what needs to change).
3. Document `scoring_mode: "legacy" | "multi_dim" | "pace"` and the new
   `analysis.pace.*` and `provider_overrides.<provider>.shared_allotment` keys in
   whatever config-reference doc/section already lists `analysis.*` keys (check
   `config/` directory and README for an existing example config file to update).
4. Run the **entire** test suite one final time (`.venv/bin/python -m pytest -q`),
   and additionally run the actual CLI against live data if any of the underlying
   tools (`cswap`, `codexbar`, `tokscale`) are available in this environment
   (`.venv/bin/python -m ai.cli` or however `pyproject.toml`'s entry point invokes it —
   check `pyproject.toml`), to eyeball that the Conserve section and burn buckets
   render sensibly on real data, not just fixtures. If none of those tools are
   available in this environment, skip the live run and note that in the step's
   completion message — do not fabricate fake tool output to force a live run.

**Done when:** full suite green, README/config docs updated, and (if possible) one
real `ai` invocation visually confirms the new report sections.

---

## Phase 3 — Remaining correctness bugs (major severity)

Each of these is independent; order here is roughly by how much they affect the numbers
a user actually sees.

### Step 16 — `gemini` provider override key is dead config

**File:** `src/ai/config.py` (the `provider_overrides` dict is keyed `"gemini"`);
`src/ai/analysis/use_or_lose.py`, `_classify_flexibility` and
`_compute_flexibility_profile` (both look up overrides by `provider.lower().replace(" ",
"-")`, which for this provider is `"antigravity"` post-canonicalization in
`runner.py::_canonical_provider` — the override never matches).

**Fix:** add an alias-resolution step wherever `provider_overrides` is looked up by
provider key (both `_classify_flexibility` and `_compute_flexibility_profile` do this
independently — fix both, or factor a shared `_override_key(provider)` helper that both
call). Use the same alias mapping `report.py::_consumption_line` already has for exactly
this purpose (`{"antigravity": "gemini", "opencode-go": "opencode"}`), reused rather
than redefined a third time — consider moving it to `ai.models` as a shared
`PROVIDER_CONFIG_ALIASES` constant so `report.py`, `use_or_lose.py`, and `_plan_meta`
(which already has its own copy too, per the earlier review) all read the same mapping
instead of maintaining three copies that can drift.

**Test:** an Antigravity/Gemini account with a 5h window → the `gemini.5h` override's
`flexibility` value is actually applied (assert `_classify_flexibility` or the resulting
`FlexibilityProfile` reflects the configured override, not the 0.5 default).

**Done when:** test passes, full suite green.

### Step 17 — Learned flexibility can leak from one provider into an unrelated one

**File:** `src/ai/analysis/history.py`, `merge_learned_flexibility` (~line 150).

**Bug:** when no exact `f"{provider}:{duration_kind}"` key exists, it falls back to
_any_ key ending in `f":{duration_kind}"`, in nondeterministic dict-iteration order.

**Fix:** remove the cross-provider fallback entirely — return `base_flex` unchanged
when there's no exact match for this provider. Cross-provider learning isn't a coherent
concept (a fast-burning Grok weekly says nothing about a Codex weekly); this fix is a
deletion, not an addition:

```python
def merge_learned_flexibility(base_flex, provider, duration_kind, learned):
    if not duration_kind or not learned:
        return base_flex
    key = f"{provider.lower().replace(' ', '-')}:{duration_kind}"
    learned_flex = learned.get(key)
    if learned_flex is None:
        return base_flex
    return 0.3 * learned_flex + 0.7 * base_flex
```

**Test:** learned dict containing only a `"grok:weekly"` entry, called with
`provider="codex", duration_kind="weekly"` → returns `base_flex` unchanged (today it
would incorrectly blend in Grok's rate).

**Done when:** test passes, full suite green.

### Step 18 — Burn-rate learning over-trusts short intervals and goes blind across resets

**File:** `src/ai/analysis/history.py`, `compute_learned_burn_rates` (the function from
Step 13 — if Phase 2 wasn't done first in this environment, target
`compute_learned_flexibility`'s inline loop directly at ~line 92–135 instead).

**Bug 1:** `time_delta_days = max(0.01, ...)` — a snapshot pair minutes apart, with any
usage delta, extrapolates to an enormous %/day rate with equal weight to a
day-apart pair.

**Fix 1:** weight each `(burn_rate, weight)` pair by `time_delta_days` itself (or by
`min(time_delta_days, 1.0)`, whichever reads more naturally against the existing
weighted-average code) instead of a flat `1.0`, so a two-minute-apart pair barely
influences the average.

**Bug 2:** the exact-`resets_at`-string-match requirement plus the `consumed <= 0`
skip mean any snapshot pair straddling a window reset (previous snapshot's window
hasn't reset yet, current one has) contributes nothing to the learned rate — the
full-cycle case is systematically excluded.

**Fix 2:** when `current_remaining > prev_remaining` (a reset clearly happened between
snapshots) and both timestamps and window durations are known, don't skip — instead
treat the _previous_ cycle's implied consumption as `prev_remaining` (100% minus what
was left) consumed over the portion of that cycle between the previous snapshot and its
own `resets_at`, and include that as a data point. If this reconstruction feels too
speculative to implement confidently, the minimally-safe version of Fix 2 is: at least
stop silently discarding these pairs with zero signal — log/count how often this
happens (e.g. return it as a diagnostic count alongside the learned rates) so the gap
is visible rather than invisible. Prefer the fuller reconstruction if straightforward;
fall back to the visibility-only version if not.

**Test:** two snapshots 3 minutes apart with a usage delta → contributes negligible
weight to the final average rate, verified by comparing against a control average
computed without that pair. A snapshot pair straddling a reset → contributes some
signal to the learned rate rather than being silently dropped (or, for the
visibility-only fallback, the discard is now counted/reported somewhere testable).

**Done when:** tests pass, full suite green.

### Step 19 — Chronic-waste summary double-counts the same billing cycle

**File:** `src/ai/analysis/history.py`, `chronic_waste_summary` (~line 170).

**Bug:** averages `remaining_percent` across up to 7 recent snapshots per
`provider:label` key without checking whether those snapshots span the same
cycle — several snapshots taken shortly after a reset (all near 100% remaining) read
as "consistently underused" with no actual chronic pattern.

**Fix:** when bucketing samples for a given `key`, only keep at most one sample per
distinct `resets_at` value (the most recent sample seen for that `resets_at`); require
`len(distinct_resets_at_seen) >= 2` (not `len(samples) >= 2`) before reporting a key.
This requires threading `resets_at` alongside each `prev_remaining` sample into the
`wasted[key]["samples"]` structure (currently just a flat list of floats) — change it
to a list of `(resets_at, remaining)` pairs, dedupe by `resets_at` before averaging.

**Test:** 5 snapshots, all sharing the same `resets_at` (same cycle, just polled
repeatedly) → the window does NOT appear in `chronic_waste_summary`'s output (today it
would, incorrectly). 3 snapshots spanning 3 distinct `resets_at` values, all showing
high remaining → the window DOES appear (genuine chronic pattern, correctly detected).

**Done when:** tests pass, full suite green.

### Step 20 — The most expensive configured plan dilutes every cheaper plan's urgency

**File:** `src/ai/analysis/use_or_lose.py`, `_score_multi_dimension` (~line 506).

**Bug:** `max_plan_price` is the maximum `monthly_price` across every plan in the
entire config, used to normalize `value_urgency` for all of them — adding one
expensive plan silently suppresses every cheaper plan's score.

**Fix:** normalize each window's `value_urgency` against _that window's own plan's_
`monthly_price` (already available as `plan_price` a few lines below in the same
function), not a global maximum. Keep a sane floor (e.g. `max(plan_price, 20.0)`) for
windows with no configured price at all, rather than a global cross-plan maximum:

```python
plan_price_for_norm = float(monthly_price) if monthly_price else 20.0
value_urgency = 0.0
if profile.value_at_risk_usd is not None and plan_price_for_norm > 0:
    value_urgency = max(0.0, min(100.0, (profile.value_at_risk_usd / plan_price_for_norm) * 100))
```

(Remove the `max_plan_price` computation loop over `plans_cfg.values()` entirely — it's
no longer needed.) Note this function currently doesn't receive `monthly_price`
directly as a parameter — check its call site in `analyze_use_or_lose` and thread it
through, or derive an equivalent value from `profile` if one is already available there.

**Test:** two providers, a $10 plan and a $30 plan, each with a window at the same
`remaining`/`value_at_risk_usd` relative to its own price → their `value_urgency`
scores are now equal (today, the $10 plan's score would be roughly 3x lower purely
because the $30 plan exists in config).

**Done when:** test passes, full suite green.

### Step 21 — Colliding fallback slot labels silently drop real quota windows

**File:** `src/ai/collectors/codexbar.py`, `_slot_label` (~line 334) and the
primary/secondary/tertiary loop in `_from_row` (~line 222).

**Bug:** when CodexBar doesn't name a provider's slots, `_slot_label` falls back to a
duration-bucket label like `"{provider} 5-hour quota"`. Two same-duration slots on one
account produce the identical label string; the de-dup check a few lines down
(`window and not any(window.same_measurement(extra) for extra in extra_windows)`, and
implicitly any later logic keying on label) can end up keeping only one of them.

**Fix:** when the fallback path is used (no entry in `_SLOT_LABELS` for this provider),
make the label include the slot index unconditionally so same-duration slots never
collide: change the fallback branch's return value to always include `index` even in
the "kind" branches, e.g. `f"{provider_name} 5-hour quota ({index})"` for all three
`kind == "5h"/"weekly"/"monthly"` branches, not just the final unnamed-kind fallback
which already includes `index`.

**Test:** a CodexBar row with `primary` and `secondary` both being unnamed 5h-duration
blocks with different `usedPercent` values → both windows survive into the final
`windows` list (assert `len(windows) == 2`, not deduped down to 1).

**Done when:** test passes, full suite green.

### Step 22 — A bare dollar sign in a reset description can misclassify billing kind

**File:** `src/ai/collectors/codexbar.py`, the balance-detection loop in `_from_row`
(~line 279) and `_billing_kind` (~line 355).

**Bug:** the DeepSeek-style regex scan for a `$` figure in any window's reset
description can flip an `UNKNOWN`-billing account to `PREPAID_BALANCE` on a false
match — and `analyze_use_or_lose` treats `PREPAID_BALANCE` accounts as non-expiring,
removing them from use-or-lose consideration entirely. A subscription window that
happens to have a `$` somewhere in its human-readable reset description (e.g. quoting a
price) would be silently pulled out of the report.

**Fix:** only let this heuristic change `billing` from `UNKNOWN` to `PREPAID_BALANCE`
when there are **no** `resets_at`-bearing windows on the account at all (mirroring the
precedence `_billing_kind` itself already documents: `windows and any(window.resets_at
is not None for window in windows) -> SUBSCRIPTION_WINDOW` should always win). Add that
same check as a guard immediately before the `if billing == BillingKind.UNKNOWN:
billing = BillingKind.PREPAID_BALANCE` line in `_from_row`.

**Test:** an account with one window that has both a `resets_at` value AND a `$` figure
in its `resetDescription` → `billing_kind` stays whatever it was (not flipped to
`PREPAID_BALANCE`), and the window still appears in `analyze_use_or_lose`'s output.

**Done when:** test passes, full suite green.

### Step 23 — A matched-but-errored cswap row can never produce its intended specific warning

**File:** `src/ai/collectors/runner.py`, `_claude_cross_checks` (~line 164).

**Bug:** the branch meant to say "cswap failed for this account, and CodexBar's data is
for a different/unidentified account — don't substitute it" is unreachable as ordered;
an email match against CodexBar takes precedence first and always produces the generic
"reporting inconsistency" message instead.

**Fix:** reorder the checks so the specific case is checked first: if `cswap_row.error`
is set, check the email-match branch's condition (`cswap_row.error and live_codexbar`)
_before_ attempting `_compare_live_rows` — i.e. don't call `_compare_live_rows` on a
`cswap_row` that has `.error` set at all; route errored cswap rows straight to the
existing `if cswap_row.error and live_codexbar:` message, matched rows or not.

**Test:** a cswap row with `.error` set, whose account email happens to match a live
CodexBar row → the specific "cswap could not read canonical usage... do not
substitute" message is produced (today: the generic inconsistency message is produced
instead).

**Done when:** test passes, full suite green.

---

## Phase 4 — Minor fixes, grouped by file

Each of these is small; group them by file so one agent session can knock out a whole
file's minor issues together and still end in a clean, testable state.

### Step 24 — `config.py`: two independent small fixes

1. **Silent bad `--config` path**: `load_config` currently only checks
   `candidate.is_file()` and falls through to defaults with no warning if an explicitly
   passed `path` doesn't exist. Fix: when `path` is explicitly provided (not the default
   XDG lookup) and it does not resolve to a file, raise `SystemExit(f"Config file not
found: {candidate}")` rather than silently using defaults — being explicit about a
   config path and having it ignored is worse than a hard failure here.
2. **Dead `'daily'` flexibility default**: `consumption_flexibility_defaults` has a
   `"daily"` key that `classify_window_minutes` can never produce (only `"5h"`,
   `"weekly"`, `"monthly"`, or `None`). Either remove the dead key, or (better, since
   the intent — a coarser bucket than 5h but finer than weekly — is reasonable) add a
   real `"daily"` bucket to `classify_window_minutes` in `models.py` for windows between
   `WINDOW_5H_MAX_MINUTES` and roughly 1440 minutes, and thread it through anywhere
   `classify_window_minutes` return values are matched against literal `"5h"`/`"weekly"`/
   `"monthly"` strings (search all call sites first — this touches more than one file;
   if that search turns up more than 2-3 call sites, prefer the "just remove the dead
   key" fix instead to keep this step's blast radius small).

**Test:** `load_config("/nonexistent/path.yaml")` raises `SystemExit`. If the `"daily"`
bucket is added: a window with `window_minutes` between the 5h and weekly boundaries
classifies as `"daily"` and picks up the `0.1` flexibility default.

**Done when:** tests pass, full suite green.

### Step 25 — `history.py`: snapshot save collisions and permission ordering

**File:** `src/ai/analysis/history.py`, `save_snapshot` (~line 23).

1. Filename is second-granularity (`%Y-%m-%dT%H%M%SZ`) — two runs within the same
   second silently overwrite each other. Fix: append a short disambiguator if a file
   with that timestamp already exists (e.g. loop appending `-1`, `-2`, ... before
   `.json` until the path doesn't exist), or switch to microsecond precision in the
   format string if acceptable (check whether anything else parses this filename
   format elsewhere in the codebase before changing it — `load_recent_snapshots` sorts
   by filename, so format changes must stay lexically sortable).
2. The file is briefly created at the process umask before the explicit
   `filepath.chmod(0o600)` a line later. Fix: open the file with restrictive
   permissions from the start instead of chmod-ing after the fact —
   `os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)` wrapped in
   `os.fdopen(..., "w")`, replacing the `filepath.write_text(...)` call.

**Test:** two `save_snapshot` calls with timestamps forced to collide (monkeypatch
`utcnow` or construct two `Snapshot`s with the same `collected_at`) produce two
distinct files, both readable back. (Permission-ordering fix has no meaningful
automated test beyond confirming the file's final mode is `0o600` — that assertion
likely already exists; leave it, just confirm it still passes.)

**Done when:** tests pass, full suite green.

### Step 26 — `base.py`: remaining `run_json` hardening

(Step 1 already fixed the "wrong fragment" bug in this function — this step covers two
smaller, separate issues in the same function.)

1. Non-zero exit codes are currently ignored whenever stdout happens to parse as JSON —
   a tool that exits 1 but prints a JSON error object gets that object treated as a
   real payload. Fix: when `proc.returncode != 0`, only accept the parsed JSON if it's
   the caller's expected shape is ambiguous at this layer (this function is generic
   across all collectors) — the safest general fix is to still parse and return the
   JSON (some tools legitimately exit non-zero with a structured error payload the
   caller wants to inspect), but additionally raise if `proc.returncode != 0` AND the
   parsed JSON looks empty/trivial (e.g. `{}`, `[]`, or a bare error-shaped dict with no
   usable fields) — check how each collector currently handles error payloads
   (`codexbar.py`'s `_from_row` already handles `row.get("error")`) before deciding
   whether this needs a code change here or is already handled downstream. If every
   caller already checks for an `error` key in what's returned, this finding may not
   need a fix in `base.py` at all — verify before changing anything, and if no change
   is needed, say so instead of forcing an edit.
2. The candidate cap (already removed as part of Step 1's fix) — confirm Step 1 already
   resolved this; no further action needed here.

**Test:** only add a test if part 1 above results in an actual code change; otherwise
this step's "test" is confirming (and briefly documenting in the commit message) that
existing downstream error handling already covers the non-zero-exit-code case.

**Done when:** full suite green; either a fix + test landed, or a documented
no-change-needed conclusion.

### Step 27 — `tokscale.py`: window-inference cleanups

**File:** `src/ai/collectors/tokscale.py`, `_infer_window_minutes` (~line 101),
`_from_row` (~line 34).

1. Use nominal per-kind minutes (`300`/`10080`/`43800`, matching `WINDOW_NOMINAL_MINUTES`
   from Step 9 if Phase 2 has landed in this environment, or hardcode the same three
   numbers directly if not) instead of the bucket-maximum constants
   (`WINDOW_5H_MAX_MINUTES` etc.) currently used — the bucket maximums (360/10080/44640)
   overstate a real 300-minute window's duration by ~20%, which feeds directly into
   value-at-risk math.
2. Guard the `"chat"/"completions"/"premium" -> monthly"` rule to only apply when
   `provider == "copilot"` (currently applies to any provider using those label
   strings).
3. Remove the `'"7" in display_label.lower()'` weekly heuristic entirely — it
   false-positives on any label or provider name containing the digit 7 and is
   redundant with the `"week" in key` check immediately before it.

**Test:** a non-Copilot provider with a `"premium"` label does NOT get classified as
monthly. A label like `"Grok7 usage"` (contrived, but exercises the removed heuristic)
does NOT get classified as weekly purely from containing "7". A `"5h"` label now infers
`window_minutes == 300`, not `360`.

**Done when:** tests pass, full suite green.

### Step 28 — `use_or_lose.py` + `models.py`: two independent small math fixes

1. **`burn_minutes` ignores remaining fraction** (`_burn_estimate_text` /
   `_compute_flexibility_profile`, ~line 162): `burn_minutes = capacity /
max(rate, 0.001)` always assumes the _full_ capacity, regardless of how much
   `remaining` actually is — the printed "~1.5h at typical pace" text is identical
   whether 10% or 90% remains. Note: if Phase 2's Step 7 already introduced
   `burn_minutes_for_remaining` for the `cycles_needed`/`earliest_start_calendar`
   computation, reuse that exact value for the burn-estimate text too, rather than
   computing a second, inconsistent scaled figure. Thread `burn_minutes_for_remaining`
   (or equivalent) into `_burn_estimate_text` instead of the unscaled `burn_minutes`.
2. **`same_measurement` ignores `remaining_percent`** (`models.py`, ~line 163): two
   windows reported only via `remaining_percent` (not `used_percent`) at genuinely
   different values compare as equal for de-dup purposes because the comparison only
   checks `used_percent`. Fix: also compare `remaining_percent` in the equality check.

**Test:** burn-estimate text for a window at 20% remaining is shorter/smaller than for
the same window at 90% remaining (currently identical). Two `QuotaWindow`s with
`used_percent=None`, `remaining_percent=50.0` and `remaining_percent=80.0` respectively
→ `same_measurement` returns `False` (currently `True`, since both have `used_percent
is None` and matching `window_minutes`/`resets_at`).

**Done when:** tests pass, full suite green.

### Step 29 — `codexbar.py` + `cli.py`: remaining minors

1. **`codexbar.py`**, `_query_provider` (~line 177): the per-provider timeout (90s) is
   tighter than the old bundled-call budget (180s) for a provider that genuinely needs
   more time. Fix: raise the per-provider timeout to something closer to the bundled
   budget's intent — e.g. `120.0` as a middle ground — or make it configurable via
   `collectors.codexbar.provider_timeout` in config, defaulting to `120.0`. Prefer the
   configurable version if it's not much more code; otherwise just raise the constant.
2. **`codexbar.py`**, `_normalize_providers` (~line 100): an unrecognized
   `--providers` value silently falls through to `[None]` (the full bundled "enabled
   providers" call) rather than surfacing the typo. This is a UX nit more than a
   correctness bug — lowest priority in this step; only fix if the above two are
   already done and time remains. If fixed: raise a clear `CollectorError` or
   `SystemExit` naming the unrecognized value instead of silently falling through, but
   only for values that aren't `"enabled"/"configured"/"default"/"all"/"both"` and
   don't parse as a comma-separated list of anything (i.e. don't break the legitimate
   comma-separated-list path).
3. **`codexbar.py`**, `_from_row` (~line 226): balance-blob detection
   (`has_named_balance_blob`) currently skips primary/secondary/tertiary slot windows
   for any `PREPAID_HINTS` provider whenever a named balance blob is present, even if
   that provider also has a real subscription window. Fix: only skip a given slot when
   there's a genuine overlap with an extra/named window (use the existing
   `same_measurement`-based de-dup already used for `extra_windows`, rather than
   blanket-skipping all three slots whenever any balance blob exists).
4. **`cli.py`**, `main()` (~line 191): exit code is always `0` even when every
   collector fails. Fix: track whether `snapshot.collector_errors` is non-empty AND
   `snapshot.accounts` is empty (total failure) at the end of `main()`, and `return 1`
   in that case instead of `0`.
5. **`cli.py`**, `_apply_cli_overrides` (~line 194): `collectors.setdefault(name,
{})["enabled"] = False` will raise `TypeError` if a user's config has that
   collector's value as a plain boolean (`collectors: {tokscale: true}` — a form
   `runner.py::_enabled` explicitly supports) rather than a dict, because
   `setdefault` returns the existing boolean, not a dict, and subscripting a `bool`
   fails. Fix: `collectors[name] = {"enabled": False}` unconditionally instead of
   `setdefault(...)[...]  = False`, for all three `--no-*` flags.

**Test:** one test per numbered fix above, each independent. For #4: run the full CLI
path (or call `main()` directly) with all three collectors forced to fail → exit code
`1`. For #5: a config with `collectors: {tokscale: true}` (boolean form) plus
`--no-tokscale` on the CLI → no `TypeError`, and tokscale ends up disabled.

**Done when:** tests pass, full suite green.

---

## Phase 5 — Nits + test-coverage gaps

### Step 30 — Two nits

1. **`cswap.py`**, `_account_from_item` (~line 51): `active = bool(item.get("active"))
or number == active_number` — when both `number` and `active_number` are `None`,
   `None == None` is `True`, marking every slot with a missing number as "active." Fix:
   also require `number is not None` in that comparison.
2. **`tokscale.py`**, `_infer_window_minutes` (~line 105, or wherever Step 27 left it):
   the `key in ("session", "5h", "5-hour", "5 hour")` membership check is dead code
   given the substring checks right beside it; and `"5h" in key` matches `"15h"`/`"45h"`
   as unintended substrings. Fix: remove the dead membership tuple, and anchor the
   substring check with a word boundary or exact-match instead (e.g. check
   `key.strip() in ("5h", "session")` or a regex with `\b` boundaries) so `"15h"` no
   longer false-matches.

**Test:** cswap item with no `number` field and no `activeAccountNumber` in the parent
payload → `active == False`. A tokscale label like `"15h window"` (contrived) does not
classify as the 5h bucket.

**Done when:** tests pass, full suite green.

### Step 31 — Close the test-coverage gaps the review found

1. **`tests/test_tokscale_parse.py`** (new file — this collector currently has zero
   test coverage, despite the uncommitted diff adding nontrivial `window_minutes`
   inference logic to it). Cover, at minimum: `_from_row` on a realistic multi-metric
   payload (mirror the shape seen in a real `tokscale usage --json` response — Claude
   with Session/Weekly metrics, Copilot with Chat/Completions/Premium, a provider with
   a `credit_status` block); `_infer_window_minutes` for every branch (5h, weekly,
   monthly-via-copilot-labels, unclassifiable → `None`); date-only `resets_at` values
   like `"2026-08-01"` (no time component) parse correctly via `parse_dt`.
2. **`tests/test_report.py`** — if Step 14 (Phase 2) already expanded this file
   substantially, this step is largely already done; otherwise, at minimum add
   coverage for `_action_buckets`'s bucket-assignment logic (THIS WEEK / THIS WEEKEND /
   LATER THIS MONTH / THROTTLED) and the `_throttled_waste_line` math fixed in Step 5,
   since neither had any test before this plan and both hid real bugs.

**Done when:** new test files/cases pass, full suite green, and (informally) confirm
via `pytest --collect-only` or coverage tooling if available that `tokscale.py` and
`report.py`'s rendering functions are no longer at zero coverage.

---

## Phase 6 — tokscale true per-provider containment (exploratory)

### Step 32 — Investigate whether tokscale can be queried per-provider today

This is exploratory, not a guaranteed code change — the outcome determines what (if
anything) gets implemented.

1. Run `tokscale codex --help`, `tokscale cursor --help`, `tokscale antigravity
--help`, `tokscale trae --help`, `tokscale warp --help` (these subcommands exist per
   `tokscale --help`'s top-level command list) and check whether any of them expose the
   same subscription-usage data that `tokscale usage --json` aggregates, scoped to just
   that one integration.
2. **If yes** for at least the providers currently covered by `tokscale usage`
   (Claude, Codex, Copilot, Grok — check which subcommand, if any, corresponds to
   each): implement a `codexbar.py`-style fan-out in `tokscale.py` — a per-provider
   subcommand call, run concurrently via `ThreadPoolExecutor` (mirror
   `codexbar.py::_query_providers`), each with its own timeout and error isolation,
   with the existing single bundled `tokscale usage --json` call kept as a fallback
   for any provider without a matching subcommand (or for the whole source, if this
   turns out to be only partially supported).
3. **If no** clean per-provider path exists: do not force a fan-out that doesn't
   really isolate anything. Instead, write up the finding (which subcommands exist,
   what each actually returns, why none substitute for per-provider `usage` data) as
   a short note appended to this plan document or a new
   `docs/tokscale-per-provider-investigation.md`, so a future upstream feature
   request to the tokscale project has a concrete, accurate ask instead of a vague
   one. Leave the Phase-1 Step-2 mitigation (raised timeout, cross-check-only
   posture) as the standing solution.

**Done when:** either a working, tested fan-out implementation lands (repeat the
testing rigor of Step 2/Phase-1 collector tests: per-provider isolation, timeout
handling, fallback to the bundled call), or a written investigation note exists
explaining why it isn't currently possible.

---

## Phase 7 — Deferred / optional (after Steps 1–32)

Park items that are useful but **not** required for the review-derived correctness
pass. Do these only after Phase 6, or when the operator explicitly prioritizes them.

Background for Steps 33–34: [`cswap-reliability.md`](cswap-reliability.md) and
[`claude-local-usage.md`](claude-local-usage.md) (cswap multi-account reliability
already landed: cache hydrate, countdown recompute, CodexBar/tokscale fallback).

### Step 33 — Upstream: richer cswap JSON for display-grade usage (optional)

**Status (2026-07-30):** **done**. [claude-swap#170](https://github.com/realiti4/claude-swap/pull/170)
merged and shipped in cswap 0.24.0. aiuse prefers its official fields while
remaining compatible with older cswap JSON.

**Historical upstream request (2026-07-23):**

- Issue/PR: [realiti4/claude-swap#170](https://github.com/realiti4/claude-swap/issues/170)
  — _feat(json): expose display-grade last-good usage_ (merged)
- Branch: `possibilities:feat/display-last-good-usage`
- Contract proposed: when decision-grade data is too old, keep
  `usageStatus: unavailable` + `usage: null`, and add additive
  `lastGoodUsage` / `lastGoodFetchedAt` / `lastGoodAgeSeconds` for UIs.

**Why (our side):** `ai` currently reads `~/.claude-swap-backup/cache/usage.json`
when JSON marks a slot unavailable. Prefer official last-good fields when #170
lands; keep cache hydration as fallback for older cswap.

**Completed work:**

1. Prefer `lastGoodUsage` (+ age fields) in `collectors/cswap.py`.
2. Keep cache-file hydration for absent/malformed official fields and older cswap versions.
3. Document that cswap 0.24.0 enables official display-grade JSON; it is not a minimum requirement.

**Do not** open a duplicate issue — track #170.

### Step 34 — Surface Claude usage-credits / pay-as-you-go more fully (optional)

**Status:** done. cswap `usage.spend` → `UsageCredits` on `AccountUsage`
(`usage_credits` in JSON); pretty report shows a dedicated **usage credits**
block. Does not reclassify accounts as prepaid (subscription windows still
drive use-or-lose). Step 35 (ccusage local burn) remains deferred.

**Why:** Website Settings → Usage shows spend, balance, and monthly credit
limits. cswap sometimes carries a `spend` block (notes-only previously).

### Step 35 — Optional local burn section via ccusage / stats-cache (optional)

**Why:** Token burn on this machine is interesting for activity, but it is **not**
subscription 5h/7d %. Do not use it for use-or-lose alert authority.

**Work (only if product wants activity metrics):**

1. Shell out to `ccusage claude daily --json` (Homebrew/bun/npm all fine) **or**
   parse `~/.claude/stats-cache.json` in pure Python.
2. Render a clearly labeled “local Claude Code burn (this machine)” note or
   section — never as `used_percent` on plan windows.
3. Multi-account is out of scope for this path (active `~/.claude` only).

**Done when:** optional collector behind a flag or config default-off, with tests
using fixtures (no live dependency in CI).
