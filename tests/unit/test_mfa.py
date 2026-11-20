"""TOTP (RFC 6238) and the encrypted secret, standard library only."""
import base64

import pytest

from backend.app import mfa

# RFC 6238 appendix B: the SHA-1 secret is the ASCII "12345678901234567890"; at T=59 the 8-digit code is 94287082.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


def test_rfc_6238_vector():
    assert mfa.code_at(RFC_SECRET, 59 // 30) == "287082"          # the last 6 digits of 94287082
    assert mfa.code_at(RFC_SECRET, 1111111109 // 30) == "081804"  # 07081804


def test_a_code_is_accepted_once_and_within_one_step_of_drift():
    now = 1_800_000_000.0
    step = mfa.current_step(now)
    code = mfa.code_at(RFC_SECRET, step)
    assert mfa.matching_step(RFC_SECRET, code, last_step=None, now=now) == step
    assert mfa.matching_step(RFC_SECRET, f" {code[:3]} {code[3:]} ", last_step=None, now=now) == step
    assert mfa.matching_step(RFC_SECRET, code, last_step=step, now=now) is None          # replay
    assert mfa.matching_step(RFC_SECRET, code, last_step=None, now=now + 30) == step     # previous step: drift
    assert mfa.matching_step(RFC_SECRET, code, last_step=None, now=now + 90) is None     # too old
    assert mfa.matching_step(RFC_SECRET, "12345", last_step=None, now=now) is None
    assert mfa.matching_step(RFC_SECRET, "abcdef", last_step=None, now=now) is None


def test_the_secret_is_stored_encrypted_and_tamper_evident():
    secret = mfa.new_secret()
    assert len(secret) == 32 and len(mfa._raw(secret)) == 20
    sealed = mfa.seal(secret, "app-secret")
    assert secret not in sealed and sealed.startswith("v1:")
    assert mfa.seal(secret, "app-secret") != sealed                 # a fresh nonce each time
    assert mfa.unseal(sealed, "app-secret") == secret
    with pytest.raises(mfa.MfaError, match="FINGERPRINT_SECRET"):
        mfa.unseal(sealed, "another-secret")
    raw = bytearray(base64.urlsafe_b64decode(sealed[3:]))
    raw[-1] ^= 1
    with pytest.raises(mfa.MfaError):
        mfa.unseal("v1:" + base64.urlsafe_b64encode(bytes(raw)).decode(), "app-secret")
    with pytest.raises(mfa.MfaError):
        mfa.seal(secret, "")


def test_backup_codes_work_once():
    codes, hashes = mfa.new_backup_codes()
    assert len(codes) == 8 and all(len(c) == 14 and c.count("-") == 2 for c in codes)
    assert all(c not in h for c, h in zip(codes, hashes, strict=True))
    left = mfa.use_backup_code(codes[3].upper(), hashes)
    assert left is not None and len(left) == 7
    assert mfa.use_backup_code(codes[3], left) is None
    assert mfa.use_backup_code("123456", hashes) is None


def test_otpauth_uri():
    uri = mfa.otpauth_uri("ABC", "me@example.org")
    assert uri.startswith("otpauth://totp/Cat-Egory%3Ame%40example.org?") and "secret=ABC" in uri and "issuer=Cat-Egory" in uri
