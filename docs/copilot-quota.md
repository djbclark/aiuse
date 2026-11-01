# GitHub Copilot quota

GitHub Copilot enforces quotas and rate limits across two distinct architectural layers:

| Layer                                                      | Window                 | What it is                                                                                           | How it surfaces                                        |
| :--------------------------------------------------------- | :--------------------- | :--------------------------------------------------------------------------------------------------- | :----------------------------------------------------- |
| **Management API** (`api.github.com`)                      | Monthly                | Included plan premium interactions (e.g. 1,500/mo on Individual plans) and authorized overage budget | Proactive via `GET /copilot_internal/user`             |
| **Inference Gateway** (`api.individual.githubcopilot.com`) | Rolling 7-day (weekly) | Opaque sliding-window integral of total consumed token volume across frontier models                 | Reactive only via `HTTP 429 user_weekly_rate_limited`  |
| **Inference Gateway** (`api.individual.githubcopilot.com`) | Session (5-hour)       | Short-term burst throttle during continuous autonomous loops                                         | Reactive only via `HTTP 429 user_session_rate_limited` |

## Proactive vs. Reactive Limits

1. **Monthly Subscription Allowance:**
   - GitHub returns `quota_snapshots.premium_interactions` on `https://api.github.com/copilot_internal/user`, indicating included credits, credits used, and overage permissions.
   - Completions (inline autocomplete) and basic chat are marked `unlimited: true` on paid plans and are suppressed by `aiuse` (`keep_copilot_report_window` in `src/aiuse/models.py`) to prevent cluttering the matrix.

2. **Rolling 7-Day & Session Rate Limits:**
   - GitHub does **not** expose a proactive REST endpoint or token meter for the rolling 7-day window.
   - The limit is computed server-side across token volume (prompt tokens + completion tokens × model multiplier) over a sliding 168-hour window.
   - Capacity frees continuously as older high-volume hours age out; there is no fixed calendar reset.
   - When exceeded, GitHub returns:
     - `HTTP 429 Too Many Requests`
     - Error payload: `{"error": {"code": "user_weekly_rate_limited"}}`
     - Response headers: `Retry-After: <seconds>` and `x-ratelimit-reset: <epoch>`

## What `aiuse` shows

- **Labels:**
  - `GitHub Copilot premium requests` (monthly allowance).
  - `GitHub Copilot weekly quota` (nominal 10,080 min / 7 days).
  - `GitHub Copilot session quota` (nominal 300 min / 5 hours).
- **Window Filtering (`src/aiuse/models.py`):**
  `keep_copilot_report_window` retains premium requests, weekly/7-day, and session/5h windows, while filtering out autocomplete completions and generic chat message counts.
- **Collector Integration (`src/aiuse/collectors/`):**
  - `tokscale`: Mapped in `_metric_label` and `_infer_window_minutes`.
  - `codexbar`: Slot labels mapped in `_slot_label` via rate labels and block window kinds.
- **Refusal & Latch Ingestion (`src/aiuse/quota_refusals.py`):**
  Scans agent execution logs (`~/.local/state/acp-run/*.jsonl`) for Copilot rate limit errors (`user_weekly_rate_limited`, `weekly_rate_limited`, `user_session_rate_limited`), extracts `Retry-After` seconds or ISO reset timestamps, and registers pool exhaustion.

## Native Collector (Future)

Tracked in issue `aiuse-0vn`. A native collector (`src/aiuse/collectors/copilot.py`) can resolve credentials via `gh auth token` or macOS Keychain (`gh:github.com`) to query `/copilot_internal/user` without external CLI dependencies.
