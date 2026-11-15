import httpx

from common.config import Settings
from crawler.browser import PageCapture, classify_error
from crawler.result import Artifact, CrawlResult
from crawler.robots import RobotsDecision, fetch_robots
from crawler.storage import MemoryStore
from crawler.tasks import crawl

S = Settings(_env_file=None, egress_proxy_token="tok")


def transport(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.url.scheme}://{request.url.host}{request.url.path}"
        if key not in routes:
            raise httpx.ConnectError("unreachable")
        status, body, headers = routes[key]
        return httpx.Response(status, content=body, headers=headers)
    return httpx.MockTransport(handler)


def robots(routes, token=None):
    return fetch_robots("example.com", agent="CategoryBot/0.1", proxy_url=None, proxy_token=token,
                        transport=transport(routes))


def test_robots_rules():
    ok = robots({"https://example.com/robots.txt": (200, b"User-agent: *\nAllow: /\n", {})})
    assert ok.allowed and ok.reason == "parsed"
    no = robots({"https://example.com/robots.txt": (200, b"User-agent: CategoryBot\nDisallow: /\n", {})})
    assert not no.allowed
    other = robots({"https://example.com/robots.txt": (200, b"User-agent: Googlebot\nDisallow: /\n", {})})
    assert other.allowed


def test_robots_status_semantics():
    assert robots({"https://example.com/robots.txt": (404, b"", {})}).allowed  # 4xx: no restrictions
    assert not robots({"https://example.com/robots.txt": (503, b"", {})}).allowed  # 5xx: disallow
    fallback = robots({"http://example.com/robots.txt": (200, b"User-agent: *\nAllow: /\n", {})})
    assert fallback.allowed and fallback.url.startswith("http://")  # https unreachable -> http
    assert robots({}).reason == "unavailable"


def test_robots_proxy_signals():
    blocked = {"https://example.com/robots.txt": (403, b"", {"X-Category-Blocked": "resolves_to_blocked_ip",
                                                              "X-Category-Proxy-Token": "tok"})}
    assert robots(blocked, token="tok").reason == "proxy:resolves_to_blocked_ip"
    forged = {"https://example.com/robots.txt": (404, b"", {"X-Category-Blocked": "resolves_to_blocked_ip",
                                                             "X-Category-Proxy-Token": "nope"})}
    assert robots(forged, token="tok").reason == "not_found"


def fake_capture(**kw):
    page = PageCapture(requested_url="https://example.com/", final_url="https://example.com/", http_status=200,
                       title="Shop", text="buy shoes now " * 10, html=b"<html>shop</html>", screenshot=b"\x89PNG..",
                       response_headers={"content-type": "text/html", "content-length": "17"}, sandboxed=True)
    for k, v in kw.items():
        setattr(page, k, v)
    return lambda domain, settings: page


def allow(*a, **k):
    return RobotsDecision(True, "parsed")


def test_crawl_success_stores_artifacts():
    store = MemoryStore()
    r = crawl({"job_id": "j1", "domain": "example.com", "resolved_ips": ["93.184.215.14"]}, S, store,
              capture_fn=fake_capture(), robots_fn=allow)
    assert r.ok and r.http_status == 200 and r.content_length == 17 and r.word_count == 30
    assert r.screenshot.key.endswith("/j1/screenshot.png") and store.get(r.screenshot.key).startswith(b"\x89PNG")
    import gzip
    assert gzip.decompress(store.get(r.html.key)) == b"<html>shop</html>"
    assert r.resolved_ips == ["93.184.215.14"] and r.sandboxed is True


def test_crawl_robots_paths():
    deny = lambda *a, **k: RobotsDecision(False, "parsed")  # noqa: E731
    down = lambda *a, **k: RobotsDecision(False, "unavailable")  # noqa: E731
    policy = lambda *a, **k: RobotsDecision(False, "proxy:resolves_to_blocked_ip")  # noqa: E731
    assert crawl({"job_id": "j", "domain": "e.com"}, S, MemoryStore(), robots_fn=deny).blocked_reason == "robots_disallowed"
    r = crawl({"job_id": "j", "domain": "e.com"}, S, MemoryStore(), robots_fn=down)
    assert r.error_code == "robots_unavailable" and r.retryable
    assert crawl({"job_id": "j", "domain": "e.com"}, S, MemoryStore(),
                 robots_fn=policy).blocked_reason == "resolves_to_blocked_ip"


def test_crawl_never_raises():
    def boom(domain, settings):
        raise RuntimeError("browser exploded")
    r = crawl({"job_id": "j", "domain": "e.com"}, S, MemoryStore(), capture_fn=boom, robots_fn=allow)
    assert r.error_code == "internal_error" and r.retryable


def test_result_roundtrip():
    r = CrawlResult(job_id="j", domain="e.com", requested_url="https://e.com/",
                    screenshot=Artifact(key="k", sha256="0" * 64, size=1, mime_type="image/png"))
    back = CrawlResult.from_dict(r.to_dict() | {"unknown_future_field": 1})
    assert back == r


def test_classify_error():
    assert classify_error("net::ERR_NAME_NOT_RESOLVED at https://x/") == "dns_failure"
    assert classify_error("net::ERR_CERT_AUTHORITY_INVALID") == "tls_error"
    assert classify_error("net::ERR_TUNNEL_CONNECTION_FAILED") == "connection_failed"
    assert classify_error("Download is starting") == "download_refused"
    assert classify_error("Timeout 20000ms exceeded") == "timeout"


def test_storage_failure_is_reported_and_retryable():
    from crawler.storage import StorageError

    class Broken(MemoryStore):
        def put(self, *a, **k):
            raise StorageError("put_object", "InternalError")

    r = crawl({"job_id": "j", "domain": "e.com"}, S, Broken(), capture_fn=fake_capture(), robots_fn=allow)
    assert r.error_code == "storage_error" and "InternalError" in r.error_detail and r.retryable


def test_s3_store_against_moto():
    import boto3
    from moto import mock_aws

    from crawler.storage import S3Store, save

    with mock_aws():
        settings = Settings(_env_file=None, s3_access_key="k", s3_secret_key="s", s3_bucket="artifacts")
        store = S3Store(settings, client=boto3.client("s3", region_name="us-east-1"))
        art = save(store, "scans/x/text.txt.gz", b"hello", "text/plain", compress=True)
        import gzip
        assert gzip.decompress(store.get(art.key)) == b"hello" and art.size == 5



def test_crawl_reads_the_host_precheck_chose():
    asked = []
    def capture(host, settings):
        asked.append(("page", host))
        return fake_capture()(host, settings)
    def robots(host, **k):
        asked.append(("robots", host))
        return RobotsDecision(True, "parsed")
    r = crawl({"job_id": "j", "domain": "bare.com", "host": "www.bare.com"}, S, MemoryStore(), capture_fn=capture,
              robots_fn=robots)
    assert r.domain == "bare.com" and asked == [("robots", "www.bare.com"), ("page", "www.bare.com")]
