"""Admin UI sign-in with email and password: a short-lived key instead of pasting a cat_ key."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.cli import create_user
from database.models import ApiKey, Role

PASSWORD = "correct horse battery staple"
SIGN_IN = "/api/v1/admin/session"


@pytest.fixture(autouse=True)
def mfa_not_required(monkeypatch):
    """The local .env may require MFA (the server folder); these tests set it themselves where they need it."""
    from common.config import get_settings

    monkeypatch.setenv("CATEGORY_ADMIN_MFA_REQUIRED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def user(engine, email="mod@example.org", role=Role.MODERATOR, password=PASSWORD):
    with Session(engine) as s, s.begin():
        create_user(s, email=email, name="t", role=role, password=password)


def test_sign_in_gives_a_working_short_lived_key_and_sign_out_revokes_it(engine, client, clean):
    user(engine)
    r = client.post(SIGN_IN, json={"email": " Mod@Example.org ", "password": PASSWORD})
    assert r.status_code == 201
    body = r.json()
    assert body["role"] == "moderator"
    left = datetime.fromisoformat(body["expires_at"]) - datetime.now(UTC)
    assert timedelta(hours=11) < left <= timedelta(hours=12)
    headers = {"Authorization": f"Bearer {body['key']}"}
    assert client.get("/api/v1/admin/me", headers=headers).json()["role"] == "moderator"

    assert client.post(f"{SIGN_IN}/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/admin/me", headers=headers).status_code == 401

    remembered = client.post(SIGN_IN, json={"email": "mod@example.org", "password": PASSWORD, "remember": True}).json()
    assert datetime.fromisoformat(remembered["expires_at"]) - datetime.now(UTC) > timedelta(days=6)


def test_wrong_passwords_are_limited_and_look_the_same(engine, client, clean):
    user(engine)
    user(engine, email="nopass@example.org", password=None)
    user(engine, email="contrib@example.org", role=Role.CONTRIBUTOR)
    for email, password in (("nobody@example.org", PASSWORD), ("nopass@example.org", PASSWORD),
                            ("contrib@example.org", PASSWORD)):  # contributors have no admin UI
        r = client.post(SIGN_IN, json={"email": email, "password": password})
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_login"
    for _ in range(5):
        assert client.post(SIGN_IN, json={"email": "mod@example.org", "password": "wrong"}).status_code == 401
    r = client.post(SIGN_IN, json={"email": "mod@example.org", "password": PASSWORD})
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_attempts"  # even the right one, for now


def test_sign_out_keeps_ordinary_api_keys(client, admin):
    assert client.post(f"{SIGN_IN}/logout", headers=admin).status_code == 204
    assert client.get("/api/v1/admin/me", headers=admin).status_code == 200


def test_set_password_cli(engine, clean, monkeypatch, capsys, client):
    from backend import cli

    user(engine, email="admin@example.org", role=Role.ADMIN, password=None)
    answers = iter(["a new long password", "a new long password"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(answers))
    assert cli.main(["set-password", "--email", "admin@example.org"]) == 0
    assert client.post(SIGN_IN, json={"email": "admin@example.org", "password": "a new long password"}).status_code == 201
    with Session(engine) as s:
        assert s.scalar(select(ApiKey.name).order_by(ApiKey.id.desc())) == "web sign-in"


def test_unknown_accounts_cost_as_much_as_wrong_passwords(client, monkeypatch):
    """The response time must not reveal which e-mail addresses have accounts."""
    from backend.app.api import session as session_api

    checked = []
    real = session_api.verify_password
    monkeypatch.setattr(session_api, "verify_password", lambda h, p: checked.append(h) or real(h, p))
    r = client.post("/api/v1/admin/session", json={"email": "nobody@example.com", "password": "whatever-long-1"})
    assert r.status_code == 401 and checked == [session_api._NO_ACCOUNT_HASH]


def enrol(engine, email="mod@example.org"):
    """mfa-enroll as the CLI does it: returns the secret and the backup codes it showed."""
    from backend.app import mfa
    from backend.cli import mfa_enroll

    shown: list[str] = []
    with Session(engine) as s, s.begin():
        def ask(_):
            secret = shown[1].split(":", 1)[1].strip().replace(" ", "")
            return mfa.code_at(secret, mfa.current_step())
        assert mfa_enroll(s, email, "test-app-secret", ask=ask, say=lambda line: shown.extend(line.split("\n")))
    secret = shown[1].split(":", 1)[1].strip().replace(" ", "")
    codes = [line.strip() for line in shown if line.startswith("  ") and line.count("-") == 2 and len(line.strip()) == 14]
    return secret, codes


def test_second_factor(engine, client, clean, monkeypatch):
    from backend.app import mfa
    from common.config import get_settings
    from database.models import AuditLog, User

    monkeypatch.setenv("CATEGORY_FINGERPRINT_SECRET", "test-app-secret")
    get_settings.cache_clear()
    user(engine)
    secret, codes = enrol(engine)
    assert len(codes) == 8
    with Session(engine) as s:
        stored = s.scalar(select(User).where(User.email == "mod@example.org"))
        assert stored.totp_secret.startswith("v1:") and secret not in stored.totp_secret
        assert all(c not in str(stored.totp_backup_codes) for c in codes)

    body = {"email": "mod@example.org", "password": PASSWORD}
    r = client.post(SIGN_IN, json=body)
    assert r.status_code == 401 and r.json()["error"]["code"] == "otp_required"
    assert client.post(SIGN_IN, json=body | {"otp": "000000"}).json()["error"]["code"] == "invalid_otp"
    used = mfa.code_at(secret, mfa.current_step())  # enrolment used this step already
    assert client.post(SIGN_IN, json=body | {"otp": used}).json()["error"]["code"] == "invalid_otp"
    assert client.post(SIGN_IN, json=body | {"otp": codes[0]}).status_code == 201       # a backup code
    assert client.post(SIGN_IN, json=body | {"otp": codes[0]}).json()["error"]["code"] == "invalid_otp"
    wrong_password = client.post(SIGN_IN, json=body | {"password": "x" * 12, "otp": codes[1]})
    assert wrong_password.json()["error"]["code"] == "invalid_login"                     # code never checked
    with Session(engine) as s:
        assert len(s.scalar(select(User.totp_backup_codes).where(User.email == "mod@example.org"))) == 7
        assert s.scalar(select(AuditLog.id).where(AuditLog.action == "session.backup_code_used")) is not None
    get_settings.cache_clear()


def test_wrong_codes_count_towards_the_lockout(engine, client, clean, monkeypatch, fake_redis):
    from common.config import get_settings

    monkeypatch.setenv("CATEGORY_FINGERPRINT_SECRET", "test-app-secret")
    get_settings.cache_clear()
    user(engine)
    enrol(engine)
    body = {"email": "mod@example.org", "password": PASSWORD, "otp": "000000"}
    for _ in range(5):
        assert client.post(SIGN_IN, json=body).json()["error"]["code"] == "invalid_otp"
    assert client.post(SIGN_IN, json=body).json()["error"]["code"] == "too_many_attempts"
    get_settings.cache_clear()


def test_required_mfa_refuses_accounts_without_it(engine, client, clean, monkeypatch):
    from common.config import get_settings

    monkeypatch.setenv("CATEGORY_ADMIN_MFA_REQUIRED", "true")
    get_settings.cache_clear()
    user(engine)
    r = client.post(SIGN_IN, json={"email": "mod@example.org", "password": PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "mfa_enrollment_required"
    get_settings.cache_clear()


def test_enrolment_saves_nothing_on_a_wrong_code(engine, clean):
    from backend.cli import mfa_disable, mfa_enroll
    from database.models import User

    user(engine)
    with Session(engine) as s, s.begin():
        assert not mfa_enroll(s, "mod@example.org", "k", ask=lambda _: "000000", say=lambda _: None)
    with Session(engine) as s:
        assert s.scalar(select(User.totp_secret).where(User.email == "mod@example.org")) is None
    enrol(engine)
    with Session(engine) as s, s.begin():
        mfa_disable(s, "mod@example.org")
    with Session(engine) as s:
        u = s.scalar(select(User).where(User.email == "mod@example.org"))
        assert (u.totp_secret, u.totp_backup_codes) == (None, [])


def test_enrolment_refuses_redirected_output(monkeypatch, capsys):
    """The secret must reach a terminal only: with output redirected (a file, a pipe, a log) nothing is shown."""
    import io

    from backend import cli

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    assert cli.main(["mfa-enroll", "--email", "mod@example.org"]) == 1
    assert "run it in a terminal" in capsys.readouterr().err
