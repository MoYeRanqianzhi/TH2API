---
name: tokenharbor-free-tier
description: Recall when enabling free models, measuring the free allowance, or explaining why paid routes fail on Token Harbor.
metadata:
  type: project
  scope: TH2API accounts and the qualification test
  status: active
  last_verified: 2026-11-03
---

Free models are **off** for a new account. `:free` model IDs answer
`403 free_models_disabled` until the account grants the data-sharing consent:
`POST /api/me/privacy` with `{"free_models_enabled": true, ...}`. The dashboard
does this from a consent modal; the registration pipeline now performs the same
call and stores `free_models_enabled: true`.

The free allowance is a personal rolling 7×24-hour window that starts with the
first free request (not a calendar week). It is measured by the **list-price
value** of the work, and free-model calls do consume it. The dashboard/API only
exposes an integer `used_pct` (`GET /api/me/free-tier` also gives `reset_at`,
`req_used`, `exhausted`).

Measured magnitude: 4,000 output tokens of `deepseek-v4.1-flash:free` moved
`used_pct` 0→1; a second 4,000 kept it at 1; 6,000 more moved it to 2. The
pelican qualification run (21,482 completion tokens, ~$0.026 at list price)
moved it to 3. Both point at a window worth roughly **$0.9, i.e. about one
dollar** — not ten.

With a $0 wallet, paid routes return `402 balance_zero`; only the `:free` IDs
are usable. `deepseek-v4.1-flash:free` can return a per-minute
`429 rate_limit_exceeded` for accounts with recent content-policy declines,
while sibling free models keep working.

**Evidence:** live calls on 2026-11-03, `GET /api/me/free-tier`, and the usage
probes recorded in the conversation.

**Why:** the consent is invisible from `/v1/models`, and the allowance is
value-based, so request counts or token counts alone mislead.

**How to apply:** enable free models before promising free access; treat the
allowance as roughly a dollar per week; do not infer a huge quota from "free".

**Recheck when:** Token Harbor publishes an allowance amount or changes the
consent endpoint.
