# bash completion for aiuse (djbclark/aiuse)
# Install: source this file, or:
#   eval "$(aiuse --print-completion bash)"

_aiuse_completions() {
  local cur prev
  COMPREPLY=()
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD - 1]}"

  local opts="
    --help --version
    --config -c
    --show-config-path --generate-config --doctor --status --suggest --serve --print-completion
    -t --timeout --port --max-age
    --format --json --no-color --no-tui -q --quiet --alerts-only --brief --full
    --no-tokscale --no-cswap --no-codexbar
    --providers --min-remaining --max-days --save --traditional-summary
    -i --interval --once
    doctor status prompt suggest serve watch
  "

  case "${prev}" in
    --format)
      COMPREPLY=($(compgen -W "pretty json" -- "${cur}"))
      return 0
      ;;
    --print-completion)
      COMPREPLY=($(compgen -W "bash zsh" -- "${cur}"))
      return 0
      ;;
    --config | -c | --save)
      COMPREPLY=($(compgen -f -- "${cur}"))
      return 0
      ;;
    -t | --timeout | --min-remaining | --max-days | --providers)
      return 0
      ;;
  esac

  if [[ ${cur} == -* ]]; then
    COMPREPLY=($(compgen -W "${opts}" -- "${cur}"))
    return 0
  fi

  if [[ ${COMP_CWORD} -eq 1 ]]; then
    COMPREPLY=($(compgen -W "doctor ${opts}" -- "${cur}"))
    return 0
  fi

  COMPREPLY=($(compgen -W "${opts}" -- "${cur}"))
}

complete -F _aiuse_completions aiuse
complete -F _aiuse_completions ai
