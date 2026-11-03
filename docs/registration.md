# Registration usage

The registration pipeline lives in `th2api.registration`. It obtains an address
from an email provider, signs up on Token Harbor, verifies the email, enables
free models, and mints a `thk_live_…` API key.

## Setup

```bash
uv sync
```

## Library

```python
from th2api.registration import register_account, save_account

account = register_account(label="my-agent")
save_account(account)

print(account.email, account.api_key)
```

`register_account` accepts:

| Argument | Meaning |
| --- | --- |
| `email`, `password` | reuse an existing inbox instead of creating one |
| `label` | API key label |
| `domain` | DuckMail domain to use for the new inbox |
| `mail_provider` | custom mail source, see below |
| `proxy` | HTTP(S) proxy URL, see below |
| `verification_timeout` | seconds to wait for each verification email |

## Mail providers

The mail source is an abstraction: `DuckMailProvider` (default) and
`DomainForwardProvider` ship with the project, and any other source can be
plugged in.

```python
from th2api.registration import (
    DomainForwardProvider,
    DuckMailProvider,
    register_account,
)

# Default: a new disposable inbox per registration.
account = register_account(mail_provider=DuckMailProvider(domain="niceground.shop"))

# Custom domain: generate <random>@example.com, read mail from one forward inbox.
provider = DomainForwardProvider(
    domain="example.com",
    forward_to="inbox@niceground.shop",
    pin="<DuckMail password or access token>",
)
account = register_account(mail_provider=provider)
```

`DomainForwardProvider` never creates a DuckMail account for the generated
address. It expects the custom domain's catch-all to forward to `forward_to`,
and `pin` is that DuckMail inbox's credential — a password or an access token
(JWT) — not the Token Harbor account password. The pipeline generates a
separate password for Token Harbor.

### Concurrent registrations

One forward inbox may receive several registrations' mail at once. The provider
is designed around a queue, not a cursor:

- messages are never deleted or marked consumed;
- a mailbox only considers mail newer than its own creation time;
- the flow accepts a message only when the `To` header names the generated
  address, or — if a forwarder rewrote the recipient — when the user id inside
  the `/verify-email?token=…` link matches the signed-up session;
- bursts are read across several inbox pages.

Two simultaneous `register_account` calls (threads, processes, or separate
machines) can therefore share one `forward_to` inbox without stealing each
other's verification mail. The tests in `tests/test_routing.py` cover this.

### Custom providers

Implement three methods and pass the instance as `mail_provider`:

```python
from th2api.registration import EmailProvider, Mailbox

class OutlookProvider(EmailProvider):
    name = "outlook"

    def create_mailbox(self) -> Mailbox:
        ...  # create an alias/address and return Mailbox(address, self.name, secret=...)

    def list_messages(self, mailbox: Mailbox):
        ...  # return recent Message entries; do not delete them

    def get_message(self, mailbox: Mailbox, message_id: str):
        ...  # return the full Message with text/html
```

`EmailProvider.wait_for_message` already implements polling on top of those
three methods. An IMAP or Microsoft Graph implementation can live outside this
package and be injected the same way.

## Proxy

Passing `proxy` routes **every** request in the pipeline through it: inbox
creation and polling on `api.duckmail.sbs`, the sign-up server action, the
verification link, the free-model consent, and key creation on
`tokenharbor.ai`.

```python
account = register_account(
    label="my-agent",
    proxy="http://user:pass@proxy.example.com:3129",
)
```

The same value is used for the CLI:

```bash
uv run th2api-register --proxy http://user:pass@proxy.example.com:3129
```

Notes:

- The proxy URL must include its scheme: `http://`, `https://`, or
  `socks5h://` for SOCKS5.
- Prefer `socks5h://` over `socks5://`: `h` means the proxy resolves the
  hostname. Plain `socks5://` resolves locally, so under a fake-IP tunnel it
  sends an unroutable address and the proxy closes the connection.
- Encode reserved characters in credentials, e.g. `p@ss` becomes `p%40ss`.
- Both HTTP(S) and SOCKS5 proxies are passed straight to libcurl; an
  end-to-end registration through `socks5h://` has been verified.
- There is no automatic pool rotation. Token Harbor rejections surface as
  `RegistrationError`; a proxy that cannot connect raises curl_cffi's
  `ProxyError` / timeout directly. For `account_cap` / Turnstile failures the
  original `AccountCapError` / `CaptchaRequiredError` is available as
  `exc.__cause__`, so a caller can retry with the next proxy:

```python
from th2api.registration import (
    AccountCapError,
    CaptchaRequiredError,
    RegistrationError,
    register_account,
    save_account,
)

for proxy in proxies:
    try:
        account = register_account(proxy=proxy)
        break
    except RegistrationError as exc:
        if not isinstance(exc.__cause__, (AccountCapError, CaptchaRequiredError)):
            raise
else:
    raise SystemExit("every proxy was rejected")

save_account(account)
```

- A dead proxy fails fast with `curl_cffi`'s `ProxyError` / timeout; check the
  proxy independently (for example `curl -x "$proxy" https://api.ipify.org`)
  before a batch run.

## CLI

```bash
uv run th2api-register --count 3 --label my-agent
```

| Flag | Purpose |
| --- | --- |
| `--count N` | register `N` accounts in one run |
| `--label NAME` | label for the minted key |
| `--email-provider KIND` | `duckmail` or `domain-forward` |
| `--domain DOMAIN` | DuckMail domain or forwarding domain |
| `--forward-to ADDRESS` | DuckMail inbox receiving forwarded mail |
| `--pin SECRET` | DuckMail password or access token for `--forward-to` |
| `--proxy URL` | route both services through a proxy |
| `--timeout SECONDS` | wait for each verification email |
| `--output PATH` | account record path (default `var/accounts.jsonl`) |
| `--email` / `--password` | reuse an existing inbox |

## Stored record

Each run appends one JSON line to `var/accounts.jsonl` (git-ignored):

- `email`, `password`, `fingerprint` (fresh random UUID per run)
- `api_key`, `key_id`, `label`, `free_models_enabled`
- `email_provider`, `forward_to` (null for DuckMail)
- `auth_token`, `access_token`, `refresh_token`, `cookies`,
  `token_expires_at`
- `verified_at`, `created_at`

The file contains plaintext credentials and session tokens. Keep it local.

## Errors

| Exception | Meaning |
| --- | --- |
| `RegistrationError` | pipeline failed; inspect `__cause__` |
| `AccountCapError` | egress IP has used its sign-up allowance |
| `CaptchaRequiredError` | egress IP now requires a Turnstile check |
| `RegionBlockedError` | exit country is not supported for sign-ups |
| `EmailVerificationError` | verification link missing or rejected |
| `ApiKeyError` | key creation or validation failed |
| `EmailProviderError` | provider configuration is invalid |

All of them are exported from `th2api.registration`.
