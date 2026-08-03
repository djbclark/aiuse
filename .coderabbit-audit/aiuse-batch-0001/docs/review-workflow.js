// See also: ../AGENTS.md (repo map), ../README.md (project), code-review-2026-07-23.html
// (the output this script produced), fix-implementation-plan.md (what came of it).
//
// This is a Claude Code "Workflow" orchestration script — not standalone Node/JS.
// It only runs inside Claude Code's Workflow tool, which supplies `agent()`,
// `pipeline()`, `parallel()`, `phase()`, `log()`, and `args` as part of a
// sandboxed script execution context; there is no npm entry point for it.
// Checked in verbatim (run ID wf_280ac10f-931, 2026-07-23) for reproducibility
// and so the review's methodology — what was asked, in what order, with what
// verification — is auditable rather than only described secondhand in the
// report it produced. Five of the ~79 agent calls (two Design-phase proposals
// and both Judge calls) failed on a usage-credit limit mid-run; the report's
// footer and fix-implementation-plan.md both note where hand-synthesis filled
// that gap.
//
// To re-run or extend: Workflow({ scriptPath: '<path to this file>',
// resumeFromRunId: 'wf_280ac10f-931' }) replays completed agent() calls from
// cache and only re-executes new/changed ones.

export const meta = {
  name: 'ai-project-ultra-review',
  description: 'Multi-agent review of the ai quota CLI plus design panels for tokscale timeouts and rating algorithm',
  phases: [
    { title: 'Review', detail: 'six dimension finders over collectors, analysis, report, tests' },
    { title: 'Verify', detail: 'adversarial verification of each finding' },
    { title: 'Design', detail: 'independent proposals: tokscale containment, rating redesign' },
    { title: 'Judge', detail: 'judge + synthesis per design topic' },
  ],
}

const ROOT = '/Users/djbclark/src/ai'
const SP = args.scratchpad

const CONTEXT = `
PROJECT CONTEXT — the "ai" CLI (${ROOT}, Python 3.14, src layout at src/aiuse/, tests in tests/).
Purpose: collect AI subscription quota usage from three external CLIs and tell the user which
paid subscription windows to burn before they reset ("use it or lose it").
- collectors/cswap.py: canonical multi-account Claude Code source (cswap list --json).
- collectors/codexbar.py: live multi-provider source. Discovers enabled providers via a fast
  local call, then queries each provider in its own concurrent subprocess (90s each), isolating
  timeouts/errors per provider.
- collectors/tokscale.py: complementary multi-provider source; ONE bundled "tokscale usage --json"
  call with a single 120s timeout.
- collectors/base.py: run_json subprocess helper. collectors/runner.py: runs collectors
  concurrently, canonicalizes provider slugs, selects which source's rows drive the report, and
  cross-checks overlapping live measurements.
- models.py: dataclasses (AccountUsage, QuotaWindow, Snapshot, alerts), window-duration bucketing.
- analysis/use_or_lose.py: alert generation + scoring. Two paths: legacy _score and multi-dim
  _score_multi_dimension (default on) blending value-at-risk, consumption flexibility, deadline.
- analysis/history.py: snapshot persistence, learned burn rates, chronic-waste summary.
- report.py: terminal report; cli.py: entrypoint; config.py: defaults + YAML/JSON merge.
There is an UNCOMMITTED working-tree diff (run: git -C ${ROOT} diff) — it is part of the review:
it adds window_minutes inference to tokscale rows, adds max_days/min_remaining gates to the
multi-dim path, and renames a report section.
Use absolute paths when reading files. You may run read-only commands and
${ROOT}/.venv/bin/python for experiments. Do NOT modify any files, do NOT run git commands that
change state, do NOT run any external CLI subcommand that logs in, submits, or deletes data.
`

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      maxItems: 10,
      items: {
        type: 'object',
        properties: {
          file: { type: 'string', description: 'repo-relative path, e.g. src/aiuse/analysis/use_or_lose.py' },
          line: { type: 'integer' },
          title: { type: 'string' },
          severity: { enum: ['critical', 'major', 'minor', 'nit'] },
          category: { type: 'string' },
          description: { type: 'string' },
          failure_scenario: { type: 'string', description: 'concrete inputs/state -> wrong output' },
          suggested_fix: { type: 'string' },
        },
        required: ['file', 'title', 'severity', 'description', 'failure_scenario'],
        additionalProperties: false,
      },
    },
  },
  required: ['findings'],
  additionalProperties: false,
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { enum: ['CONFIRMED', 'REFUTED', 'UNCERTAIN'] },
    reasoning: { type: 'string' },
    corrected_severity: { enum: ['critical', 'major', 'minor', 'nit'] },
    corrected_description: { type: 'string' },
  },
  required: ['verdict', 'reasoning'],
  additionalProperties: false,
}

const PROPOSAL_SCHEMA = {
  type: 'object',
  properties: {
    title: { type: 'string' },
    summary: { type: 'string' },
    detailed_design: { type: 'string', description: 'full markdown design incl. exact functions/files to change' },
    tradeoffs: { type: 'string' },
    implementation_sketch: { type: 'string', description: 'concrete code-level sketch' },
    facts_discovered: { type: 'string', description: 'any new facts you verified (CLI flags, configs, timings)' },
  },
  required: ['title', 'summary', 'detailed_design', 'tradeoffs', 'implementation_sketch'],
  additionalProperties: false,
}

const JUDGE_SCHEMA = {
  type: 'object',
  properties: {
    winner: { type: 'integer', description: '1-based index of winning proposal' },
    scores: { type: 'array', items: { type: 'object', properties: { proposal: { type: 'integer' }, score: { type: 'number' }, rationale: { type: 'string' } }, required: ['proposal', 'score', 'rationale'], additionalProperties: false } },
    synthesis: { type: 'string', description: 'final recommended design in markdown, merging the best ideas' },
  },
  required: ['winner', 'scores', 'synthesis'],
  additionalProperties: false,
}

const FINDER_COMMON = `
You are one reviewer in a fan-out code review. Find REAL defects: correctness bugs, logic errors,
misleading output, silent data loss, dead/cancelled-out math, unsafe subprocess/timeout handling,
behavior changes hidden in the uncommitted diff. Quality issues (misleading naming, duplicated
logic) only if they would plausibly cause a future bug or wrong user decision. NOT style nits.
For every finding give a CONCRETE failure scenario (specific input data -> specific wrong output).
Where practical, verify by running ${ROOT}/.venv/bin/python snippets importing the module.
Rank findings most-severe first. Report at most 10; if you find more, keep the worst.
Leads below are unverified hypotheses from a first read — check each honestly (some may be wrong),
and search beyond them.
`

const FINDERS = [
  {
    key: 'tokscale',
    prompt: `${CONTEXT}\n${FINDER_COMMON}
SCOPE: src/aiuse/collectors/tokscale.py (including its uncommitted diff hunks) and src/aiuse/collectors/base.py.
Real payload sample from a live run is saved at ${SP}/tokscale-usage.json (labels seen live:
Claude "Session"/"Weekly", Codex "Weekly", Copilot "Chat"/"Completions"/"Premium", Grok Build "Weekly").
Leads:
- _infer_window_minutes maps labels "chat"/"completions"/"premium" to monthly for ANY provider, not just copilot.
- '"7" in display_label.lower()' can false-positive on unrelated labels.
- the redundant membership + substring checks for 5h labels.
- single bundled 120s run_json call: one slow provider inside tokscale stalls the entire collector; no partial results.
- copilot resets_at values like "2026-08-01" (date only) — check parse_dt handles them and what happens downstream if not.
- BillingKind assignment when metrics list is empty vs credit-only rows.
Also check base.py run_json JSON-extraction fallback for edge cases (e.g. stderr banners, multiple JSON values).`,
  },
  {
    key: 'codexbar',
    prompt: `${CONTEXT}\n${FINDER_COMMON}
SCOPE: src/aiuse/collectors/codexbar.py.
Leads:
- min_timeout=180 is applied only when discovery returns exactly one provider; is the reasoning sound for other counts?
- _normalize_providers: "all"/"both" become a literal provider arg; check _query_provider timeout logic for that path.
- _from_row: has_named_balance_blob skips primary/secondary/tertiary for PREPAID_HINTS providers — can that hide real subscription windows?
- the "$" balance regex on reset descriptions (DeepSeek path) — false positives/negatives.
- _window: the 0/0 quantity heuristic sets used=None remaining=0.0 — trace what analysis does with that row.
- _slot_label fixed label tables vs tokscale's labels for the same provider (cross-check matching in runner._matching_window matches by exact label, else resets_at within 15 min).
- error row shape: provider "codexbar-query-errors" account row — how does the rest of the pipeline treat it (canonicalization, report, analysis)?`,
  },
  {
    key: 'runner-cswap',
    prompt: `${CONTEXT}\n${FINDER_COMMON}
SCOPE: src/aiuse/collectors/runner.py and src/aiuse/collectors/cswap.py.
Leads:
- _select_and_cross_check: when cswap is authoritative for claude, tokscale's claude rows are silently DROPPED — never selected, never cross-checked (live tokscale reports a Claude row today; see ${SP}/tokscale-usage.json).
- cswap rows with error but no windows: check _has_live_data / selection / analysis skip logic end to end.
- _claude_cross_checks account matching by email lowercase; cswap account may be "cswap-slot-N" when email missing while codexbar has the email — spurious warnings?
- cswap _window_from_block: named windows (fiveHour/sevenDay/monthly) get window_minutes only if the block carries windowMinutes — if cswap schema v1 omits it, Claude windows have window_minutes=None; trace what that does to multi-dim scoring, flexibility profile, and report consumption lines. Verify against the real cswap CLI if available (cswap list --json is read-only).
- runner._enabled semantics for booleans/dicts; ThreadPoolExecutor usage; collector errors aggregation.
- _canonical_provider aliases vs tokscale provider keys ("grok-build" -> "grok"; "Claude" -> "claude"?) — check case and space normalization end to end.`,
  },
  {
    key: 'analysis',
    prompt: `${CONTEXT}\n${FINDER_COMMON}
SCOPE: src/aiuse/analysis/use_or_lose.py (including uncommitted diff hunks) and src/aiuse/models.py.
Leads:
- line ~153: cycles_needed = max(1, int(round((remaining/100.0)*capacity/capacity))) — capacity cancels out; expression is just round(remaining/100) in [0,1] -> always 1 for remaining<150%. Determine intended semantics and blast radius (earliest_start_calendar, burn text, flexibility_urgency).
- the NEW uncommitted gates (days>max_days, remaining<min_remaining) in the multi-dim path: what alerts existed before that now vanish? e.g. windows below 40% remaining were previously eligible (throttled-waste bucket in report). Is filtering by min_remaining before scoring consistent with multi-dim's own value/urgency filters?
- claude 5h override: flexibility 0.0 + refill_capacity 45 requests -> cycles_needed=1 -> earliest_start_calendar = resets_at - window_minutes = the past -> flexibility_urgency=100 always; combined with deadline_urgency for days<=0.5, a Claude 5h window near reset scores HIGH forever. Quantify the score with .venv python.
- _score_multi_dimension: max_plan_price derived from the max across ALL configured plans — value_urgency for a cheap plan is diluted by an expensive one; intended?
- dedupe key uses window.label + resets_at — same label windows without resets_at collapse across DIFFERENT accounts? (key includes account) — verify.
- _plan_meta: plans.get(lookup) or plans.get(provider) — provider already canonicalized; dead second lookup?
- models.QuotaWindow.same_measurement float equality; days_until_reset negative handling; classify_window_minutes boundaries (360 exactly, 10080 exactly).
- score scaling *1.5 and urgency thresholds: reachable ranges for each urgency; is CRITICAL reachable at all for realistic inputs?`,
  },
  {
    key: 'history-config',
    prompt: `${CONTEXT}\n${FINDER_COMMON}
SCOPE: src/aiuse/analysis/history.py and src/aiuse/config.py.
Leads:
- merge_learned_flexibility: on provider miss it falls back to ANY provider with the same duration_kind (endswith ':weekly') — cross-provider contamination of learned flexibility; first-match order is dict order.
- compute_learned_flexibility compares each historical snapshot to the CURRENT one only (prev->now), weighting all pairs equally — an old snapshot in the same window cycle yields a low burn rate estimate; consumed<=0 pairs are skipped entirely (a window that RESET between snapshots silently contributes nothing — is that right for burn learning?).
- _find_current_remaining requires exact resets_at string equality — isoformat formatting differences (e.g. '+00:00' vs 'Z', microseconds) between saved snapshot JSON and live windows.
- chronic_waste_summary: only windows <=360 min (message says 'Throttled window'); history[:7] slice; avg over per-snapshot samples counts the same window cycle multiple times.
- load_recent_snapshots: sorted(directory.iterdir(), reverse=True) relies on filename ordering; non-JSON files; max_count=30 interacting with retention.
- save_snapshot writes file then chmods 0600 — pre-existing umask race, plus chmod(0o700) on every save.
- config._deep_merge list handling (lists replaced wholesale — fine?), _xdg_config_home, load_config break-on-first-candidate.
- DEFAULT_CONFIG: consumption_flexibility_defaults has 'daily' key but classify_window_minutes never produces 'daily'; provider_overrides use 'gemini' but runner canonicalizes to 'antigravity' with alias mapping in _plan_meta — check _classify_flexibility uses provider_overrides with the CANONICAL key while _plan_meta aliases antigravity->gemini; are overrides for gemini ever applied to antigravity rows?`,
  },
  {
    key: 'report-cli-tests',
    prompt: `${CONTEXT}\n${FINDER_COMMON}
SCOPE: src/aiuse/report.py (incl. uncommitted diff), src/aiuse/cli.py, src/aiuse/__main__.py, tests/ (all files), pyproject.toml, justfile.
First run the test suite: cd ${ROOT} && .venv/bin/python -m pytest -q  (report failures as findings; do not fix anything).
Leads:
- report.py imports private functions from use_or_lose (_classify_flexibility, _compute_value_at_risk) — duplicated computation drift risk vs the profiles already attached to alerts.
- _throttled_waste_line: monthly_waste = value_usd * 30 — value_usd is per-CYCLE; for a 5h window there are ~4.9 waking cycles/day, so x30 is wrong dimensionally; check.
- _action_buckets: 'THIS WEEKEND' = days<=10; throttled+days<=3 goes to THIS WEEK while longer throttled goes to a waste bucket — sensible?
- color semantics: high remaining yellow / low green — intentional inversion, but check consistency across bars and text.
- _human_deadline / _time_bucket boundary duplication with analysis buckets.
- cli.py: argument handling, --json path (does it include cross_checks/collector_errors?), exit codes, snapshot saving call sites.
- tests: enumerate which currently-shipping behaviors have NO test (multi-dim scoring path, the new uncommitted gates, tokscale window_minutes inference, runner claude/tokscale drop) and report the riskiest gaps as findings (category 'test-coverage', severity minor unless the gap hides a live bug).`,
  },
]

function verifyPrompt(fd, i) {
  return `${CONTEXT}
You are adversarial verifier #${i + 1} for one code-review finding. Your default stance: the finding
is WRONG until the code proves otherwise. Read the actual file(s), re-derive the behavior, and if
practical reproduce with ${ROOT}/.venv/bin/python. REFUTED if the failure scenario cannot occur as
described (wrong line, guarded elsewhere, intended+documented behavior, or scenario unreachable).
CONFIRMED only if you can trace concrete inputs to the wrong outcome. UNCERTAIN only when
verification genuinely needs data you cannot obtain. If confirmed but severity is inflated/deflated,
set corrected_severity.
FINDING:
file: ${fd.file}
line: ${fd.line || 'unspecified'}
title: ${fd.title}
severity: ${fd.severity}
description: ${fd.description}
failure_scenario: ${fd.failure_scenario}
suggested_fix: ${fd.suggested_fix || 'none given'}`
}

const TOKSCALE_FACTS = `
VERIFIED FACTS (from live inspection today — re-verify anything you build on):
- tokscale on PATH is a wrapper script: 'exec npx tokscale@latest "$@"' (~/.local/bin/tokscale).
  Cold npx resolution can download the package (observed today: tokscale@4.7.0 auto-installed);
  that latency lands INSIDE the collector's 120s timeout. '@latest' also means unpinned behavior.
- 'tokscale usage --help' (v4.7.1... check yourself) shows ONLY --json, --light, --home. There is NO
  --provider filter. Binary strings confirm no hidden provider flag for 'usage'.
- tokscale has per-integration subcommands: codex, cursor, antigravity, trae, warp — run
  '<sub> --help' to see if any expose per-provider usage/quota output. There may also be settings in
  ~/.config/tokscale (a cache dir exists) controlling which providers 'usage' queries. Investigate
  (read-only!). Do NOT run login/logout/submit/delete/autosubmit subcommands.
- A warm 'tokscale usage --json' ran in ~1s today; output saved at ${SP}/tokscale-usage.json
  (providers: Claude, Codex, Copilot, Grok Build). So steady-state latency is fine; the risk is
  cold npx installs, provider-API hangs inside tokscale, and unpinned upgrades.
- codexbar.py already does per-provider containment: fast local discovery of enabled providers,
  then one subprocess per provider run concurrently (90s each), errors isolated per provider, with
  a bundled-call fallback. runner.py runs the three collectors concurrently; tokscale is only
  SELECTED when codexbar has no live rows for that provider; claude rows from tokscale are
  currently dropped when cswap is authoritative.
USER'S ASK: "do tokscale per-provider like codexbar so timeouts are contained."
Your design must honestly confront that tokscale's CLI has no provider filter today.`

const RATING_FACTS = `
USER'S COMPLAINT (verbatim): "we should reconsider the rating algorithm, claude code goes to the
top too often because an hourly window is often near up, but often times the weekly usage will be
almost exhausted with several days until reset."
VERIFIED SUPPORTING FACTS:
- Claude's 5-hour window and weekly window draw down the SAME underlying allotment: burning the 5h
  window consumes weekly budget. When the weekly is nearly exhausted, "use your 5h window now" is
  actively harmful advice (it accelerates weekly lockout).
- Live data right now (${SP}/tokscale-usage.json + cswap): Claude Session(5h) ~3% used (97%
  remaining, resets in ~5h), Claude Weekly 64% used (36% remaining, resets in ~2 days). Under
  current code the weekly is FILTERED OUT (36% < min_remaining_percent=40, a gate the uncommitted
  diff extends to the multi-dim path), while the 5h window scores high.
- Why 5h wins structurally (src/aiuse/analysis/use_or_lose.py::_score_multi_dimension): deadline
  urgency uses ABSOLUTE days (days<=0.5 -> raw 100); a 5h window is always within 0.5 days of
  reset. Claude 5h config override (config.py): flexibility 0.0, refill_capacity 45 requests ->
  cycles_needed collapses to 1 (note: the formula (remaining/100)*capacity/capacity cancels — a
  suspected bug) -> earliest_start_calendar = resets_at - 300min = in the past ->
  flexibility_urgency = 100. Net: Claude 5h scores ~HIGH every run regardless of weekly state.
- Alerts are generated per-window independently; nothing in analyze_use_or_lose looks at sibling
  windows of the same account/provider.
DESIGN REQUIREMENTS:
1. Never recommend burning capacity that is gated by a scarcer enclosing window (5h gated by weekly,
   weekly gated by monthly where applicable).
2. When a long window is nearly exhausted with days left, the right output is a CONSERVE advisory
   (pace yourself / it resets Friday), not silence — today it's silently filtered by min_remaining.
3. Deadline urgency should be meaningful for short recurring windows (they always reset soon —
   that alone must not dominate).
4. Preserve: config back-compat where reasonable, both scoring paths or an explicit migration,
   explainable report lines, JSON output stability, testability (spec the tests).
Read src/aiuse/analysis/use_or_lose.py, src/aiuse/models.py, src/aiuse/config.py, src/aiuse/report.py,
tests/test_use_or_lose.py before designing. Cover both the scoring change AND how report.py
presents it (incl. the action-plan buckets).`

const TOPICS = [
  {
    key: 'tokscale-timeouts',
    angles: [
      `${CONTEXT}\n${TOKSCALE_FACTS}\nANGLE 1 — WORK WITH TODAY'S TOKSCALE AS-IS. Design timeout containment without changing tokscale upstream: think version pinning / bypassing the npx-latest wrapper from the collector, tighter budget with cached-last-good fallback (staleness-labeled), demoting tokscale to cross-check-only so its failure never blocks selection, per-integration subcommands if any expose usage, killing the subprocess early while runner proceeds, etc. Investigate the CLI hands-on (read-only) before proposing. Be specific about changes to src/aiuse/collectors/tokscale.py, base.py, runner.py and config.`,
      `${CONTEXT}\n${TOKSCALE_FACTS}\nANGLE 2 — CHANGE THE INTERFACE. Design the ideal per-provider containment matching codexbar.py's pattern, including what has to change outside this repo: an upstream tokscale '--provider' flag (spec it: discovery of enabled providers, per-provider usage), or replacing the bundled call with N concurrent 'tokscale usage' calls each filtered via whatever mechanism exists (settings toggles? --home trick with per-provider config dirs? evaluate honestly), or dropping tokscale's aggregated call for direct per-integration subcommand calls. Spec the collector code (mirror codexbar.py's discovery/fan-out/fallback structure) and the migration/fallback story when the flag is absent (old tokscale version).`,
    ],
  },
  {
    key: 'rating-algorithm',
    angles: [
      `${CONTEXT}\n${RATING_FACTS}\nANGLE A — HIERARCHICAL BUDGET COUPLING. Model the real constraint: per account, windows form a hierarchy (5h ⊂ weekly ⊂ monthly) sharing one allotment. Derive each window's EFFECTIVE usable remaining = min(own remaining, tightest enclosing window's remaining headroom), gate/demote child-window alerts accordingly, and emit CONSERVE advisories when a parent is behind pace. Spec the grouping logic (how to detect the hierarchy from window_minutes per account), the scoring changes, and report changes.`,
      `${CONTEXT}\n${RATING_FACTS}\nANGLE B — PACE-BASED EXPECTED-WASTE SCORING. Replace absolute-days deadline urgency with pace: compare fraction-of-window-elapsed vs fraction-used; project waste at reset (optionally using history.py burn rates); rank by projected wasted VALUE, so a weekly window 90% unused halfway through outranks any 5h window, and a weekly 64% used at 70% elapsed is ON PACE (no alert, or conserve note). Define the math precisely for windows with/without resets_at and window_minutes, and how the 5h window's score derives from weekly headroom.`,
      `${CONTEXT}\n${RATING_FACTS}\nANGLE C — MINIMAL SURGICAL FIX. Smallest diff that fixes the complaint without re-architecting: e.g. normalize deadline urgency by window duration, cap short-window urgency by sibling long-window remaining (a simple per-account pre-pass), replace the min_remaining hard gate with a conserve-advisory branch, fix the cycles_needed cancellation. Every change must name exact functions/lines and keep existing tests' spirit; spec new tests. Argue why minimal beats redesign here (or concede where it can't).`,
    ],
  },
]

function judgePrompt(topic, proposals) {
  const blob = proposals
    .map((p, i) => (p ? `PROPOSAL ${i + 1}: ${p.title}\nSUMMARY: ${p.summary}\nDESIGN:\n${p.detailed_design}\nTRADEOFFS:\n${p.tradeoffs}\nIMPLEMENTATION:\n${p.implementation_sketch}\nFACTS DISCOVERED:\n${p.facts_discovered || 'n/a'}` : `PROPOSAL ${i + 1}: (failed)`))
    .join('\n\n========\n\n')
  return `${CONTEXT}
You are the judge for design topic "${topic.key}". Score each proposal 0-10 on: (a) actually fixes
the user's stated problem, (b) technical soundness against the REAL code (read the files to check
claims; distrust any CLI capability claims you can cheaply re-verify), (c) implementation cost /
risk, (d) operability (fallbacks, back-compat, explainable output). Then write a SYNTHESIS: the
single recommended design, merging the best ideas across proposals, concrete enough to implement
(files, functions, config keys, test list). Note open questions the user must decide.
${topic.key === 'rating-algorithm' ? RATING_FACTS : TOKSCALE_FACTS}
${blob}`
}

phase('Review')
const reviewed = await pipeline(
  FINDERS,
  (f) => agent(f.prompt, { label: `find:${f.key}`, phase: 'Review', schema: FINDINGS_SCHEMA, effort: 'high' }),
  (res, f) => {
    const fs = (res && res.findings) || []
    log(`${f.key}: ${fs.length} findings`)
    return parallel(
      fs.map((fd) => () => {
        const n = fd.severity === 'critical' || fd.severity === 'major' ? 2 : 1
        return parallel(
          Array.from({ length: n }, (_, i) => () =>
            agent(verifyPrompt(fd, i), {
              label: `verify:${f.key}:${(fd.file || '?').split('/').pop()}:${fd.line || 0}${n > 1 ? ':' + (i + 1) : ''}`,
              phase: 'Verify',
              schema: VERDICT_SCHEMA,
              effort: 'high',
            })
          )
        ).then((vs) => ({ ...fd, dimension: f.key, verdicts: vs.filter(Boolean) }))
      })
    )
  }
)

phase('Design')
const designs = await pipeline(
  TOPICS,
  (t) =>
    parallel(
      t.angles.map((p, i) => () => agent(p, { label: `design:${t.key}:${i + 1}`, phase: 'Design', schema: PROPOSAL_SCHEMA }))
    ),
  (props, t) =>
    agent(judgePrompt(t, props.filter(Boolean).length ? props : []), {
      label: `judge:${t.key}`,
      phase: 'Judge',
      schema: JUDGE_SCHEMA,
    }).then((j) => ({ topic: t.key, proposals: props, judgment: j }))
)

const flat = reviewed.filter(Boolean).flat().filter(Boolean)
const confirmed = flat.filter((f) => {
  const c = f.verdicts.filter((v) => v.verdict === 'CONFIRMED').length
  const r = f.verdicts.filter((v) => v.verdict === 'REFUTED').length
  return c > 0 && c >= r
})
const uncertain = flat.filter((f) => !confirmed.includes(f) && f.verdicts.some((v) => v.verdict !== 'REFUTED'))
log(`findings: ${flat.length} raw, ${confirmed.length} confirmed, ${uncertain.length} uncertain`)
return { confirmed, uncertain, refuted: flat.length - confirmed.length - uncertain.length, designs }