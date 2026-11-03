"""DuckMail temporary-inbox client.

DuckMail exposes the mail.tm API shape under https://api.duckmail.sbs:

* ``GET  /domains``   -> ``{"hydra:member": [{"domain": ...}]}``
* ``POST /accounts``  -> create an inbox from ``{"address", "password"}``
* ``POST /token``     -> exchange the same credentials for a bearer token
* ``GET  /messages``  -> inbox listing, authenticated with that token
* ``GET  /messages/{id}`` -> full message including ``text`` and ``html``

Inboxes are disposable: DuckMail expires them after roughly 24 hours, which is
enough for a registration run but not for keeping the address afterwards.
"""

from __future__ import annotations

import secrets
import string
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from curl_cffi import requests

DEFAULT_BASE_URL = "https://api.duckmail.sbs"


def proxy_map(proxy: str | None) -> dict[str, str] | None:
    """Translate a single proxy URL into curl_cffi's per-scheme mapping."""
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


class DuckMailError(RuntimeError):
    """Raised when DuckMail rejects a request or returns an unexpected shape."""


@dataclass(frozen=True)
class Message:
    id: str
    subject: str
    sender: str
    created_at: datetime
    text: str
    html: str


class DuckMailClient:
    """Small synchronous client; one instance owns one HTTP session."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        proxy: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session(
            impersonate="chrome", proxies=proxy_map(proxy)
        )

    def domains(self) -> list[str]:
        response = self._session.get(f"{self.base_url}/domains", timeout=self.timeout)
        if response.status_code != 200:
            raise DuckMailError(f"GET /domains failed: {response.status_code}")
        return [
            entry["domain"]
            for entry in response.json().get("hydra:member", [])
            if entry.get("domain")
        ]

    def create_inbox(
        self,
        *,
        domain: str | None = None,
        local_part: str | None = None,
        password: str | None = None,
    ) -> tuple[str, str]:
        """Create a random inbox and return ``(address, password)``.

        DuckMail rejects a duplicate address, so a random local part is used by
        default; callers that need a deterministic address can pass both parts.
        """
        if domain is None:
            domains = self.domains()
            if not domains:
                raise DuckMailError("DuckMail returned no domains")
            domain = domains[0]
        if local_part is None:
            alphabet = string.ascii_lowercase + string.digits
            local_part = "th" + "".join(secrets.choice(alphabet) for _ in range(10))
        if password is None:
            password = secrets.token_urlsafe(18)

        address = f"{local_part}@{domain}"
        response = self._session.post(
            f"{self.base_url}/accounts",
            json={"address": address, "password": password},
            timeout=self.timeout,
        )
        if response.status_code not in (200, 201):
            raise DuckMailError(
                f"POST /accounts failed for {address}: "
                f"{response.status_code} {response.text[:200]}"
            )
        return address, password

    def token(self, address: str, password: str) -> str:
        response = self._session.post(
            f"{self.base_url}/token",
            json={"address": address, "password": password},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise DuckMailError(
                f"POST /token failed for {address}: "
                f"{response.status_code} {response.text[:200]}"
            )
        return response.json()["token"]

    def messages(self, token: str) -> list[Message]:
        """Newest messages are not guaranteed to be first; callers sort."""
        response = self._session.get(
            f"{self.base_url}/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"page": 1},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise DuckMailError(f"GET /messages failed: {response.status_code}")
        return [
            self._to_message(entry)
            for entry in response.json().get("hydra:member", [])
        ]

    def message(self, token: str, message_id: str) -> Message:
        response = self._session.get(
            f"{self.base_url}/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise DuckMailError(
                f"GET /messages/{message_id} failed: {response.status_code}"
            )
        return self._to_message(response.json(), include_body=True)

    def wait_for_message(
        self,
        token: str,
        predicate: Callable[[Message], bool],
        *,
        timeout: float = 180.0,
        interval: float = 5.0,
    ) -> Message | None:
        """Poll the inbox until ``predicate`` matches or the timeout elapses.

        Returns the matched message, or ``None`` on timeout. Polling is
        deliberately simple: the API has no push channel.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in sorted(
                self.messages(token), key=lambda item: item.created_at, reverse=True
            ):
                if predicate(message):
                    return self.message(token, message.id)
            time.sleep(interval)
        return None

    @staticmethod
    def _to_message(entry: dict, *, include_body: bool = False) -> Message:
        html = entry.get("html") or ""
        if isinstance(html, list):
            html = "".join(html)
        created = entry.get("createdAt")
        try:
            created_at = datetime.fromisoformat(
                created.replace("Z", "+00:00")
            ) if created else datetime.now(timezone.utc)
        except ValueError:
            created_at = datetime.now(timezone.utc)
        sender = entry.get("from") or {}
        return Message(
            id=entry.get("id", ""),
            subject=entry.get("subject", ""),
            sender=sender.get("address", "") if isinstance(sender, dict) else "",
            created_at=created_at,
            text=entry.get("text", "") if include_body else (entry.get("intro") or ""),
            html=html if include_body else "",
        )
