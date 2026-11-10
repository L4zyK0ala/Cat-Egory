"""Self-service developer keys: no account, read-only, own limits and a daily quota."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.config import get_settings
from database.models import ApiKey, User


def take(client, label="my app", accept=True):
    return client.post("/api/v1/developer/keys", json={"label": label, "accept_terms": accept})


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def test_a_key_is_shown_once_stored_as_a_hash_and_works(client, engine):
    r = take(client)
    assert r.status_code == 201
    body = r.json()
    assert body["key"].startswith("cat_") and body["label"] == "my app" and body["per_day"] > 0
    with Session(engine) as s:
        row = s.scalar(select(ApiKey).where(ApiKey.prefix == body["prefix"]))
        assert body["key"] not in (row.key_hash, row.name) and row.scopes == [] and row.expires_at
        assert s.get(User, row.user_id).password_hash is None
    stats = client.get("/api/v1/stats", headers=auth(body["key"]))
    assert stats.status_code == 200 and stats.headers["X-Quota-Limit"] == str(body["per_day"])
    info = client.get("/api/v1/developer/key", headers=auth(body["key"])).json()
    assert info["label"] == "my app" and info["used_today"] >= 2


def test_terms_must_be_accepted(client):
    assert take(client, accept=False).status_code == 422


def test_a_key_opens_nothing_beyond_the_public_api(client):
    key = take(client).json()["key"]
    assert client.get("/api/v1/admin/scans", headers=auth(key)).status_code in (403, 404)


def test_clients_can_take_only_a_few_keys_a_day(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "developer_keys_per_client_per_day", 2)
    assert [take(client).status_code for _ in range(3)] == [201, 201, 429]


def test_the_daily_quota_stops_a_key(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "developer_key_daily_quota", 2)
    key = take(client).json()["key"]
    codes = [client.get("/api/v1/categories", headers=auth(key)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = client.get("/api/v1/categories", headers=auth(key))
    assert r.json()["error"]["code"] == "daily_quota_exceeded" and int(r.headers["Retry-After"]) > 0


def test_a_holder_revokes_their_own_key(client):
    key = take(client).json()["key"]
    assert client.post("/api/v1/developer/key/revoke", headers=auth(key)).json() == {"revoked": True}
    assert client.get("/api/v1/stats", headers=auth(key)).status_code == 401


def test_admin_keys_are_not_developer_keys(client, admin):
    assert client.get("/api/v1/developer/key", headers=admin).status_code == 403
    assert client.get("/api/v1/developer/key").status_code == 401


def test_labels_are_cleaned(client):
    label = take(client, label="  ok\u0000\u200bname\n ").json()["label"]
    assert label == "okname"  # control and invisible characters are dropped
