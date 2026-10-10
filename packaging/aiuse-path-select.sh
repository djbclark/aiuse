#!/usr/bin/env bash
# Prefer Homebrew aiuse when that install contains the caam collector.
# Otherwise run the pipx commands installed with --suffix=-src
# (aiuse-src / ai-src): an editable install of this checkout, which
# includes aiuse.collectors.caam.
#
# Does not invoke brew. A Homebrew lock must not stall the sampler.
set -euo pipefail

name=$(basename "$0")
pipx_bin="${HOME}/.local/bin/${name}-src"

shopt -s nullglob
for prefix in /opt/homebrew/opt/aiuse /usr/local/opt/aiuse; do
  [[ -x "${prefix}/bin/${name}" ]] || continue
  matches=("${prefix}"/libexec/lib/python*/site-packages/aiuse/collectors/caam.py)
  if [[ ${#matches[@]} -gt 0 ]]; then
    exec "${prefix}/bin/${name}" "$@"
  fi
done
shopt -u nullglob

if [[ -x "${pipx_bin}" ]]; then
  exec "${pipx_bin}" "$@"
fi

echo "${name}: Homebrew has no collectors/caam.py and ${pipx_bin} is missing" >&2
exit 127
