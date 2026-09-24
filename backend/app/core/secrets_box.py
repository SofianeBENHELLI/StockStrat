"""Encryption at rest for the secrets kept in the database.

Credentials entered in the Administration panel (Alpaca keys, the ntfy topic,
a Telegram token) live in `app_settings`. Stored in clear, anyone who got a copy
of the database file — a backup, a zip sent for help, another account on the
machine — would get the broker keys with it. So secret values are encrypted
(Fernet: AES-128-CBC + HMAC-SHA256) with a key kept *outside* the database:

- `STOCKSTRAT_SECRET_KEY` in the environment (servers, containers), or
- `backend/.secret_key`, generated on first use, readable by the owner only.

The database and its backups therefore never contain a usable credential on
their own. The flip side is deliberate: lose the key and the stored secrets are
unrecoverable — they read as "not configured" and must be entered again, which
is a minute of work, not a data loss.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("secrets")
PREFIX = "enc:v1:"
KEY_FILE = Path(__file__).resolve().parents[2] / ".secret_key"


def restrict(path: Path) -> None:
    """Owner read/write only. Best effort: some filesystems ignore modes."""
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except OSError:
        pass


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = os.environ.get("STOCKSTRAT_SECRET_KEY", "").strip()
    if not key:
        if not KEY_FILE.exists():
            KEY_FILE.write_bytes(Fernet.generate_key())
        restrict(KEY_FILE)
        key = KEY_FILE.read_text().strip()
    return Fernet(key.encode())


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plain: str) -> str:
    return PREFIX + _fernet().encrypt(plain.encode()).decode()


def decrypt(value: object) -> str | None:
    """Plaintext for an encrypted value; legacy plaintext is returned as-is so
    the migration can find and encrypt it. A value that no longer decrypts
    (the key was replaced) reads as missing rather than raising."""
    if value is None or value == "":
        return None
    if not is_encrypted(value):
        return str(value)
    try:
        return _fernet().decrypt(str(value)[len(PREFIX):].encode()).decode()
    except InvalidToken:
        log.warning("a stored secret could not be decrypted with the current key; treating it as unset")
        return None
