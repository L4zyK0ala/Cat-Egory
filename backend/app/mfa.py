"""Time-based one-time passwords (TOTP, RFC 6238) for staff sign-in, standard library only.

Any authenticator app works (Aegis, Google Authenticator, 1Password ...): SHA-1, 6 digits, 30-second steps; a code
from the step before or after is accepted for clock drift, and a step is never accepted twice (replay).

The shared secret is stored encrypted: XOR with a keystream from HMAC-SHA256(app secret, random nonce), so a copy
of the database (a backup) alone does not reveal it. The app secret is ``CATEGORY_FINGERPRINT_SECRET``; changing it
makes stored secrets unreadable, and the users enrol again. Backup codes are kept as Argon2 hashes, one use each.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from .security import hash_password, verify_password

STEP = 30
DIGITS = 6
DRIFT = 1           # steps before and after "now" that are accepted
BACKUP_CODES = 8
ISSUER = "Cat-Egory"


class MfaError(ValueError):
    pass


def new_secret() -> str:
    """160 random bits, base32 without padding (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _raw(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_raw(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP)


def matching_step(secret: str, code: str, *, last_step: int | None, now: float | None = None) -> int | None:
    """The step whose code this is (within the drift), unless that step was already used."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    here = current_step(now)
    for step in range(here - DRIFT, here + DRIFT + 1):
        if hmac.compare_digest(code_at(secret, step), code):
            return None if last_step is not None and step <= last_step else step
    return None


def otpauth_uri(secret: str, account: str) -> str:
    label = quote(f"{ISSUER}:{account}")
    return f"otpauth://totp/{label}?" + urlencode({"secret": secret, "issuer": ISSUER, "digits": DIGITS,
                                                   "period": STEP, "algorithm": "SHA1"})


def _keystream(app_secret: str, nonce: bytes, length: int) -> bytes:
    out, counter = b"", 0
    while len(out) < length:
        out += hmac.new(app_secret.encode(), b"cat-egory/totp/" + nonce + struct.pack(">I", counter),
                        hashlib.sha256).digest()
        counter += 1
    return out[:length]


def seal(secret: str, app_secret: str) -> str:
    if not app_secret:
        raise MfaError("CATEGORY_FINGERPRINT_SECRET is not set; it encrypts the TOTP secrets")
    raw, nonce = secret.encode(), secrets.token_bytes(16)
    body = bytes(a ^ b for a, b in zip(raw, _keystream(app_secret, nonce, len(raw)), strict=True))
    tag = hmac.new(app_secret.encode(), b"cat-egory/totp-tag/" + nonce + body, hashlib.sha256).digest()[:16]
    return "v1:" + base64.urlsafe_b64encode(nonce + tag + body).decode()


def unseal(sealed: str, app_secret: str) -> str:
    if not sealed.startswith("v1:") or not app_secret:
        raise MfaError("unreadable TOTP secret")
    blob = base64.urlsafe_b64decode(sealed[3:])
    nonce, tag, body = blob[:16], blob[16:32], blob[32:]
    want = hmac.new(app_secret.encode(), b"cat-egory/totp-tag/" + nonce + body, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, want):
        raise MfaError("TOTP secret does not match the app secret (was CATEGORY_FINGERPRINT_SECRET changed?)")
    return bytes(a ^ b for a, b in zip(body, _keystream(app_secret, nonce, len(body)), strict=True)).decode()


def new_backup_codes() -> tuple[list[str], list[str]]:
    """Codes to show once (``abcd-ef01-2345``, 48 random bits) and their hashes to store."""
    codes = ["-".join(secrets.token_hex(2) for _ in range(3)) for _ in range(BACKUP_CODES)]
    return codes, [hash_password(c) for c in codes]


def use_backup_code(code: str, hashes: list[str]) -> list[str] | None:
    """The remaining hashes if ``code`` is one of them (it is used up), else None."""
    code = code.strip().lower()
    if len(code) != 14 or code[4] != "-" or code[9] != "-":
        return None
    for i, h in enumerate(hashes):
        if verify_password(h, code):
            return hashes[:i] + hashes[i + 1:]
    return None
