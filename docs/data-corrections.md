# Correcting history when a build had a bug

Every record aiuse writes names the build that collected it, so when a version
is later found to have misread a collector, misclassed a window or mispriced
a pool, you can find exactly the data it produced and discount or recompute it
instead of guessing from dates.

**Related:** [json-contract.md](json-contract.md) (the `aiuse_version` field,
schema 1.7), [history-learning.md](history-learning.md) (what reads the
history), [attribution.md](attribution.md).

## Where the version is recorded

| Record                                                           | Field                                        | Meaning                                     |
| ---------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------- |
| `~/.cache/aiuse/snapshots/<ts>.json` and `latest.json`           | `aiuse_version` and `snapshot.aiuse_version` | build that **collected** the sample         |
| `~/.cache/aiuse/samples/<ts>.json` (burst samples)               | same                                         | build that collected the burst reading      |
| `~/.cache/aiuse/ledger/<ts>.json` (tokscale ledger)              | `aiuse_version`                              | build that wrote the sample                 |
| `aiuse --json`, `aiuse --available --json`, serve `/v1/snapshot` | top-level `aiuse_version`                    | collector's build (a cache keeps its own)   |
| `aiuse attribute --json`                                         | `aiuse_version`, `coverage.collected_by`     | the report's build; sample counts per build |

**Missing or `null` means unknown.** Anything written by 5.1.3 or earlier has
no field. Treat it as "5.1.3 or earlier, build unknown": it can still be
corrected by date if you know when the buggy build was installed, but never by
version.

## List what each build collected

```sh
# count snapshots per collecting build (missing = unknown, written by <= 5.1.3)
jq -r '.aiuse_version // "unknown (<= 5.1.3)"' ~/.cache/aiuse/snapshots/2*.json | sort | uniq -c

# one line per snapshot: time, build
jq -r '[.collected_at, (.aiuse_version // "unknown")] | @tsv' ~/.cache/aiuse/snapshots/2*.json
```

(`2*.json` skips `latest.json`, which is a copy of the newest sample.)

```python
import json, pathlib
from collections import Counter

snapshots = sorted(pathlib.Path("~/.cache/aiuse/snapshots").expanduser().glob("2*.json"))
by_build = Counter()
suspect = []  # snapshots a known-buggy build (or an unknown one) produced
BUGGY = {"5.1.3"}  # builds you have found faulty
for path in snapshots:
    row = json.loads(path.read_text())
    build = row.get("aiuse_version")  # absent on records written by <= 5.1.3
    by_build[build or "unknown (<= 5.1.3)"] += 1
    if build in BUGGY:
        suspect.append(path)
print(by_build)
print(f"{len(suspect)} snapshots from a buggy build")
```

## Discount or recompute

1. **Find the window.** Collect the set of affected builds (`BUGGY` above). If
   the bug is also in "unknown" records, add the date range from when that
   build was installed to the fix release (`collected_at`).
2. **Discount.** Skip those files when learning from or charting history:
   `aiuse attribute` already reports `coverage.collected_by`, so a range whose
   samples come mostly from a faulty build is visible at a glance. Move the
   suspect files out of `~/.cache/aiuse/snapshots/` (never delete the only
   copy) to take them out of burn-rate learning.
3. **Recompute.** The raw per-account readings are in each file's
   `accounts[]`; re-derive the field the bug corrupted from them with the fixed
   build's logic, and write the corrected value back with its own provenance.
   Keep `aiuse_version` as the collecting build: it records who collected the
   data, so a corrected file should add a separate note (for example a
   `corrected_by` key) rather than overwrite it.

The `aiuse_version` of a record is provenance, not behaviour: parsers must not
branch on it (the schema is self-describing, see
[json-contract.md](json-contract.md#stability-policy)).
