---
name: email-providers
description: Recall when changing registration mail sources, adding a domain-forward setup, or debugging concurrent sign-ups.
metadata:
  type: project
  scope: TH2API registration mail providers
  status: active
  last_verified: 2026-11-03
---

Registration takes an `EmailProvider` with `create_mailbox`, `list_messages`,
and `get_message`; `wait_for_message` is implemented by the base class. Built-ins:
`DuckMailProvider` (default, one inbox per account) and
`DomainForwardProvider` (generate `<random>@domain`, read a catch-all forward
inbox in DuckMail).

For `domain-forward`, `pin` is the DuckMail credential of the forward inbox
(password or JWT access token), **not** the Token Harbor password — the flow
generates a random password for Token Harbor and stores the provider name plus
`forward_to` in the record.

Shared inboxes are handled as a queue, not a cursor: providers never delete
messages, mailbox polling starts at the mailbox creation time, the list walk
covers several pages, and `routing.message_targets_mailbox` accepts a message
only if its `To` header names the generated address or the `/verify-email`
token's first segment matches the signed-up session's JWT `sub`. That user id
match is what makes two simultaneous registrations safe even when a forwarder
rewrites the recipient.

**Evidence:** 2026-11-03 provider smoke test created both mailbox types; the
routing tests in `tests/test_routing.py` cover recipient routing, rewritten
recipients, simultaneous sign-ups, and queue pagination. A live domain-forward
registration was not run because no forwarding domain was configured.

**Why:** a naive shared inbox reader can hand one account's verification link
to another, or lose mail that arrived while another sign-up was polling.

**How to apply:** use `register_account(mail_provider=...)` or
`--email-provider domain-forward`; keep `pin` in configuration, never in the
record; add Outlook/IMAP by implementing the same three methods.

**Recheck when:** Token Harbor changes its verification token format, or the
forwarder's header behaviour is observed to differ.
