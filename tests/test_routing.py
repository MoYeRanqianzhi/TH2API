"""Routing tests for concurrent registrations sharing one forward inbox."""

from __future__ import annotations

import base64
import unittest
from datetime import datetime, timezone

from th2api.registration.duckmail import Message
from th2api.registration.providers import DomainForwardProvider, Mailbox
from th2api.registration.routing import (
    message_targets_mailbox,
    verification_user_id,
)


def token_for(user_id: str) -> str:
    raw = f"{user_id}.1793793459803.signature".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def message(
    *,
    recipients: tuple[str, ...] = (),
    user_id: str = "",
    subject: str = "Verify your email",
) -> Message:
    body = ""
    if user_id:
        body = f"https://tokenharbor.ai/verify-email?token={token_for(user_id)}"
    return Message(
        id="m1",
        subject=subject,
        sender="verify@tokenharbor.ai",
        created_at=datetime.now(timezone.utc),
        text=body,
        html="",
        to=recipients,
    )


class RoutingTest(unittest.TestCase):
    def test_recipient_header_wins(self) -> None:
        mailbox = Mailbox(address="a@example.com", provider="domain-forward")
        self.assertTrue(
            message_targets_mailbox(
                mailbox, message(recipients=("a@example.com",))
            )
        )
        self.assertFalse(
            message_targets_mailbox(
                mailbox, message(recipients=("b@example.com",))
            )
        )

    def test_rewritten_recipient_falls_back_to_user_id(self) -> None:
        mailbox = Mailbox(address="a@example.com", provider="domain-forward")
        forwarded = message(recipients=("inbox@duckmail.test",), user_id="user-a")
        self.assertTrue(
            message_targets_mailbox(
                mailbox, forwarded, expected_user_id="user-a"
            )
        )
        self.assertFalse(
            message_targets_mailbox(
                mailbox, forwarded, expected_user_id="user-b"
            )
        )

    def test_two_simultaneous_signups_do_not_cross(self) -> None:
        box_a = Mailbox(address="a@example.com", provider="domain-forward")
        box_b = Mailbox(address="b@example.com", provider="domain-forward")
        mail_a = message(recipients=("inbox@duckmail.test",), user_id="user-a")
        mail_b = message(recipients=("inbox@duckmail.test",), user_id="user-b")

        self.assertTrue(
            message_targets_mailbox(box_a, mail_a, expected_user_id="user-a")
        )
        self.assertFalse(
            message_targets_mailbox(box_a, mail_b, expected_user_id="user-a")
        )
        self.assertTrue(
            message_targets_mailbox(box_b, mail_b, expected_user_id="user-b")
        )
        self.assertFalse(
            message_targets_mailbox(box_b, mail_a, expected_user_id="user-b")
        )

    def test_unaddressed_mail_is_not_claimed_without_user_id(self) -> None:
        mailbox = Mailbox(address="a@example.com", provider="domain-forward")
        self.assertFalse(
            message_targets_mailbox(mailbox, message(recipients=()))
        )

    def test_decode_user_id(self) -> None:
        url = f"https://tokenharbor.ai/verify-email?token={token_for('abc')}"
        self.assertEqual(verification_user_id(url), "abc")


class StubDuckMail:
    """Stands in for the shared forward inbox during provider tests."""

    def __init__(self, pages: dict[int, list[Message]]) -> None:
        self.pages = pages
        self.requested: list[int] = []

    def access_token(self, address: str, pin: str) -> str:
        return "token"

    def messages(self, token: str, page: int = 1) -> list[Message]:
        self.requested.append(page)
        return self.pages.get(page, [])


class ForwardQueueTest(unittest.TestCase):
    def test_pages_are_walked_without_losing_a_burst(self) -> None:
        provider = DomainForwardProvider(
            "example.com", "inbox@duckmail.test", "pin"
        )
        mailbox = provider.create_mailbox()
        now = datetime.now(timezone.utc)
        first = message(recipients=("a@example.com",))
        second = message(recipients=("b@example.com",))
        # Both signs-up land in the same inbox within one poll interval.
        stub = StubDuckMail({1: [first], 2: [second]})
        provider._client = stub
        provider._token_value = "token"

        result = provider.list_messages(mailbox)

        self.assertEqual(len(result), 2)
        self.assertEqual(stub.requested, [1, 2, 3])

    def test_old_messages_are_ignored(self) -> None:
        provider = DomainForwardProvider(
            "example.com", "inbox@duckmail.test", "pin"
        )
        mailbox = provider.create_mailbox()
        stale = Message(
            id="old",
            subject="Verify your email",
            sender="verify@tokenharbor.ai",
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            text="",
            html="",
        )
        provider._client = StubDuckMail({1: [stale]})
        provider._token_value = "token"

        self.assertEqual(provider.list_messages(mailbox), [])


if __name__ == "__main__":
    unittest.main()
