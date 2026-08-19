# Watch mode: opt-in full-screen monitor

**Date:** 2026-08-18
**Status:** Design (refines [Issue #14](https://github.com/djbclark/aiuse/issues/14)); not yet implemented.
**Related:** [`pretty-display.md`](pretty-display.md), [`collector-concurrency.md`](collector-concurrency.md), [`companion-stack.md`](companion-stack.md), [`scheduling.md`](scheduling.md), [Issue #14](https://github.com/djbclark/aiuse/issues/14)

## Summary

`aiuse watch` opens a **full-screen, alternate-screen** monitor (htop /
`cswap watch` style) that redraws the clock matrix on a CLI-settable interval
and exits on `q` / `Esc` / `Ctrl-C`. No other interactivity.

This refines Issue #14, which originally scoped a scrollback-preserving
clear-and-redraw TTY pull loop. The operator (2026-08-18) wants the
full-screen variant: an explicitly invoked, viewport-oriented surface, which is
a different beast from the default stdout report that
[`pretty-display.md`](pretty-display.md) keeps scrollback-safe.

## Requirements (operator-stated, 2026-08-18)

- Full-screen display (alternate screen; on quit, scrollback is untouched).
- Refresh interval is a CLI-settable frequency; **default 10 minutes** (600s).
- Non-interactive except: `q`, `Esc`, and `Ctrl-C` exit cleanly.
- Along the lines of `cswap watch` / `htop`, but no menus, no selection, no
  cursor — just a read-only auto-refreshing board.

## CLI surface

`aiuse watch` as a word command, matching `doctor` / `serve` / `trust` /
`suggest` / `status` / `schema`. Flags:

| Flag                       | Default | Notes                                                             |
| -------------------------- | ------- | ----------------------------------------------------------------- |
| `-i`, `--interval SECONDS` | `600`   | Refresh cadence; `>0`. Suffixes `10m` / `90s` accepted.           |
| `--once`                   | off     | Collect + render a single frame and exit (scripts / tmux status). |
| `-q` / `--quiet`           | off     | Suppress the capacity / detail blurb inside the board.            |
| `--no-color`               | off     | Honor `NO_COLOR` as everywhere.                                   |
| `-t` / `--timeout`         | config  | Per-collector timeout for the run.                                |
| `--no-tui`                 | —       | Error: watch requires a TTY; do not silently fall back.           |

`--json` / `--alerts-only` / `--for-chat` / `--flatten` are incompatible with
`watch` → exit `2` with a clear message.

## Architecture

```
aiuse watch
 └─ enter alternate screen (Rich Live, screen=True) or Textual app
     ├─ tick loop (interval)
     │    ├─ run_collectors (ThreadPoolExecutor, 6 collectors, ≤45s)  ← background thread
     │    ├─ render_clock_matrix(snapshot, alerts)  ← reused, unchanged
     │    └─ redraw board (header + matrix + footer)
     ├─ key poll (non-blocking stdin): q / Esc / Ctrl-C → restore + exit 0
     └─ SIGINT handler → same restore + exit 0
```

### Collection scheduling

Collect is **wall-clock 5–20s warm, up to 45s cold** (six external subprocesses;
see [`collector-concurrency.md`](collector-concurrency.md)). Rules:

1. **Never overlap collections.** The next tick starts only after the previous
   collect + render finishes. If a collect runs longer than the interval, the
   next tick fires immediately (no skip, no queue).
2. **Background the collect.** The UI thread renders the last-good snapshot
   while a worker collects; the board shows `collecting… (Ns)` so the user
   knows it is not frozen.
3. **Staleness is shown.** Header: `last: HH:MM:SS · next in M:SS · q/esc quit`.
4. **Floor the interval.** Warn if `< 30s` (sub-collect-time intervals starve
   the six external tools). Hard floor at `5s` to prevent abuse.
5. **Honor `persist_snapshots`.** If snapshot persistence is enabled in config,
   each watch tick also densifies History (same as the hourly LaunchAgent), so
   watching = denser history for free.

### Library choice (design fork — decide before coding)

| Option                                                   | New deps                         | Fit  | Notes                                                                                                                                                                                               |
| -------------------------------------------------------- | -------------------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Rich `Live(screen=True)` + non-blocking stdin reader** | **None** (rich is already a dep) | Good | Reuses `render_clock_matrix` directly as the Live renderable; `q`/`esc` via a tiny `termios` / `select` key poller in a thread. ~50–100 LoC for the loop. Honest fit for a _non-interactive_ board. |
| **Textual app**                                          | textual (new dep tree)           | Best | Matches `cswap watch` exactly (its `WatchScreen` uses `Binding("escape,q", "back")`); built-in timers + resize + alt-screen. Heavier; only worth it if interactivity grows later.                   |

**Recommendation:** start with **Rich `Live`** — zero new dependencies, reuses the
existing renderer, and the feature is deliberately non-interactive (so Textual's
binding/widget machinery is overkill). Revisit Textual only if a later issue
adds menus / selection to the watch board. Record the choice in
[`pretty-display.md`](pretty-display.md) (the no-Textual rule there is scoped to
the _default stdout report_; an opt-in full-screen surface is exempt).

## Display policy

- Reuse `aiuse.report.render_clock_matrix` unchanged (same bands, clocks, width
  adaptation, color roles). The watch board is just that matrix inside a
  full-screen frame plus a header / footer.
- `render_stderr_meta` content (collection time, capacity blurb, `Detail: ai --full`)
  becomes a **footer line inside the screen**, not stderr (stderr would corrupt
  the alternate-screen layout). `-q` suppresses it.
- `NO_COLOR` / `--no-color` honored; `FORCE_COLOR` / `TTY_COMPATIBLE` gate whether
  the styled render is used (mirror `aiuse.tui.should_use_tui`).
- On resize, Rich `Live` re-flows automatically; the matrix width logic already
  adapts to `terminal_width()`.

## Exit behavior

- `q`, `Esc`, `Ctrl-C` (SIGINT) → restore terminal (disable raw mode, leave
  alternate screen), exit `0`. Watch is a UI; it never returns collect exit
  codes.
- Non-TTY stdout (`!stdout.isatty()` and not `FORCE_COLOR`) → exit `2` with
  `aiuse watch requires an interactive terminal` on stderr. (Suggest
  `aiuse --json` or the hourly LaunchAgent for non-interactive monitoring.)
- Unknown flag → argparse default error path.

## Tests (pytest; mirror `tests/test_tui.py` style)

- interval parsing: `600`, `10m`, `90s`, `0` (reject), `5` (warn), negative
  (reject).
- `--once` collects once and exits without entering the loop (mock
  `run_collectors`).
- Non-TTY rejection path (monkeypatch `stdout.isatty`).
- incompatible-flags (`--json` + `watch`) → exit `2`.
- key dispatch: inject a fake key reader; `q` / `\x1b` / `\x03` all return the
  "quit" sentinel and the loop exits cleanly. Factor the key reader so it is
  injectable — do not call `termios` directly in the unit under test.
- background collect does not overlap (two rapid ticks with a slow mocked
  collect: assert the second waits).
- snapshot persists on tick when `persist_snapshots` enabled (mock the
  snapshot writer).

## Docs / acceptance (adapted from Issue #14)

- [ ] README: `aiuse watch` in the usage section + one screenshot / asciinema.
- [ ] [`companion-stack.md`](companion-stack.md): "compose with CodexBar /
      OpenUsage for a true always-on menubar; `aiuse watch` is the in-terminal
      board."
- [ ] [`pretty-display.md`](pretty-display.md): amended with the watch-mode
      exception (done in this commit).
- [ ] [`scheduling.md`](scheduling.md): note that watching densifies History
      when `persist_snapshots` is on.
- [ ] `completions/aiuse.bash` + `aiuse.zsh`: add `watch` and `--interval`.
- [ ] [`json-contract.md`](json-contract.md): `watch` does not emit JSON; note
      that scripts wanting data should use `aiuse --json` or `aiuse serve`.

## Estimate

Rich `Live` path: **~4–8h** (the renderer exists; the work is the loop, key
polling, terminal restore, tests, docs, completions). Matches Issue #14's
`4–12h` band. The Textual path would push toward the high end / above.

## When this doc should change

- Library choice finalized (Rich Live vs Textual) → record under "Library
  choice" with the decided option and date.
- After implementation ships → flip Status to "Shipped in vX.Y.Z" and trim the
  design options to the chosen one.
- If interactivity is later added (selection, switch) → re-open the Textual
  question.
