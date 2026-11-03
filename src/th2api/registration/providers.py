"""Email providers for the registration pipeline.

The pipeline only needs three operations from an address: create one, list its
recent messages, and fetch a full message. ``EmailProvider`` is that contract;
DuckMail and a custom-domain forwarder implement it, and a deployment can add
Outlook (IMAP/Graph), Gmail, or anything else without touching the registration
flow.

Concurrency note: a domain forwarder funnels many registrations into one
inbox. Providers therefore never delete or "consume" messages. Each mailbox
filters by its own address and creation time, and the flow additionally checks
the verification token's user id, so two simultaneous registrations can poll
the same inbox without stealing each other's mail.
"""

from __future__ import annotations

import secrets
import string
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .duckmail import DuckMailClient, Message


class EmailProviderError(RuntimeError):
    """The configured provider cannot create or read mailboxes."""


@dataclass(frozen=True)
class Mailbox:
    address: str
    provider: str
    # DuckMail password or access token; never used as the Token Harbor
    # password for a forwarded domain address.
    secret: str | None = None
    forward_to: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EmailProvider(ABC):
    """Standard mailbox interface; subclasses implement the three operations."""

    name: str = "email"

    @abstractmethod
    def create_mailbox(self) -> Mailbox:
        """Return a fresh address that can receive the verification email."""

    @abstractmethod
    def list_messages(self, mailbox: Mailbox) -> list[Message]:
        """Recent messages for ``mailbox``; must not delete or mutate them."""

    @abstractmethod
    def get_message(self, mailbox: Mailbox, message_id: str) -> Message:
        """Full message, including body, for a listing entry."""

    def wait_for_message(
        self,
        mailbox: Mailbox,
        predicate,
        *,
        timeout: float = 180.0,
        interval: float = 5.0,
    ) -> Message | None:
        """Poll without consuming anything until ``predicate`` matches.

        A shared inbox may receive several registrations' mail at once; a
        non-matching message is simply skipped and stays available to the
        mailbox that owns it.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in sorted(
                self.list_messages(mailbox),
                key=lambda item: item.created_at,
                reverse=True,
            ):
                if predicate(message):
                    return self.get_message(mailbox, message.id)
            time.sleep(interval)
        return None


class DuckMailProvider(EmailProvider):
    """Default provider: DuckMail issues a disposable inbox over its API."""

    name = "duckmail"

    def __init__(self, *, proxy: str | None = None, domain: str | None = None) -> None:
        self._client = DuckMailClient(proxy=proxy)
        self._domain = domain
        self._tokens: dict[tuple[str, str], str] = {}

    def create_mailbox(self) -> Mailbox:
        address, password = self._client.create_inbox(domain=self._domain)
        return Mailbox(address=address, provider=self.name, secret=password)

    def mailbox_for(self, address: str, password: str) -> Mailbox:
        """Reuse an existing DuckMail inbox (the ``--email/--password`` path)."""
        return Mailbox(address=address, provider=self.name, secret=password)

    def _token(self, mailbox: Mailbox) -> str:
        if not mailbox.secret:
            raise EmailProviderError("DuckMail mailbox has no credential")
        key = (mailbox.address, mailbox.secret)
        if key not in self._tokens:
            self._tokens[key] = self._client.token(*key)
        return self._tokens[key]

    def list_messages(self, mailbox: Mailbox) -> list[Message]:
        return self._client.messages(self._token(mailbox))

    def get_message(self, mailbox: Mailbox, message_id: str) -> Message:
        return self._client.message(self._token(mailbox), message_id)


class DomainForwardProvider(EmailProvider):
    """Custom domain whose catch-all forwards into one DuckMail inbox.

    ``pin`` is the DuckMail credential for ``forward_to``: either the inbox
    password or an access token (JWT). The generated ``<random>@domain``
    addresses never exist in DuckMail themselves; mail arrives only through the
    forwarder, so routing happens on the recipient header and the verification
    token's user id.
    """

    name = "domain-forward"

    def __init__(
        self,
        domain: str,
        forward_to: str,
        pin: str,
        *,
        proxy: str | None = None,
    ) -> None:
        domain = domain.strip().strip(".")
        if not domain or "@" in domain:
            raise EmailProviderError(f"invalid domain: {domain!r}")
        if "@" not in forward_to:
            raise EmailProviderError(f"invalid forward address: {forward_to!r}")
        self._client = DuckMailClient(proxy=proxy)
        self._domain = domain
        self._forward_to = forward_to.strip()
        self._pin = pin
        self._token_value: str | None = None

    def create_mailbox(self) -> Mailbox:
        alphabet = string.ascii_lowercase + string.digits
        local = "th" + "".join(secrets.choice(alphabet) for _ in range(10))
        return Mailbox(
            address=f"{local}@{self._domain}",
            provider=self.name,
            secret=self._pin,
            forward_to=self._forward_to,
        )

    def _token(self) -> str:
        if self._token_value is None:
            self._token_value = self._client.access_token(
                self._forward_to, self._pin
            )
        return self._token_value

    def list_messages(self, mailbox: Mailbox) -> list[Message]:
        cutoff = mailbox.created_at - timedelta(minutes=1)
        collected: list[Message] = []
        # The shared inbox can hold a burst of simultaneous sign-ups; walk a
        # few pages rather than hoping everything fits on the first one.
        for page in range(1, 4):
            batch = self._client.messages(self._token(), page=page)
            if not batch:
                break
            collected.extend(item for item in batch if item.created_at >= cutoff)
            if min(item.created_at for item in batch) < cutoff:
                break
        return collected

    def get_message(self, mailbox: Mailbox, message_id: str) -> Message:
        return self._client.message(self._token(), message_id)
