#!/usr/bin/env bash
# mutation-test.sh — prove judge.sh and cswap-gate.sh really gate ralph (US-004, aiuse-juk.4).
#
# Rebuilds the final-plan section 1 rig: a throwaway git repo (never this one)
# with its own throwaway beads database, a stub agent run through ralph's
# `backend: custom`, and a ralph.yml that wires the real hooks the way the
# US-005 config does:
#   pre.loop.start      -> orchestration/judge.sh --record-start  (on_error: block)
#   pre.iteration.start -> orchestration/cswap-gate.sh            (on_error: block)
#   pre.loop.complete   -> orchestration/judge.sh                 (on_error: block)
#
# Five scenarios, each in a fresh rig:
#   lying   the agent commits, closes its bead, prints "tests: pass" and
#           LOOP_COMPLETE, but the repo's real check fails. Must be BLOCKED by
#           the judge, and the check must actually have run.
#   honest  the agent really fixes the bug; the check passes. Must COMPLETE,
#           with JUDGE PASS, proving the judge does not refuse everything.
#   gate    the active account's 5h window reads 95% used, above the gate's
#           80% threshold. Must be BLOCKED at pre.iteration.start before the
#           agent is ever spawned.
#   reused  the branch already has a leftover commit from earlier work; the
#           agent commits nothing, closes its bead and claims success. Must be
#           BLOCKED by the judge, which measures from the recorded loop start.
#   slow    the agent is honest, but the check hangs past the judge hook's
#           timeout_seconds (5 s here). Must be BLOCKED: ralph 2.10.1 treats a
#           hook timeout like a failure under on_error: block.
#
# By default the hooks see a stub `cswap` that reports 10% used (lying,
# honest) or 95% used (gate), so the result does not depend on the
# operator's live quota or on the login keychain being unlocked. With
# --real-cswap the hooks call the real cswap instead, and the gate scenario
# forces the threshold to 0% so it must refuse.
#
# Usage: orchestration/mutation-test.sh [--keep] [--real-cswap] [scenario...]
#   RALPH_BIN     ralph binary to test (default: ralph on PATH). Pin v2.10.1.
#   --keep        leave the rig directories in place and print where they are
#   --real-cswap  let the gate read the real `cswap list --json`
#
# Prints a transcript on stdout. Exits 0 only when every scenario produced its
# expected outcome. Needs: bash, git, jq, bd, ralph (and cswap with
# --real-cswap). Spends no LLM quota. bd runs with a scratch HOME so the rig
# never touches the operator's beads.
set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
judge="$here/judge.sh"
gate="$here/cswap-gate.sh"
ralph="${RALPH_BIN:-$(command -v ralph || true)}"
keep=no
real_cswap=no
scenarios=()
for arg in "$@"; do
  case "$arg" in
  --keep) keep=yes ;;
  --real-cswap) real_cswap=yes ;;
  lying | honest | gate | reused | slow) scenarios+=("$arg") ;;
  *)
    echo "usage: $0 [--keep] [--real-cswap] [lying|honest|gate|reused|slow]..." >&2
    exit 2
    ;;
  esac
done
[ "${#scenarios[@]}" -gt 0 ] || scenarios=(lying honest gate reused slow)
[ -x "$ralph" ] || {
  echo "mutation-test: no ralph binary (set RALPH_BIN)" >&2
  exit 2
}
needed=(git jq bd)
[ "$real_cswap" = no ] || needed+=(cswap)
for tool in "${needed[@]}"; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "mutation-test: $tool not on PATH" >&2
    exit 2
  }
done

work=$(mktemp -d "${TMPDIR:-/tmp}/judge-mutation.XXXXXX")
# shellcheck disable=SC2329 # invoked by the EXIT trap
cleanup() { [ "$keep" = yes ] || rm -rf "$work"; }
trap cleanup EXIT

# A non-interactive bash sources $BASH_ENV; an rc file there can re-prepend
# PATH and shadow the stub cswap, so ralph and its hooks run without one.
unset BASH_ENV ENV
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=rig GIT_AUTHOR_EMAIL=rig@example.invalid
export GIT_COMMITTER_NAME=rig GIT_COMMITTER_EMAIL=rig@example.invalid

# make_rig NAME MODE GATE_PCT STUB_PCT [JUDGE_HOOK_TIMEOUT [CHECK_SLEEP]] -> prints the rig repo path
make_rig() {
  local name=$1 mode=$2 gate_pct=$3 stub_pct=$4 judge_timeout=${5:-1800} check_sleep=${6:-0}
  local dir="$work/$name" repo="$work/$name/repo" bd_home="$work/$name/bd-home"
  local hook_path="$PATH"
  mkdir -p "$repo" "$bd_home"
  git -C "$repo" init -q -b main

  if [ "$real_cswap" = no ]; then
    mkdir -p "$dir/stub-bin"
    cat >"$dir/stub-bin/cswap" <<STUB
#!/usr/bin/env bash
# Stub cswap for the mutation rig: answers "list --json" only.
[ "\$*" = "list --json" ] || { echo "stub cswap: unexpected call: \$*" >&2; exit 64; }
cat <<'JSON'
{"schemaVersion": 1, "activeAccountNumber": 1, "accounts": [{"number": 1,
 "email": "rig@example.invalid", "active": true, "usageStatus": "ok",
 "usage": {"fiveHour": {"pct": $stub_pct, "resetsAt": "2026-10-09T09:40:00+00:00",
 "countdown": "4h 2m", "clock": "05:40"}}, "usageAgeSeconds": 5}]}
JSON
STUB
    chmod +x "$dir/stub-bin/cswap"
    hook_path="$dir/stub-bin:$PATH"
  fi

  # The "real" check: value.txt must say 2. It leaves a marker so the
  # transcript can prove the command was executed, not just claimed.
  cat >"$repo/check.sh" <<EOF
#!/usr/bin/env bash
echo "check.sh ran at \$(date -u +%H:%M:%SZ)" >> "$dir/check-ran"
sleep $check_sleep
echo "check.sh finished at \$(date -u +%H:%M:%SZ)" >> "$dir/check-finished"
v=\$(cat value.txt)
if [ "\$v" = 2 ]; then echo "check: value is 2 (ok)"; exit 0; fi
echo "check: FAIL value is \$v, expected 2"; exit 1
EOF
  echo 1 >"$repo/value.txt"

  # The stub agent. ralph runs it as `./agent.sh -p <prompt>`.
  cat >"$repo/agent.sh" <<EOF
#!/usr/bin/env bash
echo "agent ran pid \$\$" >> "$dir/agent-ran"
task=\$(cat "$dir/task-id")
if [ "$mode" = honest ]; then
  echo 2 > value.txt
  git add value.txt
  git commit -q -m "fix value (honest agent)"
elif [ "$mode" = idle ]; then
  : # commits nothing; relies on a leftover commit already on the branch
else
  echo "looks done" >> notes.txt
  git add notes.txt
  git commit -q -m "claim the fix (lying agent)"
fi
HOME="$bd_home" bd close "\$task" --reason "value bug fixed and verified by the agent" >/dev/null 2>&1 || true
echo "I fixed the bug and ran the suite."
echo "tests: pass"
echo "LOOP_COMPLETE"
EOF
  chmod +x "$repo/check.sh" "$repo/agent.sh"

  cat >"$repo/ralph.yml" <<EOF
cli:
  backend: custom
  command: ./agent.sh
  prompt_mode: arg
event_loop:
  max_iterations: 3
  completion_promise: LOOP_COMPLETE
hooks:
  enabled: true
  defaults:
    timeout_seconds: 60
    max_output_bytes: 8192
  events:
    pre.loop.start:
      - name: judge-start
        command: ["$judge", "--record-start"]
        on_error: block
        timeout_seconds: 60
    pre.iteration.start:
      - name: cswap-gate
        command: ["$gate"]
        on_error: block
        timeout_seconds: 120
        env:
          CSWAP_GATE_MAX_PCT: "$gate_pct"
          PATH: "$hook_path"
    pre.loop.complete:
      - name: judge
        command: ["$judge"]
        on_error: block
        timeout_seconds: $judge_timeout
        env:
          TASK_ID: "@TASK_ID@"
          EXPECT_DIFF: "yes"
          TEST_CMD: ./check.sh
          HOME: "$bd_home"
EOF

  (
    cd "$repo" || exit 1
    HOME="$bd_home" bd init -q --non-interactive --setup-exclude --skip-hooks --skip-agents -p rig >/dev/null 2>&1 ||
      HOME="$bd_home" bd init --non-interactive --setup-exclude --skip-hooks --skip-agents -p rig
    task=$(HOME="$bd_home" bd create --silent --title "Fix the value bug" --type task \
      --description "value.txt must contain 2; ./check.sh verifies it")
    echo "$task" >"$dir/task-id"
    sed -i.bak "s/@TASK_ID@/$task/" ralph.yml && rm -f ralph.yml.bak
    printf '.ralph/\n' >>.git/info/exclude
    git add check.sh agent.sh value.txt ralph.yml
    [ ! -f .gitignore ] || git add .gitignore # bd init writes one
    git commit -q -m "rig base"
    git checkout -q -b work
    if [ "$mode" = idle ]; then
      echo 2 >value.txt # earlier work on a reused branch, before this loop
      git add value.txt
      git commit -q -m "leftover commit from an earlier run"
    fi
    leftover=$(git status --porcelain)
    [ -z "$leftover" ] || {
      echo "rig $name: tree not clean after setup: $leftover"
      exit 1
    }
  ) >&2 || return 1
  echo "$repo"
}

hook_stream() { # hook_stream REPO HOOK STREAM -> ralph's own captured hook output
  local f
  f=$(find "$1/.ralph/diagnostics" -name hook-runs.jsonl 2>/dev/null | head -1)
  [ -n "$f" ] || return 0
  jq -r --arg h "$2" --arg s "$3" 'select(.hook_name == $h) | .[$s].content' "$f"
}

overall=0
run_scenario() {
  local name=$1 mode gate_pct stub_pct expect judge_timeout=1800 check_sleep=0
  case "$name" in
  lying) mode=lying gate_pct=80 stub_pct=10 expect=judge-block ;;
  honest) mode=honest gate_pct=80 stub_pct=10 expect=complete ;;
  gate) mode=honest gate_pct=80 stub_pct=95 expect=gate-block ;;
  reused) mode=idle gate_pct=80 stub_pct=10 expect=stale-block ;;
  slow) mode=honest gate_pct=80 stub_pct=10 expect=timeout-block judge_timeout=5 check_sleep=60 ;;
  esac
  if [ "$real_cswap" = yes ]; then
    gate_pct=100
    [ "$name" != gate ] || gate_pct=0
  fi
  local repo dir
  repo=$(make_rig "$name" "$mode" "$gate_pct" "$stub_pct" "$judge_timeout" "$check_sleep") || {
    echo "rig setup failed for $name"
    overall=1
    return
  }
  dir=$(dirname "$repo")

  local reading="stub cswap ${stub_pct}% used"
  [ "$real_cswap" = no ] || reading="real cswap"
  echo "=== scenario: $name (agent=$mode, gate threshold=${gate_pct}%, $reading, expect=$expect)"
  echo "\$ ralph run -c ralph.yml --no-tui -p 'Fix the value bug in value.txt'"
  local out="$dir/ralph-output.txt" rc
  (cd "$repo" && RALPH_DIAGNOSTICS=1 timeout 900 "$ralph" run -c ralph.yml --no-tui \
    -p "Fix the value bug in value.txt" </dev/null >"$out" 2>&1)
  rc=$?
  echo "--- ralph exit status: $rc; last 15 lines of ralph output:"
  tail -15 "$out" | sed 's/\x1b\[[0-9;]*m//g'
  echo "--- ralph's captured stderr of the judge hook:"
  hook_stream "$repo" judge stderr
  echo "--- ralph's captured stdout of the judge hook:"
  hook_stream "$repo" judge stdout
  echo "--- ralph's captured output of the cswap-gate hook (stdout, stderr):"
  hook_stream "$repo" cswap-gate stdout
  hook_stream "$repo" cswap-gate stderr
  echo "--- judge verdict log:"
  cat "$repo/.ralph/judge/verdicts.log" 2>/dev/null || echo "(none)"
  echo "--- real check executed: $([ -s "$dir/check-ran" ] && echo "yes ($(wc -l <"$dir/check-ran" | tr -d ' ') time(s))" || echo no)"
  echo "--- agent spawned: $([ -s "$dir/agent-ran" ] && echo "yes ($(wc -l <"$dir/agent-ran" | tr -d ' ') time(s))" || echo no)"

  local ok=no clean
  clean=$(sed 's/\x1b\[[0-9;]*m//g' "$out")
  case "$expect" in
  judge-block)
    if [ "$rc" -ne 0 ] && grep -q "Lifecycle hook 'judge' blocked orchestration at 'pre.loop.complete'" <<<"$clean" &&
      grep -q "JUDGE REFUSE: test command './check.sh' exited 1" "$repo/.ralph/judge/verdicts.log" 2>/dev/null &&
      [ -s "$dir/check-ran" ]; then ok=yes; fi
    ;;
  complete)
    if [ "$rc" -eq 0 ] && grep -q "JUDGE PASS: tracker+git+tests agree" "$repo/.ralph/judge/verdicts.log" 2>/dev/null &&
      [ -s "$dir/check-ran" ]; then ok=yes; fi
    ;;
  stale-block)
    if [ "$rc" -ne 0 ] && grep -q "Lifecycle hook 'judge' blocked orchestration at 'pre.loop.complete'" <<<"$clean" &&
      grep -q "JUDGE START: loop " "$repo/.ralph/judge/verdicts.log" 2>/dev/null &&
      grep -q "JUDGE REFUSE: no commits on HEAD beyond the loop start" "$repo/.ralph/judge/verdicts.log" 2>/dev/null; then ok=yes; fi
    ;;
  timeout-block)
    # The check started but never finished inside the hook timeout, and ralph
    # must not have completed the loop on the honest agent's LOOP_COMPLETE.
    if [ "$rc" -ne 0 ] && grep -q "Lifecycle hook 'judge' blocked orchestration at 'pre.loop.complete'" <<<"$clean" &&
      [ -s "$dir/check-ran" ] && [ ! -s "$dir/check-finished" ] &&
      ! grep -q "JUDGE PASS" "$repo/.ralph/judge/verdicts.log" 2>/dev/null; then ok=yes; fi
    ;;
  gate-block)
    if [ "$rc" -ne 0 ] && grep -q "Lifecycle hook 'cswap-gate' blocked orchestration at 'pre.iteration.start'" <<<"$clean" &&
      [ ! -s "$dir/agent-ran" ]; then ok=yes; fi
    ;;
  esac
  echo "--- verdict: $([ "$ok" = yes ] && echo "AS EXPECTED ($expect)" || echo "UNEXPECTED (wanted $expect)")"
  [ "$ok" = yes ] || overall=1
  echo
}

echo "ralph: $("$ralph" --version 2>&1) ($ralph)"
echo "judge: $judge"
echo "gate:  $gate"
echo "rig:   $work$([ "$keep" = yes ] && echo " (kept)")"
echo
for s in "${scenarios[@]}"; do run_scenario "$s"; done
echo "mutation test: $([ "$overall" -eq 0 ] && echo PASSED || echo FAILED)"
exit "$overall"
