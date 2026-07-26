# OpenCode Go quota reliability

**Symptom:** `ai` showed OpenCode Go monthly with remaining % left (e.g. 19%)
while the official OpenCode TUI said **Go limit reached / monthly usage limit
reached**, with the same reset countdown (~17d 12h).

## Cause

CodexBar’s default (`--source auto`) for `opencodego` prefers a **local**
reader (`~/.local/share/opencode/opencode.db`) that estimates usage by summing
local message costs against hardcoded dollar caps (`$12` / `$30` / `$60` for
5h / weekly / monthly). That heuristic can report headroom when the
**server-side** Go allotment is already exhausted.

CodexBar `--source web` hits `opencode.ai` billing (cookies) and returns the
authoritative percentages (monthly `usedPercent: 100` when the TUI says limit
reached). It can also expose **Zen balance** (overage prepaid) via
`usage.providerCost` with `period: "Zen balance"`.

## What `ai` does

1. For CodexBar provider `opencodego`, query with `--source web` first.
2. If web fails (no cookies / API error), fall back to CodexBar auto/local and
   annotate that the local estimate may diverge from the official limit.
3. Default `analysis.provider_overrides.opencode.shared_allotment: true` so the
   longest window (monthly) governs pace scoring — a fresh 5h/weekly bar does
   not get a separate “burn this” alert when it draws the same Go budget.
   History-derived short-window alerts obey the same gate, so an older
   “5-hour 100% left” average cannot contradict an exhausted monthly pool.

## Verify

```bash
codexbar usage --provider opencodego --source web --format json --pretty
ai --brief -q
```

When monthly is exhausted, expect CONSERVE on the monthly window (0% left) and
no “use this week” push on the sibling weekly/5h windows.
