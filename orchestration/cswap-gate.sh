#!/usr/bin/env bash
# cswap-gate.sh — refuse a new loop iteration when the active Claude 5h window
# is too depleted (US-003, aiuse-juk.3).
#
# Wired into ralph-orchestrator as a lifecycle hook:
#   hooks.events.pre.iteration.start -> orchestration/cswap-gate.sh  (on_error: block)
#
# It reads `cswap list --json` and nothing else. It never reads aiuse --json
# (its conserve/burn alerts are pace projections, not window state) and it
# never switches accounts: `cswap auto` stays off, this script only allows or
# refuses. Percent in cswap is the share USED.
#
# Exit 0 with "CSWAP GATE ALLOW: ..." when the active account's 5h window is
# below the threshold. Exit 1 with "CSWAP GATE REFUSE: ..." when it is at or
# above the threshold, and also when the reading is missing, unparsable,
# not ok, of unknown age, or older than CSWAP_GATE_MAX_AGE (fail closed).
#
# Environment:
#   CSWAP_GATE_MAX_PCT  refuse at or above this percent used (default 80)
#   CSWAP_GATE_MAX_AGE  refuse when cswap's usage reading is older than this
#                       many seconds (default 900)
#
# Dependencies: bash, cswap, jq.
set -uo pipefail

refuse() {
  echo "CSWAP GATE REFUSE: $1" >&2
  exit 1
}

for tool in cswap jq; do
  command -v "$tool" >/dev/null 2>&1 || refuse "required tool '$tool' not on PATH"
done

max_pct="${CSWAP_GATE_MAX_PCT:-80}"
max_age="${CSWAP_GATE_MAX_AGE:-900}"
case "$max_pct$max_age" in
*[!0-9]*) refuse "CSWAP_GATE_MAX_PCT and CSWAP_GATE_MAX_AGE must be whole numbers" ;;
esac

listing=$(cswap list --json 2>&1 </dev/null) || refuse "cswap list --json failed: $(head -3 <<<"$listing")"

account=$(jq -ce '
  . as $root
  | [.accounts[]? | select(.active == true or .number == $root.activeAccountNumber)][0]
  | select(type == "object")' <<<"$listing" 2>/dev/null) ||
  refuse "cswap list shows no active account"

field() { jq -r "$1" <<<"$account"; }
who="#$(field '.number // "?"') $(field '.email // "unknown"')"
status=$(field '.usageStatus // "missing"')
[ "$status" = ok ] || refuse "active account $who usage status is '$status'; cannot read its 5h window"

pct=$(field '.usage.fiveHour.pct // empty')
[ -n "$pct" ] || refuse "active account $who has no 5h window reading"
used=$(jq -rn --arg p "$pct" '$p | tonumber | floor') || refuse "unparsable 5h percent '$pct'"
pct=$(jq -rn --arg p "$pct" '$p | tonumber | if . == floor then floor else . end')
clock=$(field '.usage.fiveHour.clock // "?"')
countdown=$(field '.usage.fiveHour.countdown // "?"')
resets_at=$(field '.usage.fiveHour.resetsAt // "?"')
age=$(field '.usageAgeSeconds // empty')
reset_text="resets $clock (in $countdown; $resets_at)"

# Freshness is required, not optional: cswap omits usageAgeSeconds when it
# does not know the age, and an unknown age must not pass as fresh. Only a
# plain non-negative decimal is accepted (no sign, exponent or unit), so a
# negative age from clock skew or a value jq cannot compare refuses too.
[ -n "$age" ] || refuse "active account $who 5h reading has no reading age (usageAgeSeconds missing); cannot tell whether it is fresh"
[[ $age =~ ^[0-9]{1,9}(\.[0-9]+)?$ ]] || refuse "active account $who has an unparsable reading age '$age'"
stale=$(jq -rn --arg a "$age" --argjson m "$max_age" '($a | tonumber) > $m') ||
  refuse "active account $who has an unparsable reading age '$age'"
if [ "$stale" != false ]; then
  refuse "active account $who 5h reading is ${age%.*}s old (> ${max_age}s); refresh cswap and retry"
fi

if [ "$used" -ge "$max_pct" ]; then
  refuse "active account $who 5h window is ${pct}% used (threshold ${max_pct}%); $reset_text. Not switching accounts: wait for the reset or switch by hand."
fi

echo "CSWAP GATE ALLOW: active account $who 5h window is ${pct}% used (< ${max_pct}%); $reset_text"
exit 0
