#!/usr/bin/env bash
# Generic (non-Ansible) installer for com.djbclark.aiuse.
#
# Preferred on this operator's Macs: manage via ~/ops/site-djbclark
#   cd ~/ops/site-djbclark && just site-agents-apply
#
# This script remains for machines without the site repo.
set -euo pipefail

LABEL="com.djbclark.aiuse"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="${REPO_ROOT}/packaging/launchd/${LABEL}.plist"
DEST_DIR="${HOME}/Library/LaunchAgents"
DEST="${DEST_DIR}/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/aiuse"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/aiuse"
CONFIG="${CONFIG_DIR}/config.toml"
LEGACY_SERVICES="${CONFIG_DIR}/services.yaml"

AIUSE_BIN="$(command -v aiuse || true)"
if [[ -z "${AIUSE_BIN}" ]]; then
  echo "error: aiuse not on PATH (install via pipx or brew first)" >&2
  exit 1
fi
AIUSE_BIN="$(cd "$(dirname "${AIUSE_BIN}")" && pwd)/$(basename "${AIUSE_BIN}")"

mkdir -p "${DEST_DIR}" "${LOG_DIR}" "${CONFIG_DIR}"

# Enable persist_snapshots without clobbering an existing canonical config.
if [[ -f "${LEGACY_SERVICES}" && ! -f "${CONFIG}" ]]; then
  echo "error: migrate ${LEGACY_SERVICES} to ${CONFIG} and remove the YAML file first" >&2
  exit 1
fi
if [[ ! -f "${CONFIG}" ]]; then
  printf '%s\n' '[analysis]' 'persist_snapshots = true' 'learn_from_history = "auto"' >"${CONFIG}"
  chmod 600 "${CONFIG}"
  echo "created: ${CONFIG} (persist_snapshots: true)"
else
  if grep -q '^[[:space:]]*persist_snapshots[[:space:]]*=' "${CONFIG}" 2>/dev/null; then
    # Leave explicit setting alone.
    :
  else
    if grep -q '^\[analysis\]$' "${CONFIG}"; then
      # Insert under the first analysis table.
      tmp="$(mktemp)"
      awk '
        BEGIN { done=0 }
        /^\[analysis\]$/ && !done {
          print
          print "persist_snapshots = true"
          done=1
          next
        }
        { print }
        END {
          if (!done) {
            print "[analysis]"
            print "persist_snapshots = true"
          }
        }
      ' "${CONFIG}" >"${tmp}"
      mv "${tmp}" "${CONFIG}"
      echo "updated: ${CONFIG} (added persist_snapshots: true)"
    else
      printf '\n[analysis]\npersist_snapshots = true\n' >>"${CONFIG}"
      echo "updated: ${CONFIG} (appended analysis.persist_snapshots)"
    fi
  fi
fi

# Render plist from template.
sed \
  -e "s|AIUSE_BIN|${AIUSE_BIN}|g" \
  -e "s|USER_LOCAL_BIN|${HOME}/.local/bin|g" \
  -e "s|LOG_DIR|${LOG_DIR}|g" \
  "${TEMPLATE}" >"${DEST}"
chmod 644 "${DEST}"

UID_NUM="$(id -u)"
# Unload if already present (best-effort).
launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/${UID_NUM}" "${DEST}"
launchctl enable "gui/${UID_NUM}/${LABEL}"
launchctl kickstart -k "gui/${UID_NUM}/${LABEL}" || true

echo "installed: ${DEST}"
echo "aiuse:     ${AIUSE_BIN}"
echo "logs:      ${LOG_DIR}/"
echo "interval:  1 hour (StartInterval 3600)"
echo "note:      exit 1 = hard failure; exit 2 = alerts present (collection ok)"
echo "next:      learn_from_history: auto turns learning on once >= 2 snapshots exist"
echo "           (see docs/history-learning.md)"
