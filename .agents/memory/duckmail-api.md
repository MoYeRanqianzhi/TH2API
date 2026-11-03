---
name: duckmail-api
description: Recall when creating or reading temporary inboxes for TH2API registration.
metadata:
  type: reference
  scope: TH2API registration pipeline and any DuckMail integration
  status: active
  last_verified: 2026-11-03
---

DuckMail (`https://api.duckmail.sbs`) implements the mail.tm API shape:
`GET /domains`, `POST /accounts` (`{"address","password"}`), `POST /token`,
then `GET /messages` and `GET /messages/{id}` with `Authorization: Bearer …`.
Inboxes expire after about 24 hours and mail often takes 30-90 seconds to
arrive; verification messages come from `verify@tokenharbor.ai`.

**Evidence:** live calls on 2026-11-03; client in
`src/th2api/registration/duckmail.py`.

**How to apply:** use `DuckMailClient.create_inbox()` then `wait_for_message()`;
do not poll without a timeout. Re-requesting the Token Harbor verification mail
invalidates the earlier link.

**Recheck when:** DuckMail changes its base URL or message shape (the `html`
field can be a list of fragments).
