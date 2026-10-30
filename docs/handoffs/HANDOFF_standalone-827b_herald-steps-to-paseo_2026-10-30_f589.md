---
schema_version: 1
handoff_id: f589
parent_handoff_ids: [6680]
lineage: deterministic
chain: [standalone-827b]
repo: aiuse
workspace: main
branch: main
head_sha: 8de9a9a61b7353303253e90c9fa229d4624ee679
created_at: 2026-10-30T16:40:00-0400
writer: claude-code
---

# Handoff — Herald and turn-usage queue, to a Paseo tab

## The Goal

Land three queued issues so a Paseo session blocked on a person is visible to `/helm`, `fleet-watch` and `relay.py --why`, and Better-Paseo turn usage reaches `agent_stats`:

1. [djbclark-ade#135](https://github.com/djbclark/djbclark-ade/issues/135): ingest `~/.paseo/plugin-data/herald/attention.json` in `skills/session-finder/fleet.py`.
2. [djbclark-ade#136](https://github.com/djbclark/djbclark-ade/issues/136): Better-Paseo turn-usage snapshots on fleet session rows.
3. [site-djbclark#240](https://github.com/djbclark/site-djbclark/issues/240): ingest turn-usage into `agent_stats` (depends on #136).

## Where We Are

No product code exists. This session only resumed from the parent handoff (`6680`), confirmed `aiuse` `main` is at `8de9a9a` and in sync with origin, and confirmed all three issues are OPEN. A `/steps` walk started; its first prompt (how to run #135) was rejected by djbclark with "wants to clarify", and the clarification was never given. Then djbclark asked for this handoff to a Paseo tab. Nothing is claimed, branched or dispatched.

`aiuse` itself needs no change for this chain; it is only the chain's anchor repo. The only untracked file there is `opencode.json`, which is not this chain's.

## What We Tried

Nothing failed. The one `AskUserQuestion` ("1/3 — who implements #135") was declined for clarification, so the implementer choice (acp-dispatch vs. in-session) is still open. The receiver decides it; the default below applies.

## Key Decisions

The receiver should not ask again how to run #135. Default is a claimed `djbclark-ade` cow pasture, one cheap `acp-dispatch --kind code` slice per issue, then review and an `adversary` security verdict quoted in each PR body (home rule 28). If the receiver does the work in its own session, that is also fine, but it must still be in a claimed pasture.

## Decisions

- [decision] Do #135, then #136, then site-djbclark#240, in that order; rejected: parallel dispatch of #135 and #136; because both edit `skills/session-finder/fleet.py` and would conflict.
- [decision] Edit `djbclark-ade` only in a claimed pasture; rejected: editing the shared `~/src/djbclark-ade` checkout; because `standalone-b457` owns `djbclark-ade/main` and is on PR 154.
- [decision] List all three repos as side workspaces in Tier 1; rejected: claiming any of them; because other chains own their pointers.

## Relations

- continues [[HANDOFF_standalone-827b_herald-turn-usage_2026-10-30_6680]]
- decided_in [[HANDOFF_standalone-827b_herald-steps-to-paseo_2026-10-30_f589]]

## Evidence & Data

Facts from the issues and the parent handoff (do not copy live Herald headlines into any repo):

1. Herald file: JSON array, one entry per agent, newer event replaces older. `reason` is one of `question`, `plan`, `permission`, `finished`, `error`, `canceled`. The first three carry a `requestId`.
2. Today `fleet.py` `_paseo_waiting` / `_paseo_rows` only overlay `blocked` on running orphan Paseo rows. A Claude Paseo row that already has `paseo` set never gets it; that is the blind spot #135 fixes.
3. `skills/helm/helm.py` `classify` treats `host=paseo`, no `sid`, `status=blocked` as kind `permission`.
4. `skills/session-finder/relay.py` `paseo_look` / `--why` do not read Herald today.
5. Turn-usage is a `lastUsage` snapshot (`findLast`), not a sum.
6. Issue state checked 2026-10-30: #135, #136 and site-djbclark#240 all OPEN. No tests were run this session.

## Operator Feedback

None this session beyond the declined `/steps` prompt and the request for this handoff.

## Where We're Going

1. Read [djbclark-ade#135](https://github.com/djbclark/djbclark-ade/issues/135) in full, claim a `djbclark-ade` pasture (`cow-workspaces` skill, `cow-pasture`), and implement it. Fail closed: a missing, unreadable or non-array file is an empty list, and one bad entry never drops the rest. Join on `agentId` to every fleet row whose `paseo` or chan id matches. Store only `reason`, `eventId`, `requestId` and `createdAt`; `headline` and `detail` stay in the file and are read at `--why` time.
2. Then [djbclark-ade#136](https://github.com/djbclark/djbclark-ade/issues/136) in the same pasture.
3. Then [site-djbclark#240](https://github.com/djbclark/site-djbclark/issues/240) in a claimed `site-djbclark` workspace. Do not stage `~/ops/site-djbclark/bin/fleet-watch` or its two unpushed commits; they are not this chain's.
4. Commit with `git-commit-own`, push per unit, and send `hermes-ping` when each issue is done.

## Detached jobs

none

## Peers

No peer notices were sent from this session, so there is nothing to transfer. Known neighbours: `standalone-b457` (owns `djbclark-ade/main`, PR 154) and `standalone-132d` (owns `aiuse/main`). Tell neither anything unless the work touches their files.

## Quick Start

```bash
~/.claude/hooks/handoff-tools/.venv/bin/python ~/.claude/hooks/handoff-tools/session_log.py read \
  --state-root ~/.local/state/handoffs --dir ~/.local/state/handoffs/chains/standalone-827b
gh issue view 135 -R djbclark/djbclark-ade
```
