# Pace and allotment formulas (normative v0.1.0)

Implementations should match these numbered rules. `aiuse` dogfoods them via
`src/aiuse/analysis/pace.py` and golden fixtures.

## Window duration (W1)

Classify `window_minutes` into `5h` (≤360), `weekly` (≤10080), `monthly` (≤44640).
If null, duration class is unknown and pace confidence is `low`.

## Remaining (R1)

If only `used_percent` is set: `remaining_percent = max(0, 100 - used_percent)`.

## Shared allotment (S1–S3)

- **S1:** Among windows with known remaining%, the longest `window_minutes`
  (or nominal for its class) is the **governing** window; children are not
  independently ranked when shared allotment is enabled.
- **S2:** On duration ties, prefer a label containing `included`, else list order.
- **S3:** If pace cannot be computed for the governing window, fall back to
  independent scoring (do not invent urgency).

## Pace (P1–P8)

Inputs: remaining, resets_at, window_minutes (or nominal), now; optional
learned_rate_per_day + sample count.

- **P1** `used_fraction = (100 - remaining_percent) / 100`
- **P2** `d_days = window_minutes / 1440`
- **P3** `t_left_days = max(0, (resets_at - now) / 1 day)`
- **P4** `elapsed = clamp(1 - t_left_days / d_days, 0, 1)`
- **P5** `r_now = used_fraction / (max(elapsed, e_min) * d_days)` with `e_min` default 0.05
- **P6** Optional blend: `λ = n/(n+2)`, `r_hat = (1-λ)·r_now + λ·learned`
- **P7** `projected_used = min(1, used_fraction + r_hat * t_left_days)`, `projected_waste = 1 - projected_used`
- **P8** `projected_exhaust_at = now + (1 - used_fraction) / r_hat` days if `r_hat > ε`

### Classify (priority order)

1. `unknown` — missing waste and exhaust projections
2. `on_pace` — `elapsed < min_elapsed_fraction` and no learned rate
3. `conserve` — `projected_exhaust_at < resets_at - conserve_min_lead`
4. `burn` — `projected_waste >= waste_alert_fraction`
5. `on_pace` — otherwise

Default parameters: see `policy/pace_defaults.yaml`.

## Overage / soft ceiling (O1)

- **O1:** `has_overage` is true when `AccountUsage.usage_credits` is present
  (a real, collected overage/extra-usage wallet), or when
  `provider_overrides.<provider>.overage_state == "enabled"` (a manually set
  config override, for providers where no collector can populate
  `usage_credits` — e.g. OpenCode Go's Zen-balance fallback). Default/`unknown`/
  `disabled` never assert overage. `has_overage` **qualifies**, never
  suppresses, a `conserve`/`burn` verdict: `true` means the real risk is
  unplanned $ spend (a soft ceiling), not lockout (a hard ceiling).

## Prepaid (P-prepaid)

- **PP1:** `billing_kind` in `{prepaid_balance, payg_api}` never yields burn/conserve
  solely from remaining%. Display band is `n_a`.
- **PP2:** Optional inventory INFO (`alert_kind: prepaid`) is not suggestion-eligible.

## Bands (B1–B2)

- **B1:** Sort by band lane first (`error` → `empty` → `n_a` → `slow` → `mid` → `use`), then score.
- **B2:** Never promote prepaid into `use`/`slow` from remaining% alone.

## Suggestion (G1)

- **G1:** Only `burn` alerts with non-info urgency are suggestion-eligible.
  Conserve never wins. Null suggestion = nothing urgent.

## Health probe (H1)

- **H1:** “Up” uses `health_path` / `probe_url`; payload parse uses the quota path.
  Root 404 is not collector death if the payload path returns 200.
