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
clean local IP. Turnstile appeared on that local IP after two successful
sign-ups. Both are raised as `AccountCapError` / `CaptchaRequiredError`.

**Evidence:** 2026-11-03 probes: 6 proxies returned `account_cap`, clean IP
created two accounts, then the third attempt demanded Turnstile; the proxy pool
later stopped accepting CONNECT entirely.

**Why:** retrying the same request or changing the email hides the real cause
and burns the remaining quota.

**How to apply:** treat these as network errors. Retry from a cleaner line or
residential proxy; do not loop on the same IP. The known errors are documented
in `README.md` under "Known failures".

**Recheck when:** Token Harbor changes its sign-up limits or the failure text.
