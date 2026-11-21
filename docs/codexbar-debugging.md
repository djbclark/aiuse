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
- **Stale `widget-snapshot.json` on a fork (ad-hoc) build:** the kernel log shows
  `Sandbox: System Policy: CodexBar deny file-write-create .../Group Containers/<team>.com.steipete.codexbar/widget-snapshot.json`
  and securityd says the `application-groups` entitlement "is ignored because of invalid application signature". A team-prefixed
  group is only valid when the app is signed by that team, and an ad-hoc signature has none, so every widget write is denied
  and CodexBar logs nothing (Trace adds nothing on this path). Find it with
  `log show --last 5m --predicate 'process == "kernel" AND eventMessage CONTAINS "CodexBar"'`.
  Fix (verified 2026-11-21): re-sign the built app with an Apple Development identity, using the signature's real team ID.
  The team ID is the `TeamIdentifier` from `codesign -dvv`, not the id in the certificate's name (`R7NDWQV585` in the
  name, team `XWZC8G497C`). Steps: set `CodexBarTeamID` in the app and widget Info.plist; sign Sparkle's XPC services,
  Updater, Autoupdate, the framework, the helpers, then the widget (entitlements: app-sandbox plus group
  `<team>.com.steipete.codexbar`), then the app (group only). The widget file then appears in a new
  `<team>.com.steipete.codexbar` container. aiuse does not read that file; it reads the CLI and HTTP server.
- **Other Debug screen sections that help:** Fetch strategy attempts (which
  source each provider tried and why it fell through), Probe logs (raw provider
  probe output), CLI paths (the `PATH` the app resolved), Caches (clear the
  cookie and cost caches), Keep CLI sessions alive / Reset CLI sessions.
  Error and notification simulation are for UI testing only.
- **CodexBar's own docs:** `docs/DEVELOPMENT.md` in the CodexBar repo covers the
  file log (lines 34-35 and 134).

Never paste the log or the Probe logs into an issue unreviewed: they can hold
account emails and paths.
