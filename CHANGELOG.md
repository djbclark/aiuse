# Changelog

All notable changes to `aiuse`. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are plain
`X.Y.Z` and match the git tags, PyPI and Homebrew
([`docs/packaging.md`](docs/packaging.md)). The `--json` contract has its own
history in [`docs/json-contract.md`](docs/json-contract.md#changelog).

Releases before 3.9.0 are in the
[GitHub releases](https://github.com/djbclark/aiuse/releases) and the git log
(`git log v3.8.0`). From 3.12.1 on, every release adds an entry here.

## [3.12.1] - 2026-11-01

### Changed

- **Bedrock shows the total AWS account spend so far**, not month-to-date:
  `↑ $25.66 AWS total (as of …)`. The Cost Explorer query now spans the 13
  whole months before the current one plus the current month, which is as far
  back as the API serves. The new metadata field `bedrock_billing.since_month`
  names the earliest month with records
  ([`docs/bedrock-quota.md`](docs/bedrock-quota.md)).
- The Cursor on-demand remainder reads `↓$0.00 cap left` instead of
  `↓$0.00 prepaid`: it is a spend cap, not a wallet bought ahead. Other
  providers' prepaid wallets keep `prepaid`.

### Fixed

- Bedrock no longer shows `$0.00` when Cost Explorer has no cost records.
  AWS answers an empty period with `Amount: "0"`, most visibly on the first day
  of a month, and aiuse read that as a measured zero. An empty window is now
  `UNKNOWN`; a real zero-amount record is still a measured zero.
- The Bedrock cache logic no longer fabricates a `$0.00` row from an old
  `UNKNOWN` store or from a read that returned no rows; both stay unknown.

## [3.12.0] - 2026-11-01

### Added

- Gemini 4 Argon has its own `gemini_argon` pool family; JSON schema 1.5
  publishes the model-to-family map.

## [3.11.0] - 2026-10-29

### Fixed

- Bedrock shows overall AWS account month-to-date charges before credits,
  with no category breakdown, and keeps its collector source pin.
- omp grant pace is preserved, and Bedrock credits are separated from spend.
- The release script smokes the installed `watch` before other probes.

## [3.10.5] - 2026-10-29

### Fixed

- An unknown Bedrock total shows as `UNKNOWN`.

## [3.10.4] - 2026-10-29

### Added

- Dollar direction is named (`↓` left, `↑` spent), the header is compacted,
  and Bedrock Cost Explorer reads are gated (one billed read per six hours,
  and only after other usage moves).

### Fixed

- omp's monthly credit is paced from dollars left and the reset time.
- OpenCode Go and Zen share one vault read when collected together.

## [3.10.3] - 2026-10-29

### Fixed

- OpenCode: every Go API key is read for Go windows and the Zen balance.
- `watch` leaves the tty blocking so the first frame paints, and ignores
  trackpad scroll sequences instead of quitting.

## [3.10.2] - 2026-10-29

### Added

- CodexBar usage is read from its HTTP server when it answers `/health`;
  the CLI is the fallback.

### Fixed

- OpenCode Go sizes its monthly window from `access.startsAt`.
- Matrix note-only rows start at the first column and show oc-go on the sweep.
- History counts snapshots by filename, which shortens `watch` start.

## [3.10.1] - 2026-10-28

### Added

- SIPB collector option `ignore_expired_certificates`.

### Fixed

- Uninstalled optional collectors are expected in the `watch` footer.
- Frozen-quota detection scans each candidate's own span.
- Matrix rows pass the reference timestamp to window-clock inference.

## [3.10.0] - 2026-10-23

### Added

- ClinePass multi-account: one labelled row per account, with no false
  disagreement ([#41](https://github.com/djbclark/aiuse/issues/41)).

## [3.9.4] - 2026-10-22

### Added

- The omp rate gate is a durable fail-closed latch: evidence persists in
  `~/.local/state/aiuse/rate-gates.json`, Anthropic 429 retry-after is read
  from omp's own logs, and `aiuse gate status|clear` inspects it
  ([`docs/omp-cloud-credits.md`](docs/omp-cloud-credits.md)).

### Fixed

- The `watch` `a` sweep leaves deprecated collectors at their configured state.

## [3.9.3] - 2026-10-22

### Fixed

- A rate-gated omp summary line says `GATED`, and the gated omp row is stored
  in the snapshot cache.

## [3.9.2] - 2026-10-22

### Changed

- The default install is cswap, CodexBar and tokscale. caut, caam, OpenUsage.ai
  and OpenUsage.sh are deprecated for overlap; their code stays and still runs
  when the binary is present and the collector is enabled
  ([`docs/collectors-caut-openusage.md`](docs/collectors-caut-openusage.md)).

### Fixed

- An Antigravity source that still reports Gemini is kept.
- Windowless status rows are kept when a pool is hidden.

## [3.9.1] - 2026-10-21

### Added

- `paseo` collector: the Paseo daemon's cached usage report as a second source.

### Fixed

- A rate-gated omp matrix row is empty, not a spendable percent.
- Matrix OTHER shows omp dollars left only.

## [3.9.0] - 2026-10-21

### Added

- omp rate gate on the shared Claude account window (JSON schema 1.4), mirrored
  onto snapshot accounts so `--for-chat` shows it.

### Fixed

- Stale-window hygiene: reset-passed cached windows are dropped, old cswap
  hydration expires, and `watch` survives a `brew upgrade`.

[3.12.1]: https://github.com/djbclark/aiuse/compare/v3.12.0...v3.12.1
[3.12.0]: https://github.com/djbclark/aiuse/compare/v3.11.0...v3.12.0
[3.11.0]: https://github.com/djbclark/aiuse/compare/v3.10.5...v3.11.0
[3.10.5]: https://github.com/djbclark/aiuse/compare/v3.10.4...v3.10.5
[3.10.4]: https://github.com/djbclark/aiuse/compare/v3.10.3...v3.10.4
[3.10.3]: https://github.com/djbclark/aiuse/compare/v3.10.2...v3.10.3
[3.10.2]: https://github.com/djbclark/aiuse/compare/v3.10.1...v3.10.2
[3.10.1]: https://github.com/djbclark/aiuse/compare/v3.10.0...v3.10.1
[3.10.0]: https://github.com/djbclark/aiuse/compare/v3.9.4...v3.10.0
[3.9.4]: https://github.com/djbclark/aiuse/compare/v3.9.3...v3.9.4
[3.9.3]: https://github.com/djbclark/aiuse/compare/v3.9.2...v3.9.3
[3.9.2]: https://github.com/djbclark/aiuse/compare/v3.9.1...v3.9.2
[3.9.1]: https://github.com/djbclark/aiuse/compare/v3.9.0...v3.9.1
[3.9.0]: https://github.com/djbclark/aiuse/compare/v3.8.0...v3.9.0
