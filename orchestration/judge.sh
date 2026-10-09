#!/usr/bin/env bash
# judge.sh — refuse loop completion unless tracker, git and tests agree (US-002, aiuse-juk.2).
#
# Wired into ralph-orchestrator as a lifecycle hook:
#   hooks.events.pre.loop.complete -> orchestration/judge.sh  (on_error: block)
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
#   JUDGE_BASE    git ref the work is measured against (default: the first of
#                 origin/HEAD, origin/main, main, origin/master, master that exists)
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

for tool in git jq bd; do
  command -v "$tool" >/dev/null 2>&1 || refuse "required tool '$tool' not on PATH"
done

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
base="${JUDGE_BASE:-}"
if [ -z "$base" ]; then
  for candidate in origin/HEAD origin/main main origin/master master; do
    if git rev-parse --verify -q "$candidate^{commit}" >/dev/null; then
      base=$candidate
      break
    fi
  done
fi
[ -n "$base" ] || refuse "no base ref found; set JUDGE_BASE"
git rev-parse --verify -q "$base^{commit}" >/dev/null || refuse "base ref '$base' does not exist"
ahead=$(git rev-list --count "$base..HEAD") || refuse "cannot count commits in $base..HEAD"
dirty=$(git status --porcelain -- . ':(exclude).ralph') || refuse "git status failed"
if [ -n "$dirty" ]; then
  refuse "uncommitted changes in the work tree (EXPECT_DIFF=$EXPECT_DIFF)" "$(head -20 <<<"$dirty")"
fi
if [ "$EXPECT_DIFF" = yes ] && [ "$ahead" -eq 0 ]; then
  refuse "no commits on HEAD beyond $base for a task that expects a diff"
fi
if [ "$EXPECT_DIFF" = no ] && [ "$ahead" -ne 0 ]; then
  refuse "$ahead commit(s) beyond $base on a task that expects no diff"
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
    refuse "the work changes what the judge relies on (${#guard_hits[@]} hit(s) in $base..HEAD); set JUDGE_ALLOW_PROTECTED=yes only after reviewing them" \
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
