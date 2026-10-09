#!/usr/bin/env bash
# gh-issues-to-beads.sh — mirror aiuse's GitHub issues into beads (US-001, aiuse-juk.1).
#
# Idempotent: every imported bead carries external_ref "gh-<number>"; an issue
# whose ref already exists in the beads database is skipped, so re-running is
# safe. Dry-run is the default: it prints the exact bd commands it would run.
#
# Usage:
#   orchestration/gh-issues-to-beads.sh [--apply] [--repo OWNER/NAME] [--issue N]...
#
#   --apply        actually run the bd commands (default: dry run, no writes)
#   --repo R       GitHub repo (default: the current repo, via gh)
#   --issue N      also import issue N even if it is closed (repeatable). A
#                  closed issue is created and then closed in beads, with a
#                  close reason naming the GitHub issue, so both trackers agree.
#
# Environment:
#   BD_DIR         directory bd runs in (default: git toplevel). Point it at
#                  the checkout that owns .beads/ when running from a worktree.
#   GH_ISSUES_JSON read issues from this file instead of calling gh (tests).
#
# Mapping (title "[est. X] Title" is the repo's sizing convention):
#   title          the issue title without its "[est. ...]" prefix
#   description    issue URL, estimate, labels, then the issue body (truncated)
#   external_ref   gh-<number>
#   type           enhancement -> feature, bug -> bug, otherwise task
#   priority       bug label -> P1; title starting "Optional:" -> P3;
#                  documentation-only labels -> P3; everything else -> P2
#   labels         gh-import plus the issue's own labels
#
# Dependencies: gh, jq, bd, git.
set -euo pipefail

apply=no
repo=""
extra_issues=()
while [ $# -gt 0 ]; do
  case "$1" in
  --apply) apply=yes ;;
  --repo)
    repo="$2"
    shift
    ;;
  --issue)
    extra_issues+=("$2")
    shift
    ;;
  -h | --help)
    sed -n '2,32p' "$0"
    exit 0
    ;;
  *)
    echo "gh-issues-to-beads: unknown argument: $1" >&2
    exit 2
    ;;
  esac
  shift
done

bd_dir="${BD_DIR:-$(git rev-parse --show-toplevel)}"
max_body=4000

if [ -z "$repo" ] && [ -z "${GH_ISSUES_JSON:-}" ]; then
  repo=$(gh repo view --json nameWithOwner -q .nameWithOwner)
fi

fields=number,title,state,labels,body,url
if [ -n "${GH_ISSUES_JSON:-}" ]; then
  issues=$(cat "$GH_ISSUES_JSON")
else
  issues=$(gh issue list -R "$repo" --state all --limit 500 --json "$fields")
fi

# Wanted = every open issue plus any --issue numbers, oldest first.
wanted_json=$(printf '%s\n' "${extra_issues[@]+"${extra_issues[@]}"}" | jq -R 'select(length > 0) | tonumber' | jq -s .)
selected=$(jq -c --argjson extra "$wanted_json" \
  '[.[] | select(.state == "OPEN" or (.number as $n | $extra | index($n)))] | sort_by(.number) | .[]' \
  <<<"$issues")

missing=$(jq -r --argjson extra "$wanted_json" \
  '[.[].number] as $have | $extra[] | select(. as $n | $have | index($n) | not)' <<<"$issues")
if [ -n "$missing" ]; then
  echo "gh-issues-to-beads: requested issue(s) not found on GitHub: $(tr '\n' ' ' <<<"$missing")" >&2
  exit 1
fi

existing=$(bd -C "$bd_dir" --readonly list --all -n 0 --json | jq -r '.[].external_ref // empty')

run() {
  if [ "$apply" = yes ]; then
    "$@"
  else
    printf '  '
    printf '%q ' "$@"
    printf '\n'
  fi
}

created=0
skipped=0
while IFS= read -r issue; do
  [ -n "$issue" ] || continue
  num=$(jq -r .number <<<"$issue")
  ref="gh-$num"
  raw_title=$(jq -r .title <<<"$issue")
  if grep -qxF -- "$ref" <<<"$existing"; then
    echo "skip   #$num ($ref already in beads): $raw_title"
    skipped=$((skipped + 1))
    continue
  fi

  state=$(jq -r .state <<<"$issue")
  url=$(jq -r .url <<<"$issue")
  labels=$(jq -r '[.labels[].name] | join(",")' <<<"$issue")
  estimate=$(jq -r '.title | capture("^\\[est\\. (?<e>[^]]*)\\]").e // ""' <<<"$issue")
  title=$(jq -r '.title | sub("^\\[est\\. [^]]*\\] *"; "")' <<<"$issue")
  body=$(jq -r --argjson max "$max_body" \
    '(.body // "") as $b | if ($b | length) > $max then $b[0:$max] + "\n\n[truncated; full text on GitHub]" else $b end' \
    <<<"$issue")

  priority=2
  case ",$labels," in *,bug,*) priority=1 ;; esac
  if [ "$priority" = 2 ]; then
    case "$title" in Optional:*) priority=3 ;; esac
    if [ "$labels" = documentation ]; then priority=3; fi
  fi
  type=task
  case ",$labels," in
  *,bug,*) type=bug ;;
  *,enhancement,*) type=feature ;;
  esac

  description="GitHub issue #$num: $url
Estimate: ${estimate:-none given}
GitHub labels: ${labels:-none}
Imported by orchestration/gh-issues-to-beads.sh. Keep the trackers in step:
close this bead and GitHub issue #$num together.

$body"

  bead_labels="gh-import${labels:+,$labels}"
  echo "create #$num P$priority $type ($state): $title"
  if [ "$apply" = yes ]; then
    id=$(bd -C "$bd_dir" create --silent --title "$title" --description "$description" \
      --external-ref "$ref" --priority "$priority" --type "$type" --labels "$bead_labels")
    echo "       -> $id"
  else
    run bd -C "$bd_dir" create --silent --title "$title" --description "<${#description}-char description>" \
      --external-ref "$ref" --priority "$priority" --type "$type" --labels "$bead_labels"
    id='<new-id>'
  fi
  if [ "$state" = CLOSED ]; then
    run bd -C "$bd_dir" close "$id" --reason "Closed on GitHub as issue #$num before import; mirrored for cross-reference."
  fi
  created=$((created + 1))
done <<<"$selected"

mode=$([ "$apply" = yes ] && echo applied || echo "dry run, nothing written")
echo "summary: $created to create, $skipped already present ($mode)"
