# Pretty display: Rich renderables, not Textual

**Decision (2026-07-24):** TTY pretty output uses **Rich** (non-`Layout`
renderables printed sequentially). Do **not** use Textual or Rich `Layout`
for the default report.

## Default stdout: priority ladder

Default `aiuse` prints **every provider account** on **stdout** as a ranked list
(top → bottom), with **no blank lines**:

1. **error** (red) — could not fetch usage for that provider/account  
2. **empty** (red) — totally depleted  
3. **n/a** (dim) — non-expiring prepaid / pay-as-you-go (no use-or-lose urgency)  
4. **slow** (yellow) — conserve / pace yourself  
5. **mid** (cyan) — on pace / advisory / no alert  
6. **use** (green) — important to burn soon

Tags label each row. **error / empty / n/a** are fixed lanes near the top;
**slow / mid / use** share a continuous use-urgency gradient (not alphabetical
within a band): most-empty-for-longest at the **top**, most-urgent-to-use-now
at the **bottom**. Read **bottom → top** to pick what to use next. Collection
time, capacity blurb, and `Detail: aiuse --full` go to **stderr** (suppressed
with `-q`).

`--full` keeps the long report on stdout (including History when snapshots
exist). `--brief` aliases the default. Ladder and `aiuse status` lines may
append compact **forecast** fragments from pace (`~lockout …`, projected waste %)
when projections exist.

### Color for readability

- Semantic ANSI roles (red / dim / yellow / cyan / green), not decorative rainbows.
- Always pair color with a text tag (`empty` / `n/a` / `slow` / `mid` / `use`).
- Bold the tag + provider; keep secondary fields in the default face.
- Respect `NO_COLOR` / `--no-color`.

## Why Textual was the wrong fit

`aiuse` prints a **static report** that must remain in the terminal scrollback.
Textual and Rich `Layout` are **viewport-oriented**: they claim a rectangular
region and redraw inside it — at odds with “dump every line into scrollback.”

| Approach | Layout help | Scrollback? |
| --- | --- | --- |
| Rich without `Layout` (Panel, Table, Rule, Group, Columns, …) | Strong | **Yes** |
| Rich `Layout` | Strong (grids) | No — clipped to terminal height |
| Textual (inline or full-screen) | Strongest | No — viewport + redraw |
| Plain `print` | None | Yes |

## Implementation

- Gate: `ai.tui.should_use_tui` (TTY, not `--json` / `--alerts-only` / `--no-tui`)
- Default: `render_priority_ladder` → stdout; `render_stderr_meta` → stderr
- Full: `ai.tui.builders.build_report_sections` + Rich Rule/Panel
- Fallback: classic string path when not a TTY or `--no-tui`
