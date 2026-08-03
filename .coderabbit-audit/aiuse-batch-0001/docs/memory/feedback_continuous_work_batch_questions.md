---
name: feedback-continuous-work-batch-questions
description: User prefers continuous autonomous work through multi-phase plans; batch clarifying questions ahead of time rather than pausing at each step
metadata:
  node_type: memory
  type: feedback
  originSessionId: 18f4effc-694f-4df8-ae44-495910324272
---

When executing a multi-phase/multi-step implementation plan (e.g. the
aiuse quota-algorithm-audit phases), keep working through phases
continuously without stopping to ask "should I continue?" between each
one.

**Why:** explicit correction after I asked a check-in question between
Phase 1 and Phase 2 of `docs/quota-algorithm-audit-2026-08-01.md`
(2026-08-02). The user's answer: "continue continuously unless you need
input from me. try to ask me for input in batches and ahead of time."

**How to apply:** only pause to ask when genuinely blocked (missing
decision, ambiguous requirement, a step's described bug doesn't
reproduce as written, etc. — same bar as the plan doc's own "stop and
report the discrepancy" rule). If multiple questions are foreseeable
across upcoming phases, ask them together up front rather than one at a
time as each phase is reached. Applies generally, not just to this one
plan.
