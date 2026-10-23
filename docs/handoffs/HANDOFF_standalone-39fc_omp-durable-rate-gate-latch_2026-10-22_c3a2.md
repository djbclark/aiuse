---
schema_version: 1
handoff_id: c3a2
parent_handoff_ids: ["f0ee"]
lineage: deterministic
chain: [standalone-39fc]
repo: aiuse
workspace: main
branch: main
head_sha: 8b26f2a
created_at: 2026-10-22T20:40:00-0400
writer: zcode
---

# Handoff — omp rate gate is now a durable fail-closed latch (released 3.9.4)

## The Goal

The operator reported (third day running) that aiuse showed omp available
while Anthropic refuses every request until the 6am weekly reset, after four
prior fixes that each stopped working. Ask: coordinate with other models,
with advice from codex astra, and land a permanent fix.

## Where We Are

**Released as 3.9.4** (operator chose "release now" in the /loose walk): tag
`v3.9.4`, PyPI, GitHub release, Homebrew formula `8b26f2a`, installed Cellar
3.9.4, `brew test` passed. `main` at `8b26f2a`, pushed. The launchd sampler
runs 3.9.4 from its next invocation. A second fix rode the same release
(operator report, same evening): the watch `a` sweep no longer force-enables
deprecated collectors (`DEPRECATED_COLLECTORS` in config.py; e17ae08), so
caut/caam/openusage no longer print "not found on PATH" errors on every
sweep. The long-running watch pane (pid 88521, started 11:57am on 3.9.3
code) still needs an operator restart to pick either fix up. Full suite 1381
passed. Working-tree proof (this is the request from the operator's paste):

```text
$ AIUSE_SKIP_HOMEBREW_FORMULA=1 .venv/bin/aiuse gate status
aiuse gate: omp GATED until 2026-10-23T05:59:59.928000-04:00
(omp-429-anthropic, observed 2026-10-22T19:49:07.928000-04:00)   # exit 1
```

That is Anthropic's own retry-after (36652s) from the π 429 the operator
pasted (`req_011CgJArBbZXryGqwdN1eEUb`), parsed from `~/.omp/logs`, landing
exactly on the 10:00Z reset. Evidence store:
`~/.local/state/aiuse/rate-gates.json` (already seeded with that event).

## Diagnosis (what the five fixes never addressed)

Every aiuse surface was actually CORRECT this evening — matrix, `--available`
(omp omitted), `--for-chat`, `--json` (`usable_now:false`, `rate_gated_by`),
watch pane w2F:p2R, `--full`, and every stored snapshot since 15:16Z was
GATED. The π TUI still 429'd at 19:49 EDT because **π/omp never consults
aiuse** (0 references in the binary and configs). The recurring "omp shows
available" incidents are a structural class: the gate was re-derived at every
read/save from whatever inputs happened to be present and **failed open** —
omp row vanished (morning incident), Claude row missing/stale, cswap headroom
blink, and (found by astra) the `apply_credit_overrides_to_snapshot(...) or
apply_shared_rate_gate_to_snapshot(...)` short-circuit in watch.py that
skipped the gate whenever a credit override applied.

## The fix (d4f1b81, codex gpt-6-astra design review)

1. `src/aiuse/rate_gates.py` — evidence store at
   `~/.local/state/aiuse/rate-gates.json` (env `AIUSE_RATE_GATE_STORE`):
   records `{event_id, source, observed_at, resets_at, note}`, dedup by
   event_id, prune expired, atomic 0600 write, rejects spans >32d (never
   clamps), `clear_gate` writes a `cleared_through` tombstone so replaying an
   old log after a clear stays cleared while a new refusal re-gates.
   `active_gate()` = strongest unexpired evidence (+120s grace).
2. `src/aiuse/omp_rate_events.py` — parses `~/.omp/logs/omp.*.log` JSON
   lines (`cowork transport rejected`, `api.anthropic.com`, status 429,
   `retry-after` seconds; env `AIUSE_OMP_LOG_DIR`), deadline from event
   time, dedup by request-id, rotation/malformed tolerant.
3. Gate application (`selfdescribe.py`, both worlds) now records derived
   gates and consults the latch: `max(derived, latch)` wins; missing Claude
   data / headroom blinks no longer ungate recorded evidence (fail closed);
   latch-held gates render `window_label: "shared Claude window (latched)"`.
4. `save_snapshot` ingests omp-log evidence every collection; `aiuse gate
status [--json]` (exit 1 gated / 0 clear; ingests logs first) and
   `aiuse gate clear omp` are the machine/operator surfaces.
5. watch.py `or`-short-circuit fixed; conftest isolates store + log dir per
   test; 20 new tests pin every historical incident (vanished row, headroom
   blink, missing claude row, latch expiry, tombstone+replay, watch
   regression).

Astra's distilled advice (full transcript was /tmp/codex-astra-omp-gate.txt,
ephemeral; recorded in [`docs/omp-cloud-credits.md`](../omp-cloud-credits.md)
"Durable latch" section): persist scoped evidence records not one
irreversible deadline; compute deadlines from event time; tombstone clears;
reject implausible deadlines; keep the credits window `state` = dollars; exit
0/1/2 semantics for gate probes; end-state `prepare_snapshot` boundary
unifying all surfaces (future work, not attempted tonight).

## Key Decisions

1. Fail closed on recorded evidence, deliberately overriding the 2026-10-21
   "missing Claude data never gates" operator rule for evidence already
   seen: the rule's intent (don't corrupt omp reporting from a cswap outage)
   is preserved for never-before-seen states; a latch only holds what was
   positively observed, until its own reset.
2. No JSON schema bump: `rate_gated_by` shape unchanged; only `window_label`
   /`evidence` text can differ while latched.
3. Spend-path enforcement is NOT solved by aiuse alone: π/omp never reads
   aiuse. `aiuse gate status` exit code is the one-call surface for wrappers;
   the long-term fix is an omp upstream PR (consult eligibility before
   credential selection; emit refusals structurally). Operator rule: upstream
   PRs, not local patches.
4. bandit/semgrep rejected the SHA1 dedup digest (first commit attempt);
   switched to SHA256. Convention note: run the pre-commit chain mentally
   before committing crypto-adjacent calls.
5. Did not restart the watch, did not cut a release, did not spend omp, did
   not run `aiuse watch --all-providers`.

## Where We're Going

1. Operator: `just release 3.9.4` when ready — until then the latch exists
   only in the working tree (`.venv/bin/aiuse`, `AIUSE_SKIP_HOMEBREW_FORMULA=1`).
2. After release, the first sampler run seeds the store from that machine's
   omp logs automatically (or run `aiuse gate status` once now, working
   tree, as done here).
3. Follow-ups (also in [`docs/next-options.md`](../next-options.md)):
   serve's cached payloads/suggestions are selected before gate evaluation
   and can briefly disagree (astra risk #4); `prepare_snapshot` unification;
   omp upstream PR for gate consultation; multi-account Claude scoping
   (astra: no "any account clears all" rule — current latch is single-account
   by construction).
4. The Claude weekly window resets 2026-10-23T10:00:00Z; the latch will
   self-expire (+120s). Do not spend omp before then.

## Detached jobs

none

## Quick Start

```bash
cd /Users/djbclark/src/aiuse && git pull --ff-only
AIUSE_SKIP_HOMEBREW_FORMULA=1 .venv/bin/aiuse gate status   # exit 1 while gated
.venv/bin/python -m pytest -q tests/test_rate_gate.py tests/test_rate_gates.py
```

Parent: [HANDOFF_standalone-39fc_omp-cache-gated-release_2026-10-22_f0ee.md](HANDOFF_standalone-39fc_omp-cache-gated-release_2026-10-22_f0ee.md).
Gate contract: [docs/omp-cloud-credits.md](../omp-cloud-credits.md).
