# Charm Hyper quota

Charm Hyper is a monthly plan that acts as an aggregator/backend for terminal AI tools like Crush. It is distinct from Z.ai (which only provides GLM).

## What `aiuse` shows

- **Service:** Renders as `hyper/crush` (canonical provider id is `hyper`).
- **Scopes (Independent Pools):** Because Charm Hyper provides multiple models, `aiuse` dynamically breaks them out into independent pools based on window labels via `analysis/pace.py`:
  - `glm` (for GLM usage)
  - `deepseek` (for DeepSeek usage)
  - `—` (for any other models acting on the base quota)

## Configuration and Identity

Do not merge `hyper` and `zai`. They are separate services that a user might subscribe to independently, even if both happen to be used through the same Crush UI.

- `hyper` is explicitly mapped in `PROVIDER_DISPLAY_NAMES` to `"hyper/crush"`.
- `zai` is explicitly mapped in `PROVIDER_DISPLAY_NAMES` to `"zai/crush"`.

The pool splitting logic (`independent_pool_key`) operates independently of the provider, so it correctly isolates `glm` and `deepseek` quotas whether they originate from `hyper`, `zai`, or any future aggregator service.
