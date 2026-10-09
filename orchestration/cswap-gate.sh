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

# The active account is the row flagged `active` or the row numbered
# `activeAccountNumber`. Both must point at the same single row: if the flag
# and the number disagree, or several rows match, the gate cannot tell whose
# window applies and refuses. A missing or non-numeric activeAccountNumber
# matches nothing (it used to match an unnumbered row via null == null).
selection=$(jq -ce '
  . as $root
  | (if (.accounts | type) == "array" then .accounts else [] end) as $rows
  | ($root.activeAccountNumber | if type == "number" then . else null end) as $n
  | [range(0; $rows | length)
     | select($rows[.].active == true or ($n != null and $rows[.].number == $n))] as $hits
  | {count: ($hits | length), numbers: [$hits[] | $rows[.].number],
     account: (if ($hits | length) == 1 then $rows[$hits[0]] else null end)}' <<<"$listing" 2>/dev/null) ||
  refuse "cswap list shows no active account (its output is not a JSON object)"
matches=$(jq -r .count <<<"$selection")
[ "$matches" != 0 ] || refuse "cswap list shows no active account"
[ "$matches" = 1 ] ||
  refuse "cswap list marks $matches accounts as active (numbers $(jq -c .numbers <<<"$selection"), activeAccountNumber $(jq -c '.activeAccountNumber' <<<"$listing")); the active flag and number disagree, so the gate cannot tell whose 5h window applies"
account=$(jq -ce '.account | select(type == "object")' <<<"$selection") ||
  refuse "cswap list's active account entry is not an object"

field() { jq -r "$1" <<<"$account"; }
who="#$(field '.number // "?"') $(field '.email // "unknown"')"
status=$(field '.usageStatus // "missing"')
[ "$status" = ok ] || refuse "active account $who usage status is '$status'; cannot read its 5h window"

pct=$(field '.usage.fiveHour.pct // empty')
[ -n "$pct" ] || refuse "active account $who has no 5h window reading"
# Validate with a regex before any arithmetic. `[ x -ge y ]` on a non-integer
# exits 2, which an `if` reads as false, so a "NaN", 1e400 or negative pct
# used to fall through to ALLOW. Only a plain decimal from 0 to 100 passes.
[[ $pct =~ ^[0-9]{1,3}(\.[0-9]+)?$ ]] ||
  refuse "active account $who has an unparsable 5h percent '$pct' (want a plain number from 0 to 100)"
used=$((10#${pct%%.*}))
[ "$used" -le 100 ] || refuse "active account $who 5h percent '$pct' is above 100"
[[ $pct =~ ^([0-9]+)\.0+$ ]] && pct=$((10#${BASH_REMATCH[1]}))
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

# ALLOW only on a positive numeric test; anything else, including a test that
# errors out, falls through to REFUSE.
if [[ $used =~ ^[0-9]+$ ]] && [ "$used" -lt "$max_pct" ]; then
  echo "CSWAP GATE ALLOW: active account $who 5h window is ${pct}% used (< ${max_pct}%); $reset_text"
  exit 0
fi
refuse "active account $who 5h window is ${pct}% used (threshold ${max_pct}%); $reset_text. Not switching accounts: wait for the reset or switch by hand."
