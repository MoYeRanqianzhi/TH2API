"""End-to-end account registration: inbox -> sign-up -> verify -> API key."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .duckmail import DuckMailClient, Message
from .tokenharbor import (
    AccountCapError,
    CaptchaRequiredError,
    EmailVerificationError,
    SignupError,
    TokenHarborClient,
)

VERIFICATION_URL = re.compile(
    r"https?://[^\s\"'<>]+/verify-email\?token=[A-Za-z0-9_\-=%.]+"
)


class RegistrationError(RuntimeError):
    """The pipeline could not produce a usable account and API key."""


@dataclass(frozen=True)
class RegisteredAccount:
    email: str
    password: str
    fingerprint: str
    api_key: str
    key_id: str
    label: str
    auth_token: str
    access_token: str
    refresh_token: str
    cookies: dict[str, str]
    token_expires_at: int | None
    verified_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_record(self) -> dict:
        return {
            "email": self.email,
            "password": self.password,
            "fingerprint": self.fingerprint,
            "api_key": self.api_key,
            "key_id": self.key_id,
            "label": self.label,
            "auth_token": self.auth_token,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "cookies": self.cookies,
            "token_expires_at": self.token_expires_at,
            "verified_at": self.verified_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }


def register_account(
    *,
    email: str | None = None,
    password: str | None = None,
    label: str = "th2api",
    domain: str | None = None,
    proxy: str | None = None,
    verification_timeout: float = 180.0,
) -> RegisteredAccount:
    """Create one account and mint a working ``thk_live_…`` key.

    Order matters: Token Harbor accepts key creation before email
    verification, but such keys are rejected by ``/v1/models``.  The pipeline
    therefore verifies first and only then mints and validates the key.
    """
    duck = DuckMailClient(proxy=proxy)
    harbor = TokenHarborClient(proxy=proxy)

    if email is None:
        email, password = duck.create_inbox(domain=domain)
    elif password is None:
        raise RegistrationError("a password is required when email is supplied")

    started = datetime.now(timezone.utc)
    try:
        fingerprint = harbor.sign_up(email, password)
    except AccountCapError as exc:
        raise RegistrationError(
            "Token Harbor refused the sign-up: the egress IP has already "
            "created too many accounts (account_cap). Retry through a cleaner "
            "proxy or another network."
        ) from exc
    except CaptchaRequiredError as exc:
        raise RegistrationError(
            "Token Harbor requires a Cloudflare Turnstile check for this "
            "network; the IP has likely used up its sign-up allowance. Retry "
            "from another clean network."
        ) from exc
    except SignupError as exc:
        raise RegistrationError(f"sign-up failed: {exc}") from exc

    token = duck.token(email, password)
    message = _wait_for_verification(
        duck, token, started - timedelta(minutes=1), timeout=verification_timeout
    )
    if message is None:
        harbor.request_verification_email()
        message = _wait_for_verification(
            duck,
            token,
            datetime.now(timezone.utc) - timedelta(minutes=1),
            timeout=verification_timeout,
        )
    if message is None:
        raise RegistrationError("verification email did not arrive")

    url = _extract_url(message)
    if url is None:
        raise RegistrationError("verification email contained no link")
    if not harbor.verify_email(url):
        # The first link can already be spent; one resend is the documented
        # recovery path in the dashboard banner.
        harbor.request_verification_email()
        newer = _wait_for_verification(
            duck, token, message.created_at, timeout=verification_timeout
        )
        if newer is None or _extract_url(newer) is None:
            raise EmailVerificationError("replacement verification email missing")
        url = _extract_url(newer)
        if not harbor.verify_email(url):
            raise EmailVerificationError("verification link was rejected twice")

    key = harbor.create_api_key(label)
    if not harbor.validate_api_key(key.plaintext):
        raise RegistrationError(
            "key was created but /v1/models rejected it; the account is not "
            "fully verified"
        )

    # Read the session after verification and key creation: Supabase rotates
    # the cookie pair during those calls, and the last value is the one a
    # later gateway can actually reuse.
    session = harbor.auth_session()

    return RegisteredAccount(
        email=email,
        password=password,
        fingerprint=fingerprint,
        api_key=key.plaintext,
        key_id=key.id,
        label=key.label,
        auth_token=session.token,
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        cookies=session.cookies,
        token_expires_at=session.expires_at,
        verified_at=datetime.now(timezone.utc),
    )


def _wait_for_verification(
    duck: DuckMailClient,
    token: str,
    after: datetime,
    *,
    timeout: float,
) -> Message | None:
    def matches(message: Message) -> bool:
        return (
            message.created_at > after
            and "verify" in message.subject.lower()
            and message.sender.lower().endswith("tokenharbor.ai")
        )

    return duck.wait_for_message(token, matches, timeout=timeout, interval=5.0)


def _extract_url(message: Message) -> str | None:
    match = VERIFICATION_URL.search(f"{message.text}\n{message.html}")
    return match.group(0) if match else None
