#!/usr/bin/env bash
# Headless repro of file-based keychain ACL / partition-list behaviour.
# Works ONLY on a throwaway keychain it creates; never reads or writes the
# login keychain. Restores the user keychain search list on exit (create-keychain
# adds the new keychain to it). No step reads an item through a client that is
# not already trusted, so no GUI prompt appears.
# Usage: bash docs/research/macos-keychain-acl-repro.sh   (macOS only)
set -euo pipefail
[[ "$(uname)" == Darwin ]] || { echo "macOS only"; exit 2; }

work="$(mktemp -d "${TMPDIR:-/tmp}/kc-repro.XXXXXX")"
kc="$work/repro.keychain-db"
kcpw="repro-$(uuidgen)"            # throwaway keychain password, never reused
orig_list="$(security list-keychains -d user | tr -d '"' | xargs)"

cleanup() {
  # shellcheck disable=SC2086
  security list-keychains -d user -s $orig_list || true
  security delete-keychain "$kc" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

# Feed commands to `security -i` on stdin so secrets and passwords stay off argv
# (visible to every same-user process via ps). Tokenised like a shell line.
# Every call is bounded: a step that unexpectedly needs a SecurityAgent dialog
# times out instead of hanging (killing the client also dismisses the dialog).
sec_i() { printf '%s\n' "$@" | timeout 15 security -i; }

acl() {  # print trusted apps + partition list of one item, no secret
  security dump-keychain -a "$kc" | awk -v S="\"svce\"<blob>=\"$1\"" '
    /^keychain: /{p=0} index($0,S){p=1}
    p && (/^ +[0-9]+: \//||/requirement:/||/partition_id/||/description: (apple|teamid|cdhash|unsigned)/||/applications: <null>/)' |
    sed -E 's/^ +/  /' | cut -c1-150
}

security create-keychain -p "$kcpw" "$kc"
# shellcheck disable=SC2086
security list-keychains -d user -s $orig_list   # take it back out of the search list at once
security set-keychain-settings "$kc"           # no auto-lock timeout during the run
sec_i "unlock-keychain -p $kcpw $kc"

echo "== T1 item created by /usr/bin/security with no -T (what gh, Claude Code, go-keyring do)"
sec_i "add-generic-password -s t1 -a acct -w secret-v1 $kc"
acl t1

echo "== T2 item created with explicit -T: Apple binary + ad-hoc binary"
adhoc="$(command -v gh || true)"; [[ -n "$adhoc" ]] || adhoc=/bin/ls
sec_i "add-generic-password -s t2 -a acct -w s2 -T /bin/ls -T $adhoc $kc"
acl t2

echo "== T3 rotate secret in place with -U (no -T): does the ACL survive?"
sec_i "add-generic-password -s t1-acl -a acct -w v1 -T /usr/bin/security -T /bin/ls $kc"
before="$(acl t1-acl)"
sec_i "add-generic-password -U -s t1-acl -a acct -w v2 $kc"
after="$(acl t1-acl)"
echo "$after"
[[ "$before" == "$after" ]] && echo "RESULT T3: ACL unchanged by -U" || echo "RESULT T3: ACL CHANGED by -U"
[[ "$(timeout 15 security find-generic-password -s t1-acl -a acct -w "$kc")" == v2 ]] && echo "RESULT T3: secret rotated to v2, no prompt"
# Not run: "-U ... -T <app>" asks to rewrite the ACL, which needs the item's
# change_acl right (applications (0) = always ask), so SecurityAgent prompts
# for the keychain password. Observed 2026-10-09 on macOS 27.0.1.

echo "== T4 partition-list change without touching the secret or app list"
apps_before="$(acl t1 | grep -v -e partition -e 'description:')"
sec_i "set-generic-password-partition-list -S apple-tool:,teamid:ABCDE12345 -s t1 -a acct -k $kcpw $kc" >/dev/null
acl t1
apps_after="$(acl t1 | grep -v -e partition -e 'description:')"
[[ "$apps_before" == "$apps_after" ]] && echo "RESULT T4: trusted-app list unchanged"
# On macOS 27.0.1 a keychain made by create-keychain holds version-256 records
# with no partition_id entry, and the command above exits 0 without adding one;
# login-keychain records are version 512 and carry one. Report what we see.
if security dump-keychain -a "$kc" | grep -q 'partition_id'; then
  echo "RESULT T4: partition entry present on throwaway keychain"
else
  echo "RESULT T4: NO partition entry on throwaway keychain (record version: $(security dump-keychain "$kc" | awk '/^version:/{print $2; exit}')); partition behaviour needs the login keychain"
fi
[[ "$(timeout 15 security find-generic-password -s t1 -a acct -w "$kc")" == secret-v1 ]] && echo "RESULT T4: secret intact"

echo "== T5 'allow all applications' (-A): what broadened looks like"
sec_i "add-generic-password -A -s t5 -a acct -w s5 $kc"
acl t5
echo "== T5 rollback: narrow back by recreate (no CLI edits a trusted-app list in place)"
v="$(timeout 15 security find-generic-password -s t5 -a acct -w "$kc")"
timeout 15 security delete-generic-password -s t5 -a acct "$kc" >/dev/null
sec_i "add-generic-password -s t5 -a acct -w $v -T /usr/bin/security $kc"; v=
acl t5
echo "DONE"
