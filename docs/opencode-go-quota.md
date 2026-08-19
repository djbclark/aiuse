# OpenCode Go quota reliability

**Symptom:** short Go windows (rolling 5h / weekly) show **0% used** while
monthly shows **100% used**, and secondary tools report ~19% monthly remaining
when the official page is already empty.

## OpenCode Go ≠ OpenCode Zen (do not merge)

From a **billing and usage** point of view these are totally different services.
`aiuse` keeps them as separate providers and ladder rows:

| Service          | Provider id    | What it is                                                            | When empty                                              |
| ---------------- | -------------- | --------------------------------------------------------------------- | ------------------------------------------------------- |
| **OpenCode Go**  | `opencode-go`  | Subscription window allotment (5h / weekly / **monthly** shared pool) | TUI: _Go limit reached_ / _monthly usage limit reached_ |
| **OpenCode Zen** | `opencode-zen` | Separate **prepaid wallet** (dollar balance, no subscription cycle)   | Inventory only; may fund overage when Go is spent       |

CodexBar often returns Zen as `usage.providerCost` / period `"Zen balance"` on
the same `opencodego` payload as Go windows. That is a transport convenience
only — **never** fold Zen into Go quota math or shared-allotment scoring.

When Go monthly is exhausted, the OpenCode TUI may offer _enable usage from
your available balance_ (Zen). That is a **different** pot of money, not a
reset of Go. See [`opencode-zen-balance.md`](opencode-zen-balance.md).

### TUI confirmation (operator, 2026-07-31)

Attempting an OpenCode Go model via `opencode` while monthly is spent:

> Go limit reached  
> monthly usage limit reached. It will reset in 9 days 23 hours. To continue
> using this model now, enable usage from your available balance

So: short windows can still show 100% available; **monthly overrides**; Go is
actually unusable until monthly resets (unless the user deliberately spends
Zen balance).

### Expired / not renewed (operator, 2026-08-19)

A lapsed Go plan is a different failure from “monthly spent”:

| Signal                                          | Monthly spent                                                                             | Subscription expired / not renewed                                |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Official `/workspace/<id>/go` page              | `rollingUsage` / `weeklyUsage` / `monthlyUsage` objects with percents; monthly ~100% used | No usage-window objects (`subscription` may also be null)         |
| OpenCode TUI                                    | _Go limit reached_ / _monthly usage limit reached_                                        | _Insufficient balance_                                            |
| CodexBar `--source web`                         | Windows matching the page                                                                 | Parse fails (“missing usage fields”) and Auto falls back to local |
| CodexBar `--source local` / OpenUsage estimated | May still show leftover $ vs $12/$30/$60 caps                                             | Same leftover % — **looks usable**                                |

`aiuse` treats a `/go` page **without usage-window objects** as **empty**,
labeled `subscription expired`. After a renew those objects return (often still
with `subscription: null` and wrapped as `$R[n]={…}`); that is a live plan,
not an expiry. The native `opencode_go` collector uses the same OpenCode
console cookie as Zen (`OPENCODE_ZEN_COOKIE` / `AIUSE_OPENCODE_ZEN_COOKIE`)
and is preferred over CodexBar/OpenUsage local estimates for `opencode-go`.

## Ground truth (official OpenCode usage page)

Example (operator-confirmed, 2026-07-31):

| Window        | Official display        | Meaning for ranking          |
| ------------- | ----------------------- | ---------------------------- |
| Rolling usage | 0% used · resets ~5h    | Short bar **looks open**     |
| Weekly usage  | 0% used · resets ~2d    | Short bar **looks open**     |
| Monthly usage | 100% used · resets ~10d | **Pool spent** — Go is empty |

Nested windows can reset their own counters while the **monthly shared
allotment** is still exhausted. Treat monthly as the hard cap for “can I use
Go?”.

## Source matrix (same machine, same time)

| Source                                 | 5h / session     | Weekly    | Monthly                     | Trust for ranking                        |
| -------------------------------------- | ---------------- | --------- | --------------------------- | ---------------------------------------- |
| **OpenCode web page**                  | 0% used          | 0% used   | 100% used                   | **Authoritative**                        |
| **CodexBar `--source web`**            | `usedPercent: 0` | `0`       | `100`                       | **Authoritative** (matches page)         |
| **CodexBar `--source local` / `auto`** | 0                | 0         | **~80.6 used** (~19% left)  | **Local estimate — wrong when depleted** |
| **OpenUsage.ai** (`estimated: true`)   | $0 of $12        | $0 of $30 | **~$48 of $60** (~19% left) | **Same local $cap heuristic**            |

The ~19 percentage-point CodexBar-vs-OpenUsage cross-check on monthly is
**not** poll timing: OpenUsage marks resources `estimated: true` and uses the
same fixed dollar caps CodexBar local uses (`$12` / `$30` / `$60`).

## Cause

1. **Shared allotment:** 5h ⊂ weekly ⊂ monthly; a fresh short bar does not
   unlock Go when monthly is spent.
2. **Local heuristic (CodexBar local + OpenUsage):** sum local message costs
   against hardcoded dollar caps. Can report headroom when **server-side**
   monthly is already at 100% used, or when the Go **subscription itself**
   has expired and the official page reports `subscription: null`.
3. **Display wording (aiuse, fixed):** ranking correctly marked monthly
   `empty`, but conserve copy still said “pace / ~lockout / projected to run
   out” at 0% left. Exhausted rows now say exhausted + reset time, and note
   that shorter windows may still look open.

## What `aiuse` does

1. Native `opencode_go` collector reads the official workspace `/go` page with
   the OpenCode console cookie. Missing usage-window objects is empty / expired;
   those objects (even with `subscription: null`) are the live allotment.
2. For CodexBar provider `opencodego`, query with `--source web` first.
3. If web fails (no cookies / API error), fall back to CodexBar auto/local and
   annotate that the local estimate may diverge from the official limit.
   Local leftover % never wins selection when the native page (or CodexBar web)
   is live.
4. Prefer CodexBar over OpenUsage for selection when both are estimates or
   both are web; cross-check still runs. When an **estimated/local** peer
   disagrees with web/server data, the warning states which side is local
   and that web billing wins.
5. Default `analysis.provider_overrides.opencode.shared_allotment: true` so the
   longest window (monthly) governs pace scoring — a fresh 5h/weekly bar does
   not get a separate “burn this” alert when it draws the same Go budget.
   History-derived short-window alerts obey the same gate.
6. OpenUsage rows with `estimated: true` get an explicit note that figures are
   local cost vs fixed $ caps.

## Verify

```bash
# Official billing path (should match the OpenCode usage page)
codexbar usage --provider opencodego --source web --format json --pretty

# Local estimate (often optimistic on monthly when the page is empty)
codexbar usage --provider opencodego --source local --format json --pretty

# OpenUsage local estimate (resources.estimated == true)
openusage opencode --force | jq '.providers.opencode.resources'

aiuse --brief -q
aiuse --json -q | jq '.snapshot.cross_checks[] | select(.provider=="opencode-go")'
```

When monthly is exhausted, expect:

- Ladder: `empty OpenCode Go … monthly … 0% left · resets within …`
- No “use this week / 5-hour” burn alert on the sibling short windows
- Cross-check warning if OpenUsage still shows ~19% monthly remaining

When the subscription has expired / not renewed, expect:

- Brief table: `empty oc-go … subscription expired`
- JSON: `plan` is `expired`, monthly/5h/weekly clocks are absent, `remaining_percent` is 0
- CodexBar local leftover % may still appear in a cross-check; trust the native page

After a renew (operator, 2026-08-19), expect:

- Brief table: `mid oc-go … 0% 0% 0%` (fresh allotment; next reset is the 5h bar)
- Official page still has `subscription: null`; live proof is the returned `rollingUsage` / `weeklyUsage` / `monthlyUsage` objects
- Native and CodexBar percentages should agree; do not keep the expired label
