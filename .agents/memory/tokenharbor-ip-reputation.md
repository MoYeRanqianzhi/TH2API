---
name: tokenharbor-ip-reputation
description: Recall when Token Harbor sign-up fails with account_cap or a human-check message during registration.
metadata:
  type: project
  scope: TH2API registration runs and any retry logic around tokenharbor.ai sign-up
  status: active
  last_verified: 2026-11-03
---

Two sign-up failures are IP-reputation signals, not bad input:

- `{"error":"We can't create another account from this network or device right now.","code":"account_cap"}`
- `Please complete the human check to continue.` (Cloudflare Turnstile, no code)

`account_cap` means the egress IP already created its allowance of accounts;
it appeared on datacenter proxy IPs while the same email/password worked from a
clean local IP. Both are raised as `AccountCapError` / `CaptchaRequiredError`.

**Possible trigger:** Turnstile showed up on the next attempt after two
successful sign-ups from the same IP. That is a hypothesis, not a confirmed
rule -- the threshold may be time-, volume-, or bot-score-based. Do not treat
"two accounts then captcha" as guaranteed either way; check the actual response
rather than pre-emptively rotating.

**Evidence:** 2026-11-03 probes: 6 proxies returned `account_cap`; the clean
local IP created two accounts, then the following attempt demanded Turnstile
(and kept demanding it minutes later). The proxy pool later stopped accepting
CONNECT entirely.

**Why:** retrying the same request or changing the email hides the real cause
and burns the remaining quota.

**How to apply:** treat these as network errors. Retry from a cleaner line or
residential proxy; do not loop on the same IP. The known errors are documented
in `README.md` under "Known failures".

**Recheck when:** Token Harbor changes its sign-up limits or the failure text.
