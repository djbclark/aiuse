# Provider identity: canonical id vs config key

Related reading: [`AGENTS.md`](../AGENTS.md) ·
[`README.md`](../README.md) · [`docs/json-contract.md`](json-contract.md) ·
[`docs/shared-quota-semantics.md`](shared-quota-semantics.md)

`aiuse` has **two** provider-name spaces, and mixing them is what makes one
vendor print under two names in a single report. This file states which is
which so the confusion does not come back.

## The two spaces

| Space            | Function                | Examples                                      | What it is for                                                        |
| ---------------- | ----------------------- | --------------------------------------------- | --------------------------------------------------------------------- |
| **Canonical id** | `canonical_provider()`  | `antigravity`, `opencode-go`, `codex`, `sipb` | Identity: matching, grouping, dedup, JSON payloads, **display**       |
| **Config key**   | `provider_config_key()` | `gemini`, `opencode`, `codex`, `sipb`         | Looking up `[plans]` and `analysis.provider_overrides` — nothing else |

Both live in `src/aiuse/models.py`. They map in opposite directions:

- `PROVIDER_ID_ALIASES` collapses every spelling that can reach us — vendor ids
  from collectors, external orchestrator ids, and the config keys — **toward**
  the canonical collector id.
- `PROVIDER_CONFIG_ALIASES` maps a canonical id **toward** the config's
  spelling.

Because the config keys are also listed in `PROVIDER_ID_ALIASES`, a value that
round-trips through `provider_config_key()` still canonicalizes back to one
identity. `provider_display_name()` canonicalizes its argument first, so it
cannot render two names for one vendor regardless of which spelling a caller
holds.

## The rule

> Never use `provider_config_key()` as an identity or display key.

It deliberately collapses onto the config's spelling. Feeding its output into
`provider_display_name()` produced a real defect: history-derived rows rendered
as `Gemini (agy)` while the live rows for the same subscription rendered as
the then-current `Google AI / Antigravity (agy)` (today both render `antigravity`),
and OpenCode Go history rendered as the title-case fallback `Opencode`. It also silently broke
`merge_learned_flexibility()`, which looked up `antigravity:5h` in a table
keyed `gemini:5h` and therefore never matched.

## Providers derived from another vendor's row

A provider can come from a sibling vendor's row instead of its own collector.
It is still its own canonical id, never an alias of the parent:

| Canonical id | Display (`POOL`) | CLI binary | Derived from                                                 | Board `SCOPE` / pool key                                     |
| ------------ | ---------------- | ---------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `omp`        | `claude-api`     | `—`        | CodexBar Claude Platform (`claudeplatform`) / Admin API cost | `—` / `omp_claude`                                           |
| `pi`         | `openai-api`     | `pi`       | Verified OpenAI API console grant / organization Costs API   | `—` / `pi_codex_api`                                         |
| `codex`      | `openai`         | `codex`    | OpenAI Codex subscription (`chatgpt` / `codex` OAuth)        | `codex` / `—`; API credits `openai-api` / `codex_openai_api` |
| `bedrock`    | `bedrock`        | —          | AWS Cost Explorer total account spend (not CodexBar's row)   | no clock; `↑ $` PAYG                                         |

`omp` (displayed as `claude-api`) is Anthropic's API credits / cloud-session grant; it is not a Claude plan window, so it
must not be folded into `claude`. (`omp_agent_db` is deprecated and disabled by default.) Details:
[`omp-cloud-credits.md`](omp-cloud-credits.md).

`pi` (displayed as `openai-api`) is the separately funded OpenAI API pool (fed by CodexBar's
`openai` provider, not `codex`), never a Codex CLI subscription alias (which displays as `POOL=openai`, `SCOPE=codex`). Disabling
`openai` in CodexBar drops this `openai-api` spend meter without affecting `codex`
subscription windows. See [Pi OpenAI API credits](pi-openai-credits.md); costs
alone do not establish remaining prepaid credit.

The `codex` provider splits into two routing families. The subscription windows (5-hour/weekly/monthly,
pool family `default`) gate the ChatGPT-subscription models (e.g. gpt-6-luna); the Codex CLI's `credits`
balance is surfaced as its own window (label `OpenAI API credits`, pool family `openai_api`), the OpenAI API
prepaid credits that gate API-only models (gpt-6.1-sol / gpt-6-astra). That balance reads exhausted at
`$0`/negative and unknown when unmeasurable or when no limit is reported, so routing must fail closed on it
instead of borrowing the subscription window's headroom.

## Window identity across collectors

Provider identity is not enough. Two collectors describe the _same_ window
differently:

| Collector    | Account                             | Label                       |
| ------------ | ----------------------------------- | --------------------------- |
| CodexBar     | `you@example.com`                   | `Gemini 5-hour`             |
| OpenUsage.ai | _(none — provider-scoped envelope)_ | `Antigravity Gemini 5-hour` |

Source priority (`collectors/runner.py`) picks one of them per run, so which
spelling lands in a snapshot depends on which collector was primary _that
hour_. Keying history on the raw label therefore forks one subscription into
two series that never match each other or the live snapshot — and the
shared-allotment child suppression, which compared labels, silently stopped
suppressing.

`analysis/history.py` keys on a **window series** instead:

```text
window_series_key(provider, label, window_minutes)
    -> "<canonical provider>:<independent pool>:<duration bucket>"
    e.g. "antigravity:gemini:5h", "antigravity:claude_gpt:5h", "claude:-:5h"
```

The independent pool comes from `analysis/pace.py:independent_pool_key()`, so
Antigravity's Gemini and Claude/GPT budgets stay separate (they are genuinely
independent allotments) while the two collectors' labels for the same budget
collapse together.

A series key is **not** a row identity on its own. A provider can hold several
subscriptions, so history groups on `(series key, account)`. An anonymous
history row adopts the live account only when `resolve_live_window()` finds
exactly one candidate — a multi-account provider never borrows a sibling's
identity.

Collectors should also avoid inventing a second spelling in the first place:
`collectors/openusage.py` keeps `_SELF_QUALIFIED_LABELS` for resource ids whose
label already names the pool, so it emits `Gemini 5-hour` rather than
double-qualifying it as `Antigravity Gemini 5-hour`.

## Snapshots are an append-only log written by older code

`~/.cache/aiuse/snapshots/*.json` holds records written by every version that
ever ran on the machine, including ones that stored a config key as the
provider or an anonymous account as JSON `null`. Read paths must therefore:

- canonicalize the provider on read, never trust the stored spelling;
- use `str(x.get("account") or "")`, not `str(x.get("account", ""))` — an
  explicit `null` stringifies to `"none"` and silently matches nothing;
- tolerate a label that no live collector produces any more.

`tests/conftest.py` redirects `snapshot_dir()` to a temp directory for every
test, because persisting is on by default (`learn_from_history: auto`) and CLI
tests otherwise write empty snapshots into the developer's real history, where
they are indistinguishable from real collections and displace genuine samples
from the newest-N window `chronic_waste_summary()` reads.
