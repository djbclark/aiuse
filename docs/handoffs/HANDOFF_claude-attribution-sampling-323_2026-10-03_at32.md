---
schema_version: 1
handoff_id: at32
parent_handoff_ids: [sc11]
lineage: deterministic
chain:
  [aiuse-1qo, aiuse-e9d, aiuse-oja, aiuse-al9, a17f, f1a4, aa64, sc11, at32]
repo: aiuse
workspace: aiuse
branch: main
head_sha: bb13e59b17b3d1d00485b858eef17fcc892796ad
created_at: 2026-10-03T21:30:00-04:00
writer: Claude Code
---

# Handoff — attribution, adaptive sampling, watch on the sampler (3.2.0 → 3.2.3)

Operator question that started it: ClinePass lost a month of quota in two
sprints and nothing recorded which client spent it. Epic `aiuse-8b7` (closed).
Supersedes [sc11](HANDOFF_zcode-schema11-selfdescribing_2026-10-03_sc11.md) as
the newest resume point. `head_sha` is the commit before the 3.2.3 release
commits.

## What shipped

1. **3.2.0** — `ledger.py` (tokscale session-file ledger sampled with each
   snapshot; optional LiteLLM spend log via `psql`), `sampler.py`
   (`aiuse sample`: idle 60 min / active 15 min / burst 3 min, burst samples
   are partial and filed under `~/.cache/aiuse/samples/`), `attribute.py`
   (`aiuse attribute`). Guide: [`../attribution.md`](../attribution.md).
2. **3.2.1** — `aiuse watch` reuses a snapshot younger than its interval and
   records its own collections as samples; overlays newer burst samples; header
   shows now / data time / sampler previous and next run. `--available` text
   names the real binary (`cursor auto [cursor-agent]`).
3. **3.2.2** — watch footer centered; 12-hour local times (`format_clock`).
4. **3.2.3** — burst judged against a reading at least 9 minutes old (a single
   whole-point tick between close samples had read as a burst and kept the
   tier at 3 minutes during ordinary Claude use); Copilot's window no longer
   splits in `attribute` when one source omits its duration; `--available`
   reset times are 12-hour.

## Outside this repo

1. site-djbclark `roles/litellm`: per-client virtual keys
   (`litellm_client_keys`: hermes, open-webui, llm-health-watchdog), reconciled
   with `just litellm-apply --tags litellm_client_keys`.
2. site-djbclark `roles/site_agents`: the LaunchAgent runs `aiuse sample`
   every 180 s.
3. This operator's `~/.config/aiuse/config.toml` has `[attribution.litellm]`
   pointing at the proxy's spend database, with `grok-sub` mapped to `grok`.
4. Finding recorded in site-private memory
   `project_clinepass_burn_attribution_2026-10-03.md`.

## Known limits

1. The session-file ledger starts 2026-10-03; earlier ranges have quota burn
   and LiteLLM rows only.
2. Crush records message counts in tokscale but no token counts.
3. Clients that reach a vendor directly (not through the proxy) appear only in
   the session-file ledger.
4. A router client's ambiguous upstreams (Hermes `moa`, `litellm_local`) are
   listed as unmapped; map them with `[attribution] provider_map`.
5. Another session's commit `a1afed1` swept the `sampling` / `attribution`
   config keys into 3.1.1 before the code that reads them existed.
