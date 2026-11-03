"""Durable local record of registered accounts.

Token Harbor shows an API key only once, and the Supabase session behind it
rotates, so every field needed to reuse the account is written down.  Records
are append-only JSONL: concurrent registration runs never rewrite the file, and
a crashed run cannot truncate earlier accounts.

The file lives under ``var/`` and is git-ignored; it contains plaintext
passwords, session tokens, and API keys.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .flow import RegisteredAccount

DEFAULT_PATH = Path("var/accounts.jsonl")


def save_account(account: RegisteredAccount, path: Path | str = DEFAULT_PATH) -> Path:
    """Append one JSON record and return the file path."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(account.to_record(), ensure_ascii=False) + "\n"
    created = not destination.exists()
    with destination.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)
    if created:
        os.chmod(destination, 0o600)
    return destination
