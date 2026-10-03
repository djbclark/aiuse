---
schema_version: 1
handoff_id: sc11
parent_handoff_ids: [aa64]
lineage: deterministic
chain: [aiuse-1qo, aiuse-e9d, aiuse-oja, aiuse-al9, a17f, f1a4, aa64, sc11]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 527c24bd3a344f31c13c25012831cd82af6347fd
created_at: 2026-10-03T19:10:00-04:00
writer: ZCode
---

# Handoff — schema 1.1 self-describing output + serve port 28787

Two operator-commissioned changes, both from the 2026-10-03 orchestration
incident (see docs/json-contract.md "How to read this" for the three
misreads that motivated them). Supersedes
[aa64](HANDOFF_zcode-aa64_grok-reset-310-close_2026-09-27_aa64.md) as the
newest resume point.

## What shipped (commits 11d67f4, 527c24b)

1. **Serve port**: default moved 8787 -> **28787** (8787 is collie-bridge,
   registered in ~/ops/site-djbclark/registry/ports.yml, which now also
   claims 28787 as default-claim aiuse-serve). run_serve probes the port
   first and fails loudly on a non-aiuse responder; /v1/health identity
   body already existed and is now documented as the client check.
2. **Schema 1.1 (misread-proofing)**: window `state`/`headroom_percent`/
   `pool_family`/`models_hint`; account `usable_now`/`binding_window`/
   `available_at`/`age_seconds` (+ `collected_at` from CodexBar
   usage.updatedAt); top-level `summary_lines`/`semantics`/`agent_notes`/
   `age_seconds`/`fresh`; cache file enriched at rest and mirrors the
   envelope under `snapshot` (flat path intact); `aiuse --available
[--json] [--live]` (cache-default, exit 3 = nothing usable);
   `aiuse note-exhausted` advisory overrides in
   ~/.cache/aiuse/agent-notes; human surfaces print used/left pairs
   (`100u/0l` cells, red EXHAUSTED row marks) — a guard test fails if a
   bare percentage ever returns. Config: `analysis.fresh_threshold_seconds`
   (default 1500).

Validation: 719 tests green, `just ci` green (ruff/mypy/prettier/
markdownlint/bandit/semgrep). Live-smoked `--available` against the real
cache (antigravity gemini listed, claude_gpt correctly absent) and
note-exhausted write/apply/remove round-trip.

## Open follow-ups (operator)

- Tell the operator when convenient: `~/ops/site-private/bin/aiuse-pools`
  can be replaced by `aiuse --available` (exit 3 = nothing usable; it also
  honours agent notes). model-routing "Reading quota numbers" and bigteam
  Step 1 can shrink to "use `aiuse --available`".
- No release cut (per policy, releases only on explicit ask); next release
  would ship 3.1.4 with these two commits.
- Not done from the brief: nothing — all 8 build items + docs + tests landed.
  Adaptations are recorded in docs/json-contract.md and the commit message.
