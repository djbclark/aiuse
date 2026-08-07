# Shared quota semantics (v0.1.0)

Language-neutral **schemas, enums, pace formulas, and golden fixtures** for
use-or-lose ranking. Design source: [`../shared-quota-semantics.md`](../shared-quota-semantics.md).

This package is **dogfooded in `aiuse`** via `tests/test_shared_quota_semantics.py`.
It is not a Python library published to PyPI yet — peers can copy fixtures and
schemas without importing `aiuse`.

## Version

**0.1.0** — freeze enums + schemas + pace rules + initial golden vectors.

## Layout

```text
docs/shared-quota-semantics/
  README.md                 # this file
  schemas/                  # JSON Schema (draft 2020-12)
  enums/                    # YAML enum lists
  policy/                   # default thresholds / shared_allotment
  formulas/pace.md          # normative pace + allotment rules
  fixtures/                 # golden vectors (contract tests)
```

## How `aiuse` maps fields

| Shared field                           | `aiuse` source                                   |
| -------------------------------------- | ------------------------------------------------ |
| `billing_kind`                         | `AccountUsage.billing_kind`                      |
| `windows[]`                            | `QuotaWindow` (`to_dict`)                        |
| `remaining_percent`                    | `QuotaWindow.remaining()` / field                |
| duration class `5h`/`weekly`/`monthly` | `classify_window_minutes`                        |
| pace                                   | `PaceProfile` / `compute_pace` / `classify_pace` |
| bands                                  | `report.alert_priority_band` / ladder tags       |
| suggestion                             | `analysis.suggest` → top-level JSON `suggestion` |

Most names are already 1:1 with `docs/json-contract.md`.

## Running the contract suite

```bash
.venv/bin/python -m pytest -q tests/test_shared_quota_semantics.py
```

## Fixture `expected` fields

`schemas/fixture.schema.json` keeps `expected` as `additionalProperties: true`,
but the contract suite (`tests/test_shared_quota_semantics.py`) currently
understands:

| Field                                                                      | Checks                                                                                                                                                                                                               |
| -------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `band`, `alert_kind`, `pace_verdict`, `suggestion_eligible`, `has_overage` | Core scoring outputs (see `docs/shared-quota-semantics.md`)                                                                                                                                                          |
| `governing_label`                                                          | Longest-duration window across _all_ windows (single-pool cases only — see caveat below)                                                                                                                             |
| `alert_labels`                                                             | List of window labels that **must** each produce their own burn/conserve alert — for hard-separated independent pools (e.g. Antigravity Gemini vs Claude/GPT) where more than one governing window can alert at once |
| `suppressed_labels`                                                        | List of window labels that **must never** alert on their own — shared-allotment children (nested 5h, cswap model-scoped weekly sub-windows, …)                                                                       |

Caveat: `governing_label` is checked against the raw, pool-unaware
`governing_partition()` (matching the original v0.1 fixtures, which are all
single-pool). For multi-pool fixtures (Antigravity dual pools) use
`alert_labels` / `suppressed_labels` instead, which are checked against the
full `analyze_use_or_lose()` output and are pool-aware.

## Non-goals (v0.1)

- Multi-language runtime / WASM
- Collector adapters or OAuth
- Publishing as a separate PyPI package (later if peers want)
