# CodexBar debug logging and Debug screen

When a CodexBar row, widget file or refresh misbehaves, turn on its file log
before guessing. Found 2026-11-21 while diagnosing the 0.85.0 fork build.

- **Turn it on:** CodexBar > Settings > Debug > Logging > "Enable file logging".
  The log is `~/Library/Logs/CodexBar/CodexBar.log` (the "Open log file" button
  reveals it). Verbosity levels, quietest last: `trace`, `verbose` (the
  default), `debug`, `info`, `warning`, `error`, `critical`. Use `trace`
  only for a specific hunt; it is the noisiest.
- **CLI:** `codexbar usage --verbose` and `--log-level` give the same
  categories on stderr without the app.
- **Read it:** lines are `[time] [LEVEL] com.steipete.codexbar.<category>:`.
  Useful categories: `antigravity` (every request and parse attempt),
  `token-cost`, `keychain-preflight`, `cookie-cache`, `subprocess`,
  `openai-web`, `memory-pressure`, and `app` (refresh-cycle timing).
- **Stale `widget-snapshot.json`:** the app-group write runs under a 10 s
  bounded-I/O guard (`WidgetSnapshotStore`). If one write times out, for example
  while macOS gates the app's access to the app-group folder, a circuit breaker
  disables all container access **for that process until it restarts**, and the
  only trace is one WARNING, "Widget snapshot I/O timed out; disabling container
  access for this process". A plain write error logs nothing. So enable the file
  log **before** launching the app, or the one warning is missed. aiuse does not
  read that file; it reads the CLI and HTTP server.
- **Other Debug screen sections that help:** Fetch strategy attempts (which
  source each provider tried and why it fell through), Probe logs (raw provider
  probe output), CLI paths (the `PATH` the app resolved), Caches (clear the
  cookie and cost caches), Keep CLI sessions alive / Reset CLI sessions.
  Error and notification simulation are for UI testing only.
- **CodexBar's own docs:** `docs/DEVELOPMENT.md` in the CodexBar repo covers the
  file log (lines 34-35 and 134).

Never paste the log or the Probe logs into an issue unreviewed: they can hold
account emails and paths.
