"""RFC 6238 TOTP codes (the 6-digit Instagram 2FA code), stdlib only."""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time


def _key(secret_b32: str) -> bytes:
    s = "".join(secret_b32.split()).upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def totp(secret_b32: str, at: float | None = None, digits: int = 6, step: int = 30) -> str:
    """The TOTP code for ``secret_b32`` at unix time ``at`` (default: now)."""
    counter = int((time.time() if at is None else at) // step)
    mac = hmac.new(_key(secret_b32), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def seconds_left(at: float | None = None, step: int = 30) -> float:
    now = time.time() if at is None else at
    return step - (now % step)
