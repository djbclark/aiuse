# Charm Hyper quota

Charm Hyper is a monthly plan that acts as an aggregator/backend for terminal
AI tools like Crush. It is distinct from Z.ai (which only provides GLM).

## What `aiuse` shows today

- **Service:** renders as `hyper/crush` (canonical provider id is `hyper`).
- **Data:** none yet. `hyper` is only a display-name mapping; there is no
  Hyper collector, so no Hyper quota rows appear in output.

## Planned shape (not implemented)

Hyper exposes two usage meters: GLM models, and all other models. They are
metered separately. When a collector exists, split them into independent
pools by window label via `independent_pool_key` in `analysis/pace.py`, the
same way Gemini and Claude/GPT are split for Antigravity.

Add the pool keys only once Hyper's real window labels are known. Speculative
`glm` and `deepseek` keys were added and then removed (2026-09-21): no account
carried both classes, `zai` is GLM-only, and DeepSeek's direct API is its own
prepaid provider (`deepseek`).

## Configuration and identity

Do not merge `hyper` and `zai`. They are separate services that a user might
subscribe to independently, even if both are used through the same Crush UI.

- `hyper` is mapped in `PROVIDER_DISPLAY_NAMES` to `"hyper/crush"`.
- `zai` is mapped in `PROVIDER_DISPLAY_NAMES` to `"zai/crush"`.
- `deepseek` (direct pay-as-you-go API) is a separate provider, not part of
  either plan.
