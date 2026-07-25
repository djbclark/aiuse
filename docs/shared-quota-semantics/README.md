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

| Shared field | `aiuse` source |
| --- | --- |
| `billing_kind` | `AccountUsage.billing_kind` |
| `windows[]` | `QuotaWindow` (`to_dict`) |
| `remaining_percent` | `QuotaWindow.remaining()` / field |
| duration class `5h`/`weekly`/`monthly` | `classify_window_minutes` |
| pace | `PaceProfile` / `compute_pace` / `classify_pace` |
| bands | `report.alert_priority_band` / ladder tags |
| suggestion | `analysis.suggest` → top-level JSON `suggestion` |

Most names are already 1:1 with `docs/json-contract.md`.

## Running the contract suite

```bash
.venv/bin/python -m pytest -q tests/test_shared_quota_semantics.py
```

## Non-goals (v0.1)

- Multi-language runtime / WASM
- Collector adapters or OAuth
- Publishing as a separate PyPI package (later if peers want)
