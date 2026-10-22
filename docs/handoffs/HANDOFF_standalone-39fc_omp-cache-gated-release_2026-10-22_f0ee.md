---
schema_version: 1
handoff_id: f0ee
parent_handoff_ids: ["7f9e"]
lineage: deterministic
chain: [standalone-39fc]
repo: aiuse
workspace: main
branch: main
head_sha: bd4b6405562833aabdb7c98da2f96ff18d30bbd0
created_at: 2026-10-22T11:40:00-0400
writer: grok
---

# Handoff — rate-gated omp cache is in 3.9.3

## The Goal

Parent [7f9e](HANDOFF_standalone-39fc_omp-still-available-429_2026-10-22_7f9e.md) left two gaps: `latest.json` could omit a rate-gated omp row when CodexBar swallowed the miss, and the human summary still led with `credits: ok`. Close both, then ship them, without spending omp and without restarting the watch.

## Where We Are

Published and installed aiuse is **3.9.3**. `main` is clean and matches `origin/main` at `bd4b640` (`docs: record published aiuse 3.9.3`). `aiuse --version` and `ai --version` both print `aiuse 3.9.3`. Cellar `/opt/homebrew/Cellar/aiuse/3.9.3`. `brew test djbclark/aiuse/aiuse` passed. PyPI: https://pypi.org/project/aiuse/3.9.3/ . GitHub: https://github.com/djbclark/aiuse/releases/tag/v3.9.3 . Tap `~/src/homebrew-aiuse` is `661f8e5`. In-repo formula commit is `b0c63f8`.

`save_snapshot` folds the admin-api credit reading into the cache file, so a CodexBar run that omits omp without a collector error still stores the gated row. A partial sample that did not ask for omp does not. A rate-gated summary says `credits: GATED`. The JSON window `state` stays `ok`. `--available` omits omp while the Claude weekly window is exhausted.

Launchd `com.djbclark.aiuse` runs `~/.local/bin/aiuse sample`, a symlink to the Homebrew formula. The next sample uses 3.9.3. This session did not run a sample and did not read `latest.json` after the upgrade, so do not assume the on-disk cache already contains the row.

Do not spend omp. The Claude weekly window resets `2026-10-23T10:00:00Z`. Do not restart the watch. Named pid 84222 is dead.

## What We Tried

1. `just release 3.9.3` published the tag, the GitHub release, PyPI, and both formula commits, then exited 1 at `brew update --force` because another session's `brew upgrade --cask paseo` held `brew vendor-install ruby`. The wrapper's exit code was 0 because it echoes the recipe rc into a file. The recipe rc was 1. The other brew was left running.
2. Waiting on the original pids was not enough. They exited and a new paseo upgrade, plus `brew install lld`, started the portable-ruby download again. The local upgrade ran only after no `vendor-install ruby` process remained.
3. A second `just release 3.9.3` was not started. The remaining steps were the ones the script had not reached: `brew update --force`, fast-forward the tap, `brew upgrade djbclark/aiuse/aiuse`, `brew test`, then `aiuse --version` and `ai --version`.
4. One pytest invocation named `tests/test_selfdescribe.py::test_summary_line_and_semantics_describe_the_grant`. That test lives in `tests/test_omp_chat_json.py`. The rerun of `tests/test_rate_gate.py` and `tests/test_omp_chat_json.py` passed (20).

## Key Decisions

1. Operator, during `/loose`: release 3.9.3 now. Rejected: leave 3.9.2 installed until a later ask.
2. Operator, during `/loose`: a rate-gated summary must not lead with `credits: ok`. The human word is `GATED`. Rejected: leave `ok` and rely on the `NOT usable now` suffix. The JSON window `state` stays `ok` because the dollars are not exhausted.
3. Operator, after the install: write this handoff. Rejected for that prompt: quit with only the tier 1 log updated.
4. Formula caveat text that still names caut, OpenUsage.ai, and OpenUsage.sh stays out of 3.9.3. `brew test` printed that caveat. It is a separate formula change.
5. `load_overrides()` inside `save_snapshot` is called with no config. Hide and disable still apply through the snapshot's `disabled_services` and `hidden_pools`. A full save whose config limits providers without setting `disabled_services` could still synthesize omp. Collectors normally set `disabled_services`. Not changed.
6. Do not spend omp to see whether pi reads `--available`. Do not comment on [OpenUsage 1422](https://github.com/robinebers/openusage/issues/1422). Do not rewrite the `~/ops/site-djbclark` pointer (chain `aiuse-1qo`).

## Evidence & Data

1. `180a79f` — `src/aiuse/analysis/history.py`, `tests/test_rate_gate.py`. Cache file carries the gated admin-api row. Three cache tests, including partial samples and a disabled omp.
2. `018bcfc` — `src/aiuse/analysis/selfdescribe.py` `summary_line`, `tests/test_rate_gate.py`, `docs/json-contract.md`, `docs/omp-cloud-credits.md`.
3. `17a07f9` — version bump. The release script's pytest passed before this commit (1360 passed). Pre-commit and pre-push passed.
4. `b0c63f8` — `packaging/homebrew/aiuse.rb` sha256 and url for `v3.9.3`.
5. `bd4b640` — `AGENTS.md`, `docs/current-status.md`, and item 4 of the rate-window section in `docs/omp-cloud-credits.md`. Pre-push all-files gate passed (`b0c63f8..bd4b640`).
6. Installed check at 2026-10-22T15:06:56Z, and again at handoff time: `aiuse 3.9.3` and `aiuse 3.9.3`.

## Operator Feedback

1. `/loose` answer: "2. Release 3.9.3 now".
2. `/loose` answer: "2. Stop leading with ok".
3. After the install: "1. /handoff".

## Where We're Going

1. Do not re-investigate the omp 429 or cut another release. Open [`docs/next-options.md`](../next-options.md) only if the operator asks what to do next. Do not spend omp before `2026-10-23T10:00:00Z`. Do not restart the watch. Do not run `aiuse watch --all-providers`.
2. Formula caveats in `packaging/homebrew/aiuse.rb` still name caut, OpenUsage.ai, and OpenUsage.sh. Change that only if the operator asks, as its own formula commit, not a release.
3. cswap re-login stays the operator's browser step. caut, caam, and OpenUsage stay deprecated.
4. Whether pi consults `aiuse --available` was not traced into `~/src/plektra-djbclark` or `~/src/djbclark-ade`. Do not spend omp to test it. The 429 in parent 7f9e (`req_011CgHNHrvtsXdX14fpxmZsF`, `retry-after-ms=73284000`) lands on the weekly reset.

## Detached jobs

none. This session owns no open bigteam job. `CLAUDE_CODE_SESSION_ID` was unset, so a query that filtered on that empty value listed other owners' jobs. A query for session `01a163f5-6090-7172-b96f-addecd42273b` found zero. Do not re-arm those other records.

## Quick Start

```bash
cd /Users/djbclark/src/aiuse && git pull --ff-only
aiuse --version
ai --version
```

Both version commands should print `aiuse 3.9.3`. Read this file, then parent `7f9e` only for the 429 transcript. The code and the installed formula already contain the cache fold and the `GATED` line.
