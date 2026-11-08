from classification.taxonomy import default_taxonomy


def test_categories(client):
    r = client.get("/api/v1/categories")
    assert r.status_code == 200
    body = r.json()
    assert body["taxonomy_version"] == default_taxonomy().version
    assert body["license"] == "CC-BY-4.0"
    ids = {c["id"] for c in body["items"]}
    assert {"gambling", "phishing", "group-security-threats"} <= ids
    assert r.headers["Cache-Control"].startswith("public")
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_categories_filter_by_kind(client):
    items = client.get("/api/v1/categories", params={"kind": "risk"}).json()["items"]
    assert items and all(c["kind"] == "risk" for c in items)
    assert client.get("/api/v1/categories", params={"kind": "evil"}).status_code == 422


def test_single_category(client):
    assert client.get("/api/v1/categories/banking").json()["parent_id"] == "group-business-technology"
    assert client.get("/api/v1/categories/nope").status_code == 404


def test_openapi_and_health(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    spec = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/categories" in spec["paths"]
    assert client.get("/metrics").status_code == 200
