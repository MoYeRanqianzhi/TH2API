"""End-to-end account registration: inbox -> sign-up -> verify -> API key."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .duckmail import Message
from .providers import DuckMailProvider, EmailProvider, Mailbox
from .routing import extract_verification_url, message_targets_mailbox
from .tokenharbor import (
    AccountCapError,
    CaptchaRequiredError,
    EmailVerificationError,
    RegionBlockedError,
    SignupError,
    TokenHarborClient,
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
    free_models_enabled: bool
    email_provider: str
    forward_to: str | None
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
            "free_models_enabled": self.free_models_enabled,
            "email_provider": self.email_provider,
            "forward_to": self.forward_to,
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
    mail_provider: EmailProvider | None = None,
) -> RegisteredAccount:
    """Create one account and mint a working ``thk_live_…`` key.

    ``mail_provider`` controls where mail comes from; the default is a fresh
    DuckMail inbox. Pass ``DomainForwardProvider`` for a custom domain that
    forwards into one shared DuckMail inbox.

    Order matters: Token Harbor accepts key creation before email
    verification, but such keys are rejected by ``/v1/models``.  The pipeline
    therefore verifies first, grants the free-model consent, and only then
    mints and validates the key.
    """
    provider = mail_provider or DuckMailProvider(proxy=proxy, domain=domain)
    harbor = TokenHarborClient(proxy=proxy)

    mailbox = _prepare_mailbox(provider, email, password)
    email = mailbox.address
    if password is None:
        # A DuckMail inbox's password doubles as the account password, as it
        # always has. A forwarded domain address has no such secret, so give
        # the Token Harbor account its own password.
        password = mailbox.secret if isinstance(provider, DuckMailProvider) else None
        password = password or secrets.token_urlsafe(20)

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
    except RegionBlockedError as exc:
        raise RegistrationError(
            "Token Harbor blocks this egress country or the email service "
            "(region_not_supported). Retry from a supported country."
        ) from exc
    except SignupError as exc:
        raise RegistrationError(f"sign-up failed: {exc}") from exc

    # The session's user id is the routing key for a shared forward inbox: the
    # verification link embeds the same id, so a sibling registration cannot
    # consume this account's mail even if the forwarder rewrote the recipient.
    user_id = harbor.auth_session().user_id

    message = _wait_for_verification(
        provider,
        mailbox,
        user_id,
        started - timedelta(minutes=1),
        timeout=verification_timeout,
    )
    if message is None:
        harbor.request_verification_email()
        message = _wait_for_verification(
            provider,
            mailbox,
            user_id,
            datetime.now(timezone.utc) - timedelta(minutes=1),
            timeout=verification_timeout,
        )
    if message is None:
        raise RegistrationError("verification email did not arrive")

    url = extract_verification_url(message)
    if url is None:
        raise RegistrationError("verification email contained no link")
    if not harbor.verify_email(url):
        # The first link can already be spent; one resend is the documented
        # recovery path in the dashboard banner.
        harbor.request_verification_email()
        newer = _wait_for_verification(
            provider,
            mailbox,
            user_id,
            message.created_at,
            timeout=verification_timeout,
        )
        if newer is None or extract_verification_url(newer) is None:
            raise EmailVerificationError("replacement verification email missing")
        url = extract_verification_url(newer)
        if not harbor.verify_email(url):
            raise EmailVerificationError("verification link was rejected twice")

    # Free routes stay disabled until the account accepts the data-sharing
    # consent; without this step :free model IDs answer free_models_disabled.
    harbor.enable_free_models()

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
        free_models_enabled=True,
        email_provider=mailbox.provider,
        forward_to=mailbox.forward_to,
        auth_token=session.token,
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        cookies=session.cookies,
        token_expires_at=session.expires_at,
        verified_at=datetime.now(timezone.utc),
    )


def _prepare_mailbox(
    provider: EmailProvider,
    email: str | None,
    password: str | None,
) -> Mailbox:
    if email is None:
        return provider.create_mailbox()

    if not isinstance(provider, DuckMailProvider):
        raise RegistrationError(
            "reusing an address is only supported with the duckmail provider"
        )
    if password is None:
        raise RegistrationError("a password is required when email is supplied")
    return provider.mailbox_for(email, password)


def _wait_for_verification(
    provider: EmailProvider,
    mailbox: Mailbox,
    user_id: str,
    after: datetime,
    *,
    timeout: float,
) -> Message | None:
    def matches(message: Message) -> bool:
        return (
            message.created_at > after
            and "verify" in message.subject.lower()
            and message.sender.lower().endswith("tokenharbor.ai")
            and message_targets_mailbox(
                mailbox, message, expected_user_id=user_id
            )
        )

    return provider.wait_for_message(mailbox, matches, timeout=timeout, interval=5.0)
