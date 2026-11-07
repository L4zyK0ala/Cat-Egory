"""Passwords, API keys, roles/scopes and client fingerprints.

API key format: ``cat_<prefix>_<secret>``
* ``prefix`` (10 chars, a-z0-9) is stored in clear and used for lookup.
* the full key is stored only as SHA-256. Keys carry ~256 bits of randomness, so a fast hash is enough
  (no dictionary attack is possible); passwords, which are low-entropy, use Argon2id instead.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import string
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from database.models import Role

KEY_PREFIX = "cat"
_PREFIX_ALPHABET = string.ascii_lowercase + string.digits
_PREFIX_LEN = 10

SCOPES = {
    "domains:write": "add and import domains",
    "scans:read": "view the scan queue",
    "scans:write": "create and cancel scans",
    "community:write": "submit reports and suggestions (Phase 6)",
    "reviews:write": "decide human-review tasks (Phase 4)",
}
ROLE_SCOPES: dict[Role, frozenset[str]] = {
    Role.ADMIN: frozenset(SCOPES),
    Role.MODERATOR: frozenset({"domains:write", "scans:read", "scans:write", "community:write", "reviews:write"}),
    Role.CONTRIBUTOR: frozenset({"community:write"}),
}

_hasher = PasswordHasher()  # Argon2id with library defaults (RFC 9106 low-memory profile)


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@dataclass(frozen=True, slots=True)
class NewApiKey:
    key: str  # show once, never store
    prefix: str
    key_hash: str


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> NewApiKey:
    prefix = "".join(secrets.choice(_PREFIX_ALPHABET) for _ in range(_PREFIX_LEN))
    key = f"{KEY_PREFIX}_{prefix}_{secrets.token_urlsafe(32)}"
    return NewApiKey(key=key, prefix=prefix, key_hash=_hash_key(key))


def parse_api_key(key: str) -> str | None:
    """Return the lookup prefix, or None if the string is not shaped like one of our keys."""
    parts = key.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX or len(parts[1]) != _PREFIX_LEN or len(parts[2]) < 32:
        return None
    if any(c not in _PREFIX_ALPHABET for c in parts[1]):
        return None
    return parts[1]


def key_matches(key: str, key_hash: str) -> bool:
    return hmac.compare_digest(_hash_key(key), key_hash)


def validate_scopes(role: Role, scopes: list[str]) -> list[str]:
    unknown = [s for s in scopes if s not in SCOPES]
    if unknown:
        raise ValueError(f"unknown scopes: {', '.join(unknown)}")
    beyond = [s for s in scopes if s not in ROLE_SCOPES[role]]
    if beyond:
        raise ValueError(f"role {role.value} cannot hold scopes: {', '.join(beyond)}")
    return sorted(set(scopes))


def fingerprint(secret: str | None, value: str | None) -> str | None:
    """Keyed hash of an IP address (or similar). Raw IPs are never stored."""
    if not secret or not value:
        return None
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()
