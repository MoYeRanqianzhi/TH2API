# TH2API

2API gateway for [tokenharbor.ai](https://tokenharbor.ai).

The gateway itself is not built yet. What exists today is the account
registration pipeline it will need: create a disposable inbox, sign up,
confirm the email, and mint a working `thk_live_…` API key.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
```

## Register an account

```bash
uv run th2api-register
```

The command prints the API key once and appends the full record (email,
password, random device fingerprint, API key, and Supabase session tokens) to
`var/accounts.jsonl` (git-ignored). Useful flags:

| Flag | Purpose |
| --- | --- |
| `--count N` | register `N` accounts in one run |
| `--label NAME` | label for the minted key |
| `--proxy URL` | route both services through an HTTP proxy |
| `--domain DOMAIN` | pick a DuckMail domain |
| `--timeout SECONDS` | wait for each verification email |

Programmatic use:

```python
from th2api.registration import register_account, save_account

account = register_account(label="my-agent")
save_account(account)
print(account.api_key)
```

### Registration through a proxy

`proxy` is a first-class argument, not a CLI-only afterthought. The same proxy
covers every request in the pipeline: DuckMail inbox creation and polling, the
Token Harbor sign-up action, the verification link, the free-model consent, and
key creation.

```python
account = register_account(
    label="my-agent",
    proxy="http://user:pass@proxy.example.com:3129",
)
```

```bash
uv run th2api-register --proxy http://user:pass@proxy.example.com:3129
```

The URL must include its scheme (`http://`, `https://`, or `socks5h://`);
percent-encode reserved characters in the credentials. For SOCKS5 use
`socks5h://` (remote DNS) — plain `socks5://` resolves hostnames locally, and
behind a fake-IP/tunnel DNS that hands the proxy an unroutable address:

```bash
uv run th2api-register --proxy "socks5h://user:pass@proxy.example.com:3000"
```

There is no automatic pool rotation — `account_cap` and Turnstile failures
surface as `AccountCapError` / `CaptchaRequiredError` inside
`RegistrationError.__cause__`, so callers can retry with another proxy. See
[docs/registration.md](docs/registration.md) for the retry example and the full
library reference.

## How registration works

1. `api.duckmail.sbs` (mail.tm-compatible) creates a temporary inbox.
2. `POST https://tokenharbor.ai/login?mode=signup` submits the login page's
   React Server Action with a browser-shaped multipart body and a freshly
   random device fingerprint for every run. Supabase creates the user already
   confirmed and returns session cookies.
3. Token Harbor still sends its own verification email. The pipeline polls the
   inbox, opens the `/verify-email?token=…` link with the same session, and
   confirms the redirect lands on `?verify=success`.
4. `POST /api/me/privacy` grants the free-model data-sharing consent, because
   `:free` routes answer `free_models_disabled` until it is accepted.
5. `POST /api/keys` mints the key, which is then checked against
   `GET /v1/models` before being recorded.

The JSONL record keeps the email, password, fingerprint, API key, free-model
consent state, raw session token, access/refresh tokens, and the cookies needed
to resume the session.

Keys created before verification are accepted by the API but rejected by
`/v1/models`, so the pipeline always verifies first.

## Qualification test (on demand)

Registration does not prove the routed model is real. This separate test asks a
thinking model to produce an SVG animation of a pelican riding a bicycle, with
`reasoning_effort: max`, and writes an HTML report for human review:

```bash
uv run th2api-qualify --model deepseek-v4.1-flash:free --effort max
```

The report lands in `var/tests/pelican-*.html` and embeds the model's page in a
sandboxed iframe, the raw output, the reasoning trace, token usage, elapsed
time, and the request id. A normal run thinks for tens of thousands of tokens,
so this is deliberately not part of `th2api-register`.

## Known failures

Both failures below are network-reputation signals from Token Harbor, not
invalid input. Retry from a cleaner network or through a residential proxy.

`account_cap` — the egress IP has already created its allowance of accounts:

```json
{"error": "We can't create another account from this network or device right now.", "code": "account_cap"}
```

Turnstile — after a couple of sign-ups the same IP is asked for a human check:

```text
Please complete the human check to continue.
```

The pipeline raises `AccountCapError` and `CaptchaRequiredError` respectively
so callers can tell these apart from real validation errors.

## Layout

```
src/th2api/registration/
  duckmail.py      temporary inbox client
  tokenharbor.py   sign-up server action, verification, API keys
  flow.py          end-to-end pipeline
  store.py         local account records
  cli.py           th2api-register entry point
src/th2api/qualification/
  pelican.py       on-demand pelican SVG test and HTML report
  cli.py           th2api-qualify entry point
```
