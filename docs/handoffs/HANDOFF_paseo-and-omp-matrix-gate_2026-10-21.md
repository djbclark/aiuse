# Handoff: paseo collector + omp matrix rate gate (2026-10-21)

Resume point after herdr tab `Thu-4pm-Continue-t` died on a z.ai 5-hour
limit mid-edit (`muse-nope` → `muse` in `tests/test_paseo.py`). That work
is committed. The operator added a live omp 429 the same afternoon; that
is committed too. Neither is released. Installed Homebrew `aiuse` is still
**3.9.0**, which draws the old matrix. Smoke the working tree with
`AIUSE_SKIP_HOMEBREW_FORMULA=1`.

## What landed

1. **`e1705d2` — paseo collector.** `paseo usage ls --json` (daemon
   five-minute cache, no `--refresh`) is a second source for claude, codex,
   copilot, grok, and antigravity, behind the direct collectors. Cursor,
   omp, opencode, pi, and muse are skipped on purpose. Docs:
   [`docs/paseo.md`](../paseo.md). Default-on, quiet when the binary is
   absent. `--no-paseo`.
2. **`c65e51a` — matrix shows the omp rate gate.** `--json` / `--available`
   already omitted omp while the shared Claude weekly window is exhausted
   (schema 1.4). The clock matrix still drew it as `mid` with percent left
   and `$95.57` in OTHER, which is what got tried. A gated row is now
   `empty`, clocks blank, note `rate-gated until <reset> · $<left> left`.
   `burn_before_reset` stays off while the gate holds.

## The 429

2026-10-21 13:06 EDT, omp call:

```text
429 rate_limit_error "This request would exceed your account's rate limit."
retry-after-ms=147469000
```

147469 seconds is ~41h, which lands on the Claude weekly reset **Fri Oct 23
6:00am** (`2026-10-23T10:00:00Z`). The cached snapshot at 17:02Z already had
that window at 0% left. Admin API reading: **$95.57 of $200** left, expires
2026-11-08. omp is not spendable until that weekly reset, dollars
notwithstanding.

## Still open

- No release. `just release` only when the operator asks. Until then
  `aiuse` on PATH is the 3.9.0 formula and still shows the old omp row.
- cswap re-login remains the operator's browser step from the earlier
  2026-10-21 handoff. Claude windows in this snapshot came from cswap and
  were fresh enough for the gate.
- Left unstaged on purpose: `.codex/config.toml` and untracked
  `opencode.json` (local graft MCP client config, not this work).
