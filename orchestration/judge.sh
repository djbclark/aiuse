#!/usr/bin/env bash
# judge.sh — refuse loop completion unless tracker, git and tests agree (US-002, aiuse-juk.2).
#
# Wired into ralph-orchestrator as two lifecycle hooks:
#   hooks.events.pre.loop.start    -> judge.sh --record-start  (on_error: block)
#   hooks.events.pre.loop.complete -> judge.sh                 (on_error: block)
#
# --record-start writes the loop's starting HEAD, ralph's loop id (from the
# hook payload on stdin) and this script's own git blob hash to
# $JUDGE_STATE_DIR/loop-start.json. The completion check then measures the
# work from that SHA, refuses a record that belongs to another loop, and
# refuses when this script changed since the loop started.
#
# It never reads the agent's transcript or claims. It checks three things itself:
#   (a) tracker: `bd show $TASK_ID --json` has status "closed" and a close
#       reason of at least 20 characters
#   (b) git:     EXPECT_DIFF=yes needs >=1 commit on HEAD beyond the base
#                branch and a clean tree (the work is committed);
#                EXPECT_DIFF=no needs no commits beyond the base and a clean tree.
#                The commits must not touch what the judge relies on: the
#                check recipe (justfile), test and lint config, conftest.py,
#                orchestration/ (this script), CI, or skip or delete tests.
#   (c) tests:   runs TEST_CMD itself and requires exit status 0
#
# Exit 0 and "JUDGE PASS: tracker+git+tests agree" only when all three hold.
# Anything else, including a missing input or tool, exits 1 with
# "JUDGE REFUSE: <reason>" as the first line of output (fail closed).
#
# Environment:
#   TASK_ID       bead id the loop is working on (required)
#   EXPECT_DIFF   yes | no (required)
#   TEST_CMD      check command, run with bash -c from the repo root
#                 (default: just check)
#   BD_DIR        directory bd runs in (default: the repo root). Point it at
#                 the checkout that owns .beads/ when the loop runs in a worktree.
#   JUDGE_STATE_DIR  where loop-start.json lives (default: JUDGE_LOG_DIR). Put
#                 it outside the clone so the agent cannot rewrite the record.
#   JUDGE_BASE    git ref to measure against when no loop start was recorded
#                 (a manual run). A recorded loop start always wins. With
#                 neither, the judge refuses rather than guess a base.
#   JUDGE_LOG_DIR where the verdict log and test logs go (default: .ralph/judge)
#   JUDGE_ALLOW_PROTECTED  yes = accept changes to the guarded paths above for
#                 this bead (listed in the verdict log). Default: refuse them.
#
# Run it from a pinned copy outside the clone the agent writes to (see
# ralph.aiuse.example.yml); a copy inside the clone can be edited by the agent.
#
# ralph keeps only the first max_output_bytes of each stream, so the verdict
# line always comes first and the test output goes to a log file.
# Dependencies: bash, bd, git, jq.
set -uo pipefail

verdict_log=""

record() {
  [ -n "$verdict_log" ] || return 0
  printf '%s task=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${TASK_ID:-?}" "$1" >>"$verdict_log" 2>/dev/null || true
}

# refuse REASON [DETAIL...] — verdict first, detail lines after, exit 1.
refuse() {
  local msg="JUDGE REFUSE: $1"
  shift
  echo "$msg" >&2
  record "$msg"
  local line
  for line in "$@"; do echo "$line" >&2; done
  exit 1
}

mode=judge
case "${1:-}" in
"") ;;
--record-start) mode=record ;;
*) refuse "unknown argument '$1' (usage: judge.sh [--record-start])" ;;
esac

tools=(git jq)
[ "$mode" = record ] || tools+=(bd)
for tool in "${tools[@]}"; do
  command -v "$tool" >/dev/null 2>&1 || refuse "required tool '$tool' not on PATH"
done

# This script's own content hash, taken before any cd, so the completion check
# can tell whether the judge that runs now is the one that recorded the start.
self_hash=$(git hash-object -- "${BASH_SOURCE[0]}" 2>/dev/null) || refuse "cannot hash ${BASH_SOURCE[0]}"

# ralph writes a JSON payload to each hook's stdin; .loop.id names the loop.
# A manual run has no payload, so the loop id stays empty.
payload=""
if [ ! -t 0 ]; then
  IFS= read -r -d '' -t 10 payload || true
fi
loop_id=$(jq -r '.loop.id // empty' <<<"$payload" 2>/dev/null) || loop_id=""

root=$(git rev-parse --show-toplevel 2>/dev/null) || refuse "not inside a git repository ($PWD)"
cd "$root" || refuse "cannot cd to repo root $root"

log_dir="${JUDGE_LOG_DIR:-.ralph/judge}"
if mkdir -p "$log_dir" 2>/dev/null && [ -w "$log_dir" ]; then
  log_dir=$(cd "$log_dir" && pwd)
  verdict_log="$log_dir/verdicts.log"
else
  log_dir=$(mktemp -d "${TMPDIR:-/tmp}/judge.XXXXXX") || refuse "cannot create a log directory"
  verdict_log="$log_dir/verdicts.log"
fi

state_dir="${JUDGE_STATE_DIR:-$log_dir}"
start_file="$state_dir/loop-start.json"

if [ "$mode" = record ]; then
  start_sha=$(git rev-parse --verify -q 'HEAD^{commit}') || refuse "cannot read HEAD to record the loop start"
  mkdir -p "$state_dir" 2>/dev/null || refuse "cannot create JUDGE_STATE_DIR $state_dir"
  if ! jq -n --arg sha "$start_sha" --arg loop "$loop_id" --arg hash "$self_hash" \
    --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{sha: $sha, loop_id: $loop, judge_hash: $hash, recorded_at: $at}' >"$start_file.tmp.$$"; then
    refuse "cannot write $start_file"
  fi
  mv -f "$start_file.tmp.$$" "$start_file" || refuse "cannot write $start_file"
  msg="JUDGE START: loop ${loop_id:-unknown} starts at $start_sha"
  record "$msg"
  echo "$msg"
  exit 0
fi

[ -n "${TASK_ID:-}" ] || refuse "TASK_ID is not set; the judge cannot know which bead to check"
case "${EXPECT_DIFF:-}" in
yes | no) ;;
*) refuse "EXPECT_DIFF must be 'yes' or 'no' (got '${EXPECT_DIFF:-}')" ;;
esac

# (a) tracker: closed, with a real close reason.
bd_dir="${BD_DIR:-$root}"
state=$(bd -C "$bd_dir" --readonly show "$TASK_ID" --json 2>&1) ||
  refuse "bd show $TASK_ID failed" "$state"
issue=$(jq -ce 'if type == "array" then .[0] else . end | select(type == "object")' <<<"$state" 2>/dev/null) ||
  refuse "bd show $TASK_ID returned no issue object"
status=$(jq -r '.status // ""' <<<"$issue")
[ "$status" = closed ] || refuse "bd $TASK_ID status is '$status', not 'closed'"
reason=$(jq -r '(.close_reason // "") | gsub("^\\s+|\\s+$"; "")' <<<"$issue")
[ "${#reason}" -ge 20 ] ||
  refuse "bd $TASK_ID close reason is ${#reason} chars (need >= 20): '$reason'"

# (b) git: commit state matches the task type, and nothing is left uncommitted.
# The base is this loop's own start, never a guessed branch: a reused clone or
# branch can already be ahead of origin/main before the loop does anything.
if [ -e "$start_file" ]; then
  start=$(jq -ce 'select(type == "object" and (.sha | type) == "string")' "$start_file" 2>/dev/null) ||
    refuse "loop start record $start_file is unreadable"
  base=$(jq -r .sha <<<"$start")
  start_loop=$(jq -r '.loop_id // ""' <<<"$start")
  start_hash=$(jq -r '.judge_hash // ""' <<<"$start")
  if [ -n "$loop_id" ] && [ "$start_loop" != "$loop_id" ]; then
    refuse "loop start record is for loop '$start_loop', not this loop '$loop_id'; was judge.sh --record-start wired at pre.loop.start?"
  fi
  [ "$start_hash" = "$self_hash" ] ||
    refuse "judge.sh changed since the loop start (blob $start_hash -> $self_hash); run the judge from a pinned copy"
  base_desc="the loop start ${base:0:12}"
elif [ -n "${JUDGE_BASE:-}" ]; then
  base=$JUDGE_BASE
  base_desc=$base
else
  refuse "no loop start recorded ($start_file) and JUDGE_BASE is unset; wire 'judge.sh --record-start' at pre.loop.start"
fi
git rev-parse --verify -q "$base^{commit}" >/dev/null || refuse "base '$base' ($base_desc) does not exist"
git merge-base --is-ancestor "$base" HEAD 2>/dev/null ||
  refuse "$base_desc is not an ancestor of HEAD; the branch history changed under the loop"
ahead=$(git rev-list --count "$base..HEAD") || refuse "cannot count commits in $base..HEAD"
dirty=$(git status --porcelain -- . ':(exclude).ralph') || refuse "git status failed"
if [ -n "$dirty" ]; then
  refuse "uncommitted changes in the work tree (EXPECT_DIFF=$EXPECT_DIFF)" "$(head -20 <<<"$dirty")"
fi
if [ "$EXPECT_DIFF" = yes ] && [ "$ahead" -eq 0 ]; then
  refuse "no commits on HEAD beyond $base_desc for a task that expects a diff"
fi
if [ "$EXPECT_DIFF" = no ] && [ "$ahead" -ne 0 ]; then
  refuse "$ahead commit(s) beyond $base_desc on a task that expects no diff"
fi

# (b2) guard: the work must not weaken what the judge relies on. The agent can
# edit the clone, so a commit that rewrites the check recipe, the test config,
# the judge itself, or skips or deletes tests could otherwise pass all three
# checks. Such work is refused unless the operator set JUDGE_ALLOW_PROTECTED=yes
# for this bead, and even then every hit is written to the verdict log.
protected_re='^(orchestration/|[Jj]ustfile$|\.justfile$|pyproject\.toml$|pytest\.ini$|setup\.cfg$|tox\.ini$|(.*/)?conftest\.py$|\.pre-commit-config\.yaml$|\.github/|package\.json$|\.yamllint$|\.markdownlint|_typos\.toml$|ruff\.toml$|mypy\.ini$)'
skip_re='^\+.*(pytest\.mark\.(skip|skipif|xfail)|pytest\.(skip|xfail|importorskip)\(|unittest\.(skip|expectedFailure)|@skip)'
guard_hits=()
if [ "$ahead" -gt 0 ]; then
  changed=$(git diff --name-only "$base" HEAD --) || refuse "cannot list files changed in $base..HEAD"
  while IFS= read -r path; do
    [ -z "$path" ] || guard_hits+=("$path")
  done < <(grep -E "$protected_re" <<<"$changed" || true)
  deleted=$(git diff --name-only --diff-filter=D "$base" HEAD -- tests) || refuse "cannot list deleted tests in $base..HEAD"
  while IFS= read -r path; do
    [ -z "$path" ] || guard_hits+=("deleted test file $path")
  done <<<"$deleted"
  added=$(git diff -U0 "$base" HEAD -- tests) || refuse "cannot diff tests in $base..HEAD"
  while IFS= read -r line; do
    [ -z "$line" ] || guard_hits+=("added skip: ${line:1}")
  done < <(grep -vE '^\+\+\+ ' <<<"$added" | grep -E "$skip_re" || true)
fi
guard_note=""
if [ "${#guard_hits[@]}" -gt 0 ]; then
  if [ "${JUDGE_ALLOW_PROTECTED:-}" = yes ]; then
    joined=$(printf '%s,' "${guard_hits[@]}")
    guard_note=" protected changes allowed by JUDGE_ALLOW_PROTECTED: ${joined%,}"
  else
    refuse "the work changes what the judge relies on (${#guard_hits[@]} hit(s) since $base_desc); set JUDGE_ALLOW_PROTECTED=yes only after reviewing them" \
      "${guard_hits[@]/#/  }"
  fi
fi

# (c) tests: run them ourselves; never trust the agent's report.
test_cmd="${TEST_CMD:-just check}"
test_log="$log_dir/test-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
bash -c "$test_cmd" >"$test_log" 2>&1 </dev/null
rc=$?
if [ "$rc" -ne 0 ]; then
  refuse "test command '$test_cmd' exited $rc (full log: $test_log)" \
    "--- last 30 lines of the test log ---" "$(tail -30 "$test_log")"
fi

msg="JUDGE PASS: tracker+git+tests agree"
record "$msg (base=$base ahead=$ahead test_cmd='$test_cmd')$guard_note"
echo "$msg"
exit 0
