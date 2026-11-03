"""Routing verification mail to the right registration.

A domain forwarder puts every registration's mail into one inbox, and two
sign-ups can produce two messages within seconds.  Recipient headers usually
survive forwarding, but not every forwarder preserves them, so the verification
token itself is the second key: its first segment is the Token Harbor user id,
which is unique per registration.
"""

from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, urlparse

from .duckmail import Message
from .providers import Mailbox

VERIFICATION_URL = re.compile(
    r"https?://[^\s\"'<>]+/verify-email\?token=[A-Za-z0-9_\-=%.]+"
)


def extract_verification_url(message: Message) -> str | None:
    match = VERIFICATION_URL.search(f"{message.text}\n{message.html}")
    return match.group(0) if match else None


def verification_user_id(url: str) -> str | None:
    """Decode the user id baked into a ``/verify-email?token=`` link.

    The token is base64 of ``<user-id>.<timestamp>.<signature>``.
    """
    token = parse_qs(urlparse(url).query).get("token", [None])[0]
    if not token:
        return None
    padded = token + "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    user_id = decoded.split(".", 1)[0]
    return user_id or None


def message_targets_mailbox(
    mailbox: Mailbox,
    message: Message,
    *,
    expected_user_id: str | None = None,
) -> bool:
    """True when ``message`` belongs to this registration, not a sibling's.

    Works for both routing keys: a preserved ``To`` header, or the user id in
    the verification link when the forwarder rewrote the recipient.
    """
    recipients = {item.casefold() for item in message.to if item}
    if mailbox.address.casefold() in recipients:
        return True

    if expected_user_id:
        url = extract_verification_url(message)
        if url and verification_user_id(url) == expected_user_id:
            return True
    return False
