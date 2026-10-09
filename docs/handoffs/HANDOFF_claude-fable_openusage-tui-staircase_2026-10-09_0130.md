# Handoff — OpenUsage CLI shadowing left the terminal raw (2026-10-09)

**Session:** Claude Code (Fable 5.1), `~/src/aiuse`. Follows
[`HANDOFF_zcode-f31d_disabled-services-grok_2026-10-09_0110.md`](HANDOFF_zcode-f31d_disabled-services-grok_2026-10-09_0110.md).
Bead: `aiuse-dtm` (fixed), follow-up `bd list | grep "Upstream: openusage.sh"`.

## Symptom

`aiuse` printed its report as a staircase: every line began where the previous
one ended (screenshot from the operator, Ghostty). The shell prompt afterwards
was fine because bash resets the terminal when it prints a prompt.

## Root cause (reproduced deterministically)

1. Running `aiuse` under a fresh pty (`scratchpad/pty_repro.py`) showed the
   pty's termios flip at ~0.65 s to `oflag=2 lflag=43` — OPOST, ECHO, ICANON,
   ISIG and IEXTEN cleared, the `cfmakeraw` signature — and 36 of 37 report
   newlines then arrived without a carriage return.
2. `/opt/homebrew/bin/openusage` (Homebrew formula `openusage` 0.25.0, the
   **openusage.sh** Go/bubbletea dashboard, linked Sep 9) sits ahead of
   `/usr/local/bin/openusage` (→ `/Applications/OpenUsage.app/Contents/Helpers/openusage`,
   the **OpenUsage.ai** JSON CLI). `collectors/openusage.py` used
   `which("openusage")`.
3. Run with no subcommand, the Go binary opens `/dev/tty` directly (stdin was
   already `DEVNULL`), enters raw mode, and never exits. Probed three times:
   raw at 0.2 s, hang until the 90 s harness limit. `openusage --force` exits
   at once (unknown flag).
4. Why it looked like a recent regression: 3.2.8 added the antigravity
   `QueryGate` (15 min). While that gate is closed the collector withholds
   `--force`, so the plain invocation (the TUI) only happens on some runs —
   it is killed at the collector timeout, and the raw terminal survives into
   the report. The existing `cli.main` save/restore only ran at process exit,
   after rendering.

## Fix (this session)

- `collectors/base.py`: `which_all` (every same-named executable on PATH in
  order, plus extra paths, deduped by realpath), `first_tool(cmd, accept)`
  (first candidate an acceptor approves, plus the rejected ones), and
  `probe_output` (short detached probe). `run_json` now passes
  `start_new_session=True`, so children have no controlling terminal and
  cannot open `/dev/tty` at all.
- `collectors/openusage.py`: `is_app_cli` (bundle path, else `--help` banner
  probe; cached per path), `resolve_app_cli` / `app_cli_path` /
  `foreign_openusage_binaries`; `_fetch_limits` and `_fetch_limits_gated` use
  the verified path, never a foreign binary.
- `collectors/runner.py`: `run_collectors` saves/restores termios around
  collection (before rendering); `collector_tools_present` uses the verified
  path.
- `tty.py`: snapshot covers stdin, else stdout, else stderr; token is
  `(fd, attrs)`.
- `cli.py` doctor: warns which same-named binaries were skipped and which CLI
  is used (only in real-PATH mode, i.e. no `which_fn` stub and `probe=True`).
- Tests: `tests/test_openusage_cli_resolution.py` (new),
  `test_base_run_json.py`, `test_tty.py`, `test_query_throttle.py`.
- Docs: `docs/collectors-caut-openusage.md` (section after the openusage.sh
  install block).

## Outcome (2026-10-09, same session)

1. `brew unlink openusage`: the operator declined (GUI works, no need).
2. Upstream issue filed: <https://github.com/janekbaraniewski/openusage/issues/405>
   (no existing issue or PR covered it; bead `aiuse-iyg` closed). Offered a PR.
3. Released as **3.3.1** via `just release 3.3.1` (PyPI + GitHub release +
   Homebrew tap). The recipe's final `brew test` then hung for five minutes:
   `aiuse --version` sat in state `T`. **Regression in 3.3.1:** the exit-time
   `restore_stdin_tty` now also covers stdout/stderr, and `brew test` (like
   `aiuse … &` under job control) runs the process in a background process
   group with stderr on the tty, so `tcsetattr` raised SIGTTOU and stopped
   it. 3.3.0 only looked at stdin, so it never wrote. Fixed by comparing the
   current attrs with the snapshot first (no-op when unchanged) and writing
   only when `os.tcgetpgrp(fd) == os.getpgrp()`; reproduced with
   `scratchpad/bgjob_repro.py` (pty + `bash -i -c 'aiuse --version & wait'`:
   3.3.1 "job stopped", working tree clean). Shipped as **3.3.2**.
