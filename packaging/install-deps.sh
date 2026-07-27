#!/usr/bin/env bash
# Install external data-source tools that aiuse shells out to (or hits via loopback).
#
# Collectors (all optional at runtime if disabled in services.yaml, but this
# script installs the full set for multi-source cross-checks):
#
#   cswap      — multi-account Claude (uv tool: claude-swap)
#   codexbar   — multi-provider quotas (Homebrew cask CodexBar)
#   caut       — multi-provider CLI peer (cargo install from GitHub)
#   openusage  — menu bar + loopback :6736 (Homebrew cask OpenUsage)
#   tokscale   — independent quota JSON (npx tokscale wrapper on PATH)
#
# Preferred on this operator's Macs (also installs caut + OpenUsage):
#   just -f ~/ops/site-djbclark/justfile install-aiuse-deps
#
# Usage:
#   ./packaging/install-deps.sh
#   ./packaging/install-deps.sh --check   # report only, exit 1 if any missing
set -euo pipefail

CHECK_ONLY=0
if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=1
fi

have() { command -v "$1" >/dev/null 2>&1; }

ok()   { printf '  ok       %s\n' "$*"; }
miss() { printf '  MISSING  %s\n' "$*"; }
note() { printf '  note     %s\n' "$*"; }

ensure_local_bin() {
  mkdir -p "${HOME}/.local/bin"
  case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *) export PATH="${HOME}/.local/bin:${PATH}" ;;
  esac
}

install_cswap() {
  if have cswap; then
    ok "cswap → $(command -v cswap)"
    return 0
  fi
  if (( CHECK_ONLY )); then miss "cswap (uv tool install claude-swap)"; return 1; fi
  if have uv; then
    uv tool install claude-swap
  elif have pipx; then
    pipx install claude-swap
  else
    echo "error: need uv or pipx to install claude-swap (cswap)" >&2
    return 1
  fi
  # uv tools often land in ~/.local/share/uv/tools/.../bin — ensure PATH link
  if ! have cswap && [[ -x "${HOME}/.local/share/uv/tools/claude-swap/bin/cswap" ]]; then
    ln -sfn "${HOME}/.local/share/uv/tools/claude-swap/bin/cswap" "${HOME}/.local/bin/cswap"
  fi
  have cswap && ok "cswap → $(command -v cswap)" || { miss "cswap after install"; return 1; }
}

install_codexbar() {
  if have codexbar; then
    ok "codexbar → $(command -v codexbar)"
    return 0
  fi
  if (( CHECK_ONLY )); then miss "codexbar (brew install --cask codexbar)"; return 1; fi
  if ! have brew; then
    echo "error: Homebrew required for CodexBar cask" >&2
    return 1
  fi
  brew install --cask codexbar
  have codexbar && ok "codexbar → $(command -v codexbar)" || {
    miss "codexbar (open CodexBar.app once; CLI is CodexBarCLI)"
    return 1
  }
}

caut_macos_trust_hint() {
  # Cargo caut is adhoc-signed; Keychain Always Allow needs a stable identity.
  # Default: print a one-line hint. Opt-in autosign: AIUSE_AUTOSIGN_CAUT=1.
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi
  if [[ "${AIUSE_AUTOSIGN_CAUT:-}" == "1" ]] && have aiuse; then
    if aiuse trust sign-caut 2>/dev/null; then
      ok "caut codesign via aiuse trust sign-caut (AIUSE_AUTOSIGN_CAUT=1)"
    else
      note "aiuse trust sign-caut failed or identity missing — run: aiuse trust setup"
    fi
    return 0
  fi
  note "macOS Keychain: after a stable Code Signing cert exists, run: aiuse trust sign-caut"
  note "  first time: aiuse trust setup  (docs/macos-keychain-trust.md)"
}

install_caut() {
  if have caut; then
    ok "caut → $(command -v caut)"
    caut_macos_trust_hint
    return 0
  fi
  if (( CHECK_ONLY )); then
    miss "caut (cargo install --locked --git https://github.com/Dicklesworthstone/coding_agent_usage_tracker)"
    return 1
  fi
  if ! have cargo; then
    echo "error: cargo/Rust required for caut (brew install rust)" >&2
    return 1
  fi
  cargo install --locked --git https://github.com/Dicklesworthstone/coding_agent_usage_tracker
  ln -sfn "${HOME}/.cargo/bin/caut" "${HOME}/.local/bin/caut"
  if have caut; then
    ok "caut → $(command -v caut)"
    caut_macos_trust_hint
    return 0
  fi
  miss "caut after install"
  return 1
}

install_openusage() {
  local app="/Applications/OpenUsage.app"
  if have openusage; then
    ok "openusage CLI → $(command -v openusage)"
  elif [[ -d "${app}" ]]; then
    note "OpenUsage.app present; CLI not on PATH (Settings → Command Line → Install)"
  elif (( CHECK_ONLY )); then
    miss "OpenUsage.app (brew install --cask openusage)"
    return 1
  else
    if ! have brew; then
      echo "error: Homebrew required for OpenUsage cask" >&2
      return 1
    fi
    brew install --cask openusage
    note "OpenUsage.app installed — open it once; optional CLI: Settings → Command Line → Install"
  fi
  # Soft check: loopback API if app running
  if curl -fsS --max-time 2 "http://127.0.0.1:6736/v1/limits" >/dev/null 2>&1; then
    ok "OpenUsage HTTP http://127.0.0.1:6736/v1/limits responding"
  else
    note "OpenUsage HTTP :6736 not responding (open -ga OpenUsage when you want that collector)"
  fi
  # App presence is enough for install success
  if [[ -d "${app}" ]] || have openusage; then
    return 0
  fi
  miss "OpenUsage"
  return 1
}

install_tokscale() {
  if have tokscale; then
    ok "tokscale → $(command -v tokscale)"
    return 0
  fi
  if (( CHECK_ONLY )); then miss "tokscale (npx wrapper on PATH)"; return 1; fi
  if ! have npx && ! have npm; then
    echo "error: npm/npx required for tokscale (brew install node)" >&2
    return 1
  fi
  # Lightweight PATH shim — matches common operator setup (always latest).
  cat >"${HOME}/.local/bin/tokscale" <<'EOF'
#!/usr/bin/env bash
exec npx --yes tokscale@latest "$@"
EOF
  chmod +x "${HOME}/.local/bin/tokscale"
  have tokscale && ok "tokscale → $(command -v tokscale)" || { miss "tokscale"; return 1; }
}

main() {
  ensure_local_bin
  echo "aiuse external data sources"
  local failed=0
  install_cswap || failed=1
  install_codexbar || failed=1
  install_caut || failed=1
  install_openusage || failed=1
  install_tokscale || failed=1
  echo
  if (( failed )); then
    if (( CHECK_ONLY )); then
      echo "One or more tools missing (exit 1)."
    else
      echo "One or more installs incomplete (exit 1)."
    fi
    exit 1
  fi
  echo "All data-source tools present (or OpenUsage.app ready)."
  echo "Verify: aiuse doctor"
}

main "$@"
