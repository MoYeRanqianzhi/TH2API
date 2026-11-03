---
name: tokenharbor-registration
description: Recall when changing Token Harbor sign-up, email verification, or API key minting in TH2API.
metadata:
  type: project
  scope: TH2API registration module and any script that touches tokenharbor.ai auth
  status: active
  last_verified: 2026-11-03
---

Token Harbor sign-up is a Next.js React Server Action, not a REST endpoint.
`POST /login?mode=signup` with header `Next-Action: 60c5d7a61c5d71d0085a79db83f9b0423db7d50ff5`
and a multipart body (bookkeeping fields `_1_$ACTION_*`, form fields prefixed
`_1_`, argument array under `0`) creates the user. Supabase auto-confirms the
address and the response sets `sb-auth-auth-token.0/.1` cookies.

The site then sends its own verification email (`/verify-email?token=…`) which
must be opened with that same session; the redirect carries `?verify=success`.
Keys minted before verification are accepted by `POST /api/keys` but rejected
by `GET /v1/models`, so always verify first, then mint and validate. Free
models also start disabled: registration must grant the privacy consent before
`:free` routes work ([[tokenharbor-free-tier]]).

Session cookies rotate during verification and key creation, so read the auth
session last and persist the raw token plus decoded access/refresh tokens.

**Evidence:** live registration on 2026-11-03; implementation in
`src/th2api/registration/tokenharbor.py` and `flow.py`.

**Why:** the action encoding and the verify-before-key rule are invisible from
the REST surface and cost a debugging cycle when rediscovered.

**How to apply:** reuse `TokenHarborClient` rather than hand-building the
multipart form. If sign-up returns a generic failure, first confirm the action
id still exists in the deployed login chunks, then check IP reputation
([[tokenharbor-ip-reputation]]).

**Recheck when:** the action id changes on redeploy, or sign-up starts returning
a session without cookies / a different verification URL shape.
