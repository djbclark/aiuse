# Pretty display: Rich renderables, not Textual

**Decision (2026-07-24):** TTY pretty output uses **Rich** (non-`Layout`
renderables printed sequentially). Do **not** use Textual or Rich `Layout`
for the default report.

**Decision (2026-08-14):** the default stdout body is a **clock matrix**
(`render_clock_matrix`), replacing the priority ladder described by earlier
revisions of this file. See "Superseded: the priority ladder" below before
reintroducing anything ladder-shaped.

## Default stdout: the clock matrix

Default `aiuse` prints **one row per account/pool** and **one column per reset
clock**, so a column reads top to bottom as a like-for-like comparison:

```text
      ## SERVICE    ACCT     SCOPE           5H       WEEK      MONTH $ UNUSED
error ?? oc-go      —        —            no usage data
empty  0 codex      gmail    —                —   100%/5d         —    $0.00
slow  48 agy        gmail    gemini      0%/2h27m   23%/22h        —    $5.31
mid   54 cursor     gmail    other models     —        —     0%/19d   $20.00
use   92 claude     mit      —             25%/now  60%/2d14h      —    $2.76
  2d14h = until this clock resets · bold = largest unit
  dim % = clock inferred, not reported · + = >1 window on that clock, showing most-used
```

`##` is a colored **0–99 action score** aligned with the row order: `0` means
empty and `99` means use as soon as possible. Shared semantic thresholds keep
category transitions contiguous: `slow` is 25–49, `mid` is 50–74, and `use`
is 75–99. The ranges are not independently stretched. A safe-to-resume `slow`
row is 49 beside a weakest `mid` at 50; the strongest `mid` is 74 beside a
weakest active `use` recommendation at 75. An error prints `??` because its
urgency is unknown; non-expiring `n/a` inventory prints `--` because it has no
use-or-lose score. Those markers avoid falsely calling unavailable data or
rolling API credit empty.

The clocks are `CLOCK_COLUMNS` — `5H`, `WEEK`, `MONTH`. An **em-dash** means the
service has no window on that clock, which is itself the answer to "why does
this one only show a weekly?". That question is what the matrix exists to
answer: the previous ladder showed each row on whichever window happened to
govern it, so every row was measured on a different clock.

When a clock has a reset timestamp the cell is `used%/duration` for **that**
clock (`25%/now`, `60%/2d14h`), not a row-level "soonest" deadline. There is
no `NEXT` column. Duration is at most two integer units, zeros dropped,
concatenated (`2h27m`, `2d14h`); minutes only under one day, seconds only
under one minute; never a calendar month (`27d`, not `0.9m`). A clock with
no timestamp stays a bare percent (`0%`, not `0%/—`). The largest unit is
**bold** when color is on.

**Percentages are `used`, not left.** 0% is untouched, 100% is exhausted. The
header prints that legend every run rather than relying on the reader to
remember which way round it is.

Two markers qualify a cell:

- **dim** — the clock bucket was _inferred_ rather than reported. Several
  providers (antigravity, grok, deepseek, openrouter) never send a window
  duration, so `models.infer_window_clock()` falls back to the label text and
  then to distance-from-reset. Only the last of those is a guess, and only that
  one renders dim.
- **`+`** — more than one window lands on that clock; the cell shows the
  most-used one.

### Bands still tag every row

The leading tag column is unchanged from the ladder (`_BAND_TAG`):

1. **error** (red) — could not fetch usage for that provider/account
2. **empty** (red) — totally depleted
3. **n/a** (dim) — non-expiring prepaid / pay-as-you-go (no use-or-lose urgency)
4. **slow** (yellow) — conserve / pace yourself
5. **mid** (cyan) — on pace / advisory / no alert
6. **use** (green) — important to burn soon

The bands are fixed in that order and each is also a deliberate queue. Read
**bottom → top** within a band:

- **error** has no trustworthy usage signal, so ties stay deterministic by
  provider/account rather than inventing urgency.
- **empty** puts a known sooner refill lower; unknown/lapsed capacity stays
  higher.
- **n/a** stays explicitly non-urgent, but larger comparable count-down
  inventory sits lower than smaller inventory; spend-up PAYG remains neutral.
- **slow** puts the most severe projected early lockout higher and the pool
  closest to safe use lower.
- **mid** is a soft recommendation queue: analyzer scores win when present;
  otherwise remaining capacity multiplied by reset pressure ranks healthy
  pools, so distant full pools do not outrank near-reset capacity.
- **use** mirrors the canonical burn recommendation order (score, remaining
  capacity, then sooner reset), so the bottom row agrees with what is most
  worth using next.

The score uses shared action-state thresholds, while sorting retains the
category-specific evidence needed to order unlike states honestly. The matrix
uses the same queue keys as the retained priority-ladder renderer.

Collection time, capacity blurb, and `Detail: ai --full` go to **stderr**
(`render_stderr_meta`, suppressed with `-q`), so stdout stays pipeable.

### Width adaptation

`render_clock_matrix` sizes itself to `min(terminal_width(), TABLE_MAX_WIDTH)`
(110). A narrow terminal **sheds optional detail** rather than slicing a number
in half, in this order:

1. `$ UNUSED`
2. Duration compact to its largest unit (`2h27m` → `2h`)
3. `SCOPE` designed short tokens (`gemini` → `gem`, `claude/gpt` → `c/gpt`,
   `other models` → `oth`) — not a blind character slice
4. Drop `SCOPE` only if no service still needs it to tell two rows apart
5. Drop `ACCT` only if no service still needs it
6. Fold the leftover disambiguator into `SERVICE` (`agy/gem`, `claude/mit`)

The clock columns themselves never drop — they are the reason the table
exists. `SCOPE` is also omitted entirely when no account has independent
pools; `ACCT` is omitted when every row's account is `—`.

`terminal_width()` honors `$COLUMNS` and falls back to `ACTION_PLAN_WIDTH` when
stdout is not a tty. Tests pin `$COLUMNS` in `tests/conftest.py`; without that
the suite's width assertions silently read whatever terminal the developer had
open.

### Color for readability

- Semantic ANSI roles (red / dim / yellow / cyan / green), not decorative rainbows.
- Always pair color with a text tag (`empty` / `n/a` / `slow` / `mid` / `use`).
- Bold the tag + provider; keep secondary fields in the default face.
- Respect `NO_COLOR` / `--no-color`.

## `--full` and its two renderers

`--full` keeps the long report on stdout (including History when snapshots
exist). It does **not** embed the clock matrix. There are two `--full`
renderers and they do not share a width policy:

| Path          | Reached when               | Width                                         |
| ------------- | -------------------------- | --------------------------------------------- |
| Plain text    | not a tty, or `--no-tui`   | rules pinned at `ACTION_PLAN_WIDTH` (80)      |
| Rich / styled | a tty (normal interactive) | `console.width - _PANEL_CHROME`, **uncapped** |

The one place `--full` truncates is `_clamp_display_width` inside
`_render_brief_action_plan`, and only when the detailed plan exceeds
`ACTION_PLAN_MAX_LINES` (23) so the compact "at a glance" block is emitted. That
clamp takes `clamp_width` — the room the caller actually has — which is
deliberately **not** the same number as the width it draws section rules at. The
plain path passes `min(terminal_width(), TABLE_MAX_WIDTH)`; passing its
80-column rule width instead used to cut alert rows on a wide terminal.

`--brief` aliases the default report (so it is the matrix, not a ladder).

## Superseded: the priority ladder

`render_priority_ladder` (`report.py`) still exists and still passes its tests,
but has **no callers in `src/`** — the default path is `render_clock_matrix`.
It is kept because its band/urgency semantics are the ones the matrix inherited
and its tests pin them. Do not wire it back into the CLI without revisiting the
2026-08-14 decision above; if you are looking for "the default renderer", it is
not this one.

`docs/generate-readme-demo.py` is the other thing that still calls it — see the
note at the top of that script.

## Why Textual was the wrong fit

`aiuse` prints a **static report** that must remain in the terminal scrollback.
Textual and Rich `Layout` are **viewport-oriented**: they claim a rectangular
region and redraw inside it — at odds with "dump every line into scrollback."

| Approach                                                      | Layout help    | Scrollback?                     |
| ------------------------------------------------------------- | -------------- | ------------------------------- |
| Rich without `Layout` (Panel, Table, Rule, Group, Columns, …) | Strong         | **Yes**                         |
| Rich `Layout`                                                 | Strong (grids) | No — clipped to terminal height |
| Textual (inline or full-screen)                               | Strongest      | No — viewport + redraw          |
| Plain `print`                                                 | None           | Yes                             |

## Watch mode exception (opt-in full-screen)

The "no Textual / no `Layout`" rule above is scoped to the **default stdout
report**, which must live in terminal scrollback. `aiuse watch` (Issue #14,
refined 2026-08-18 — see [`watch-mode.md`](watch-mode.md)) is an **opt-in,
explicitly invoked full-screen monitor** that takes over the alternate screen,
redraws the clock matrix on an interval, and exits on `q` / `Esc` / `Ctrl-C`.
Scrollback preservation is irrelevant inside an alternate-screen app, so the
viewport prohibition does not apply. Default library choice: Rich
`Live(screen=True)` (zero new deps, reuses `render_clock_matrix`); Textual is
reserved for if / when interactivity (selection, switch) is added.

## Implementation

- Gate: `aiuse.tui.should_use_tui` (TTY, not `--json` / `--alerts-only` / `--no-tui`)
- Default: `aiuse.report.render_clock_matrix` → stdout;
  `aiuse.report.render_stderr_meta` → stderr
- Clock inference: `aiuse.models.infer_window_clock` (declared minutes → label
  text → distance to reset, the last flagged `inferred`)
- Full: `aiuse.tui.builders.build_report_sections` + Rich Rule/Panel
- Fallback: classic string path when not a TTY or `--no-tui`. The TUI path calls
  the same `render_clock_matrix`, so piped and interactive output agree.
