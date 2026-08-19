#compdef aiuse ai
# zsh completion for aiuse (djbclark/aiuse)
# Install: source this file, or:
#   eval "$(aiuse --print-completion zsh)"

_aiuse() {
  local -a opts
  opts=(
    '--help[show help]'
    '--version[show version]'
    '(-c --config)'{-c,--config}'[services.yaml path]:path:_files'
    '--show-config-path[print default config paths]'
    '--generate-config[write default config files]'
    '--doctor[environment check (also: aiuse doctor)]'
    '--status[one-line status for prompts (also: aiuse status / prompt)]'
    '--suggest[single best burn pool (also: aiuse suggest)]'
    '--serve[loopback HTTP API for agents (also: aiuse serve)]'
    '--watch[full-screen quota board (also: aiuse watch)]'
    '(-i --interval)'{-i,--interval}'[watch refresh interval]:interval'
    '--once[watch: one frame then exit]'
    '--port[serve port]:port'
    '--max-age[serve cache max age seconds]:seconds'
    '--print-completion[print shell completion]:shell:(bash zsh)'
    '(-t --timeout)'{-t,--timeout}'[force tool timeout seconds]:seconds'
    '--format[output format]:format:(pretty json)'
    '--json[JSON output]'
    '--no-color[disable ANSI colors]'
    '--no-tui[force classic plain-text report]'
    '(-q --quiet)'{-q,--quiet}'[suppress stderr progress]'
    '--alerts-only[recommendations only]'
    '--brief[alias of default priority-ladder report]'
    '--full[long report with per-provider detail]'
    '--no-tokscale[skip tokscale]'
    '--no-cswap[skip cswap]'
    '--no-codexbar[skip codexbar]'
    '--providers[CodexBar providers]:providers'
    '--min-remaining[min remaining percent]:percent'
    '--max-days[max days until reset]:days'
    '--save[write JSON snapshot]:path:_files'
    '--traditional-summary[legacy flat summary]'
    'doctor:environment check'
    'status:one-line status for prompts/status bars'
    'prompt:synonym of status'
    'suggest:single best burn pool next'
    'serve:loopback HTTP API for agents'
    'watch:full-screen quota board (q/esc quit)'
  )
  _arguments -s -S $opts
}

compdef _aiuse aiuse ai
