# SIPB LLMs (MIT)

MIT SIPB runs a free, self-hosted, OpenAI-compatible LLM endpoint based on Open WebUI + Ollama.

- **Base URL:** `https://llms-dev-1.mit.edu/api`
- **Quota:** Unlimited. (Modeled honestly as `UNKNOWN` billing kind without fabricated windows).
- **Latency:** Shared GPU resources mean this server is often very slow. It is meant to be used sparingly / as a last resort.
- **Timeouts:** Due to the latency, the default collector timeout is 120 seconds.
- **Network:** The instance is often MITnet-only (requires MIT network or VPN to access).

## Identity

Provider identity: `sipb`
Display name: `SIPB LLMs (MIT)`
Collector: `sipb`

## Setup

This collector is **opt-in only** and disabled by default.
To enable it, set `[collectors.sipb] enabled = true` in your `aiuse/config.toml`.

The public unauthenticated endpoint is used for the up/down latency probe.
To list available models, provide an API key using the `MIT_SIPB_API_KEY` environment variable or SecretSpec.
The key starts with `sk-` followed by 32 hex chars.

## Other Instances

The sibling instance `https://llms-dev-0.mit.edu` is best-effort and out of scope for aiuse quota collection.
