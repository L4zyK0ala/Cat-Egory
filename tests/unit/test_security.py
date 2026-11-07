import fakeredis
import pytest

from backend.app.deps import RateLimiter
from backend.app.security import (
    ROLE_SCOPES,
    fingerprint,
    generate_api_key,
    hash_password,
    key_matches,
    parse_api_key,
    validate_scopes,
    verify_password,
)
from database.models import Role


def test_api_key_roundtrip():
    new = generate_api_key()
    assert new.key.startswith(f"cat_{new.prefix}_")
    assert parse_api_key(new.key) == new.prefix
    assert key_matches(new.key, new.key_hash)
    assert not key_matches(new.key + "x", new.key_hash)
    assert new.key not in new.key_hash
    assert generate_api_key().key != new.key


@pytest.mark.parametrize("bad", ["", "Bearer x", "cat_short_x", "abc_abcdefghij_" + "x" * 40,
                                 "cat_ABCDEFGHIJ_" + "x" * 40, "cat_abcdefghij_tooshort"])
def test_parse_rejects_malformed(bad):
    assert parse_api_key(bad) is None


def test_passwords():
    h = hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse battery")
    assert not verify_password(h, "wrong horse battery")
    assert not verify_password("not-a-hash", "x")
    with pytest.raises(ValueError):
        hash_password("short")


def test_scopes_limited_by_role():
    assert validate_scopes(Role.ADMIN, ["scans:read", "scans:read"]) == ["scans:read"]
    with pytest.raises(ValueError):
        validate_scopes(Role.CONTRIBUTOR, ["domains:write"])
    with pytest.raises(ValueError):
        validate_scopes(Role.ADMIN, ["root"])
    assert ROLE_SCOPES[Role.CONTRIBUTOR] < ROLE_SCOPES[Role.ADMIN]


def test_fingerprint_is_keyed_and_optional():
    a = fingerprint("secret-1", "203.0.113.5")
    assert a and "203.0.113.5" not in a
    assert a != fingerprint("secret-2", "203.0.113.5")
    assert fingerprint(None, "203.0.113.5") is None


def test_rate_limiter_window():
    limiter = RateLimiter(fakeredis.FakeRedis())
    results = [limiter.hit("read", "ip:x", 3) for _ in range(5)]
    assert [ok for ok, _, _ in results] == [True, True, True, False, False]
    assert results[0][1] == 2 and results[2][1] == 0
    assert 0 < results[0][2] <= 60
    assert limiter.hit("read", "ip:other", 3)[0]  # identities are independent
