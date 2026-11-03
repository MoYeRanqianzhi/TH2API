"""Token Harbor account registration primitives.

Token Harbor is a Next.js application whose auth forms are React Server
Actions, not plain REST endpoints.  A browser submits ``/login?mode=signup``
with a ``Next-Action`` header and a multipart body in React's server-action
encoding; the response is an RSC flight stream.  The helper below reproduces
that request byte-for-byte, which is why the field names look unusual.

Two quirks matter for automation:

* Supabase creates the user in an already-confirmed state, so the sign-up
  action returns a live session (``sb-auth-auth-token.0/.1`` cookies).  The
  product still requires its *own* email verification before API keys work:
  keys minted before that step are silently unusable (``401``).
* The sign-up action answers ``{"code": "account_cap"}`` when the requesting
  network has already created too many accounts.  It is an IP-reputation
  signal, not a problem with the submitted email or password.

Dashboard features that are not part of sign-up (key listing and creation)
are plain authenticated JSON endpoints under ``/api``.
"""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from curl_cffi import requests

from .duckmail import proxy_map

DEFAULT_BASE_URL = "https://tokenharbor.ai"

# Server-action ids are build artefacts of the deployed Next.js bundle.  They
# live in the login page's client chunks and can change on a redeploy; if
# sign-up starts failing with a generic error, re-read the current ids from
# ``/_next/static/immutable/chunks/*.js`` on the login page.
SIGNUP_ACTION_ID = "60c5d7a61c5d71d0085a79db83f9b0423db7d50ff5"

# Stored with the consent POST; the dashboard sends the modal text it rendered.
FREE_MODEL_CONSENT = (
    "Token Harbor may keep free-route requests and responses for diagnostics, "
    "safety and model evaluation, and providers serving free models may use "
    "them under their own terms."
)

# The router state the browser sends with a form action on /login.
LOGIN_ROUTER_STATE = json.dumps(
    [
        "",
        {"children": ["login", {"children": ["__PAGE__", {}, None, None, 0]}, None, None, 0]},
        None,
        None,
        20,
    ]
)


class TokenHarborError(RuntimeError):
    """Base class for errors raised by the Token Harbor client."""


class SignupError(TokenHarborError):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class AccountCapError(SignupError):
    """The network/device has exhausted Token Harbor's sign-up allowance."""


class CaptchaRequiredError(SignupError):
    """Token Harbor wants a Cloudflare Turnstile token for this egress IP.

    The sign-up action answers "Please complete the human check to continue."
    without an error code.  The site raises this after an IP has created a
    couple of accounts, so it is a network-reputation signal like
    :class:`AccountCapError`, not a malformed request.
    """


class RegionBlockedError(SignupError):
    """Token Harbor refuses the egress country or the email service.

    The action answers "Your IP or email service is not supported for Token
    Harbor accounts" and points at the connection's exit country.
    """


class EmailVerificationError(TokenHarborError):
    """The verification link was missing, expired, or already consumed."""


class ApiKeyError(TokenHarborError):
    """Key creation or validation failed."""


@dataclass(frozen=True)
class ApiKey:
    id: str
    label: str
    prefix: str
    last4: str
    plaintext: str


@dataclass(frozen=True)
class AuthSession:
    """The Supabase session created by sign-up, in every useful shape.

    ``cookies`` is what a browser would replay; ``token`` is the raw chunked
    cookie value; the decoded fields let a later gateway refresh the session
    without fetching the dashboard first.
    """

    cookies: dict[str, str]
    token: str
    access_token: str
    refresh_token: str
    expires_at: int | None
    user_id: str


class TokenHarborClient:
    """One signed-in (or sign-up) browser session against tokenharbor.ai."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        proxy: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session(
            impersonate="chrome", proxies=proxy_map(proxy)
        )

    @property
    def cookies(self) -> dict[str, str]:
        return dict(self._session.cookies.get_dict())

    def sign_up(
        self,
        email: str,
        password: str,
        *,
        fingerprint: str | None = None,
        timezone: str = "Asia/Shanghai",
        invite_code: str = "",
    ) -> str:
        """Create an account and leave the client holding its auth cookies.

        Returns the device fingerprint that was submitted so callers can store
        it alongside the account.  Raises :class:`AccountCapError` when the
        egress IP has already been used for too many sign-ups.
        """
        fingerprint = fingerprint or str(uuid.uuid4())
        fields = {
            "device_fingerprint": fingerprint,
            "timezone": timezone,
            "next": "",
            "email": email,
            "password": password,
            "invite_code": invite_code,
        }
        text = self._server_action(SIGNUP_ACTION_ID, fields)
        error = _action_error(text)
        if error is not None:
            message = error.get("error", "sign-up failed")
            code = error.get("code")
            if code == "account_cap":
                raise AccountCapError(message, code)
            if "human check" in message.lower():
                raise CaptchaRequiredError(message, code)
            if "not supported for token harbor accounts" in message.lower():
                raise RegionBlockedError(message, code)
            raise SignupError(message, code)
        if not any(name.startswith("sb-auth-auth-token") for name in self.cookies):
            raise SignupError("sign-up returned no session cookies")
        return fingerprint

    def enable_free_models(
        self,
        *,
        screen: str = "consent_modal",
        path: str = "/dashboard",
    ) -> bool:
        """Turn on the ``:free`` routes by granting the data-sharing consent.

        New accounts have free models *off* until the user accepts that
        free-route conversations may be retained for diagnostics and model
        evaluation.  The dashboard modal performs a single privacy POST, so
        registration does the same and returns ``True`` when the consent was
        newly granted.
        """
        privacy = self._session.get(
            f"{self.base_url}/api/me/privacy", timeout=self.timeout
        )
        if privacy.status_code != 200:
            raise TokenHarborError(
                f"privacy lookup failed: {privacy.status_code} {privacy.text[:200]}"
            )
        if privacy.json().get("free_models_enabled"):
            return False

        response = self._session.post(
            f"{self.base_url}/api/me/privacy",
            json={
                "free_models_enabled": True,
                "shown_text": FREE_MODEL_CONSENT,
                "screen": screen,
                "path": path,
            },
            timeout=self.timeout,
        )
        if response.status_code != 200 or not response.json().get(
            "free_models_enabled"
        ):
            raise TokenHarborError(
                f"free-model consent failed: "
                f"{response.status_code} {response.text[:200]}"
            )
        return True

    def request_verification_email(self) -> None:
        """Ask the dashboard endpoint to send a fresh verification link.

        Calling this invalidates outstanding links, so it is only used when no
        mail arrived or the previous link was rejected.
        """
        response = self._session.post(
            f"{self.base_url}/api/me/send-verification-email", timeout=self.timeout
        )
        if response.status_code != 200 or not response.json().get("ok"):
            raise EmailVerificationError(
                f"could not send verification email: "
                f"{response.status_code} {response.text[:200]}"
            )

    def verify_email(self, url: str) -> bool:
        """Open a verification link and report whether the site accepted it.

        The page is authenticated: it redirects anonymous visitors to /login,
        so the call has to reuse the sign-up session.
        """
        response = self._session.get(url, timeout=self.timeout, allow_redirects=True)
        query = parse_qs(urlparse(response.url).query)
        if query.get("verify") == ["success"]:
            return True
        return "Email verified" in response.text

    def list_api_keys(self) -> list[dict]:
        response = self._session.get(f"{self.base_url}/api/keys", timeout=self.timeout)
        if response.status_code != 200:
            raise ApiKeyError(
                f"key listing failed: {response.status_code} {response.text[:200]}"
            )
        return response.json().get("keys", [])

    def create_api_key(self, label: str) -> ApiKey:
        """Mint a key.  Must happen *after* email verification, see module doc."""
        response = self._session.post(
            f"{self.base_url}/api/keys",
            json={"label": label},
            timeout=self.timeout,
        )
        if response.status_code not in (200, 201):
            raise ApiKeyError(
                f"key creation failed: {response.status_code} {response.text[:200]}"
            )
        payload = response.json()
        if "plaintext" not in payload:
            raise ApiKeyError("key creation returned no plaintext key")
        key = payload["key"]
        return ApiKey(
            id=key["id"],
            label=key.get("label", label),
            prefix=key.get("prefix", ""),
            last4=key.get("last4", ""),
            plaintext=payload["plaintext"],
        )

    def validate_api_key(self, api_key: str) -> bool:
        """A key only counts once /v1/models accepts it."""
        response = self._session.get(
            f"{self.base_url}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=self.timeout,
        )
        return response.status_code == 200

    def auth_session(self) -> AuthSession:
        """Extract the Supabase session currently held by the session cookies.

        Supabase splits large cookies into ``sb-auth-auth-token.0``,
        ``.1`` … and prefixes the first chunk with ``base64-``.  Concatenating
        the chunks in order recreates the base64 JSON document that holds the
        access and refresh tokens.
        """
        cookies = self.cookies
        chunks = sorted(
            (name, value)
            for name, value in cookies.items()
            if name.startswith("sb-auth-auth-token")
        )
        if not chunks:
            raise TokenHarborError("no auth session in cookies")

        token = "".join(value for _, value in chunks)
        encoded = token.removeprefix("base64-")
        payload: dict = {}
        try:
            decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            payload = json.loads(decoded)
        except (ValueError, json.JSONDecodeError):
            # A malformed cookie should not lose the raw value; callers can
            # still replay the cookies themselves.
            pass

        access_token = payload.get("access_token", "")
        return AuthSession(
            cookies=cookies,
            token=token,
            access_token=access_token,
            refresh_token=payload.get("refresh_token", ""),
            expires_at=payload.get("expires_at"),
            user_id=_jwt_user_id(access_token),
        )

    def _server_action(
        self,
        action_id: str,
        fields: dict[str, str],
        *,
        path: str = "/login?mode=signup",
    ) -> str:
        """POST a React Server Action and return the raw RSC response text.

        The wire format has three parts: bookkeeping fields that tell React
        which action to run and how its arguments were serialised, the form
        fields themselves (prefixed with the argument index ``_1_``), and the
        serialised argument array ``["$undefined","$K1"]`` under the name
        ``0``.
        """
        boundary = "----WebKitFormBoundary" + secrets.token_hex(8)
        parts = [
            _form_part(boundary, "_1_$ACTION_REF_1", ""),
            _form_part(
                boundary,
                "_1_$ACTION_1:0",
                json.dumps({"id": action_id, "bound": "$@1"}),
            ),
            _form_part(boundary, "_1_$ACTION_1:1", '["$undefined"]'),
            _form_part(boundary, "_1_$ACTION_KEY", "k" + secrets.token_hex(16)),
        ]
        parts.extend(
            _form_part(boundary, f"_1_{name}", value)
            for name, value in fields.items()
        )
        parts.append(_form_part(boundary, "0", '["$undefined","$K1"]'))
        body = "".join(parts) + f"--{boundary}--\r\n"

        response = self._session.post(
            f"{self.base_url}{path}",
            headers={
                "next-action": action_id,
                "accept": "text/x-component",
                "origin": self.base_url,
                "referer": f"{self.base_url}{path}",
                "next-router-state-tree": LOGIN_ROUTER_STATE,
                "content-type": f"multipart/form-data; boundary={boundary}",
            },
            data=body.encode(),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise TokenHarborError(
                f"server action failed: {response.status_code} {response.text[:200]}"
            )
        return response.text


def _form_part(boundary: str, name: str, value: str) -> str:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    )


def _action_error(text: str) -> dict | None:
    """Pull ``1:{"error": ..., "code": ...}`` out of an RSC flight response.

    Successful action responses start their line ``1:`` with a chunk path or
    module reference, so only JSON objects carrying an ``error`` key count.
    """
    for line in text.splitlines():
        if not line.startswith("1:"):
            continue
        try:
            payload = json.loads(line[2:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "error" in payload:
            return payload
    return None


def _jwt_user_id(access_token: str) -> str:
    """Read the ``sub`` claim without verifying the signature.

    The token came from Supabase over TLS and is used only to route mail to
    the session that created it, so signature verification adds nothing here.
    """
    try:
        part = access_token.split(".")[1]
        payload = json.loads(
            base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        )
    except (IndexError, ValueError, json.JSONDecodeError):
        return ""
    return str(payload.get("sub", ""))
