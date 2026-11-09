"""Admin UI sign-in with email and password: a short-lived key instead of pasting a cat_ key."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.cli import create_user
from database.models import ApiKey, Role

PASSWORD = "correct horse battery staple"
SIGN_IN = "/api/v1/admin/session"


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
