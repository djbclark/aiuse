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
      SERVICE    ACCT     SCOPE           5H  WEEK MONTH   NEXT $ UNUSED
  % used — 0% untouched, 100% exhausted
error oc-go      —        —            no usage data
empty codex      gmail    —                —  100%     —   5.7d    $0.00
slow  agy        gmail    gemini          0%   23%     —     5h    $5.31
mid   cursor     gmail    other models     —     —    0%    19d   $20.00
use   claude     mit      —              25%   60%     —     5h    $2.76
  dim % = clock inferred, not reported · + = >1 window on that clock, showing most-used
```

The clocks are `CLOCK_COLUMNS` — `5H`, `WEEK`, `MONTH`. An **em-dash** means the
service has no window on that clock, which is itself the answer to "why does
this one only show a weekly?". That question is what the matrix exists to
answer: the previous ladder showed each row on whichever window happened to
govern it, so every row was measured on a different clock.

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

**error / empty / n/a** are fixed lanes near the top; **slow / mid / use** share
a continuous use-urgency gradient within their lanes (`alert_priority_band` for
the lane, `alert_use_urgency` within it). Read **bottom → top** to pick what to
use next.

Collection time, capacity blurb, and `Detail: ai --full` go to **stderr**
(`render_stderr_meta`, suppressed with `-q`), so stdout stays pipeable.

### Width adaptation

`render_clock_matrix` sizes itself to `min(terminal_width(), TABLE_MAX_WIDTH)`
(110). A narrow terminal **sheds whole columns** rather than slicing a number in
half, in this order: `$ UNUSED`, then `NEXT`, then `SCOPE`. The clock columns and
the identity columns never drop — they are the reason the table exists. `SCOPE`
gives way last of the optional three, because real pool names ("gemini",
"other models") are worth more than the dollar figure.

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

## Implementation

- Gate: `aiuse.tui.should_use_tui` (TTY, not `--json` / `--alerts-only` / `--no-tui`)
- Default: `aiuse.report.render_clock_matrix` → stdout;
  `aiuse.report.render_stderr_meta` → stderr
- Clock inference: `aiuse.models.infer_window_clock` (declared minutes → label
  text → distance to reset, the last flagged `inferred`)
- Full: `aiuse.tui.builders.build_report_sections` + Rich Rule/Panel
- Fallback: classic string path when not a TTY or `--no-tui`. The TUI path calls
  the same `render_clock_matrix`, so piped and interactive output agree.
