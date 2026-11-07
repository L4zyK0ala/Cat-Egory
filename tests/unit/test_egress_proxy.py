"""Egress proxy tests against a local HTTP server. The policy is relaxed ONLY to allow 127.0.0.1 so the
success path can be tested offline; every block test uses addresses the default policy also blocks."""
import asyncio
import http.server
import socket
import threading

import httpx
import pytest

from crawler.egress_proxy import EgressProxy, ProxyConfig


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = f"hello {self.path} host={self.headers.get('Host')}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def origin():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


DNS = {
    "public.example.com": ["127.0.0.1"],  # stands in for a public site
    "internal.example.com": ["10.0.0.5"],
    "metadata.example.com": ["169.254.169.254"],
    "rebind.example.com": ["127.0.0.1", "10.0.0.5"],
    "mapped.example.com": ["::ffff:10.0.0.5"],
}


@pytest.fixture
def proxy(origin):
    def resolver(host, port):
        if host not in DNS:
            raise socket.gaierror("nxdomain")
        return DNS[host]

    config = ProxyConfig(resolver=resolver, allow=lambda ip: ip == "127.0.0.1", allowed_ports={origin, 443},
                         max_bytes=10_000)
    instance = EgressProxy(config)
    loop = asyncio.new_event_loop()
    port = loop.run_until_complete(instance.start("127.0.0.1", 0))
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", instance, origin
    asyncio.run_coroutine_threadsafe(instance.close(), loop).result(5)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(2)


def get(proxy_url, url, **kw):
    with httpx.Client(proxy=proxy_url, timeout=5) as client:
        return client.get(url, **kw)


def test_allows_public_and_pins(proxy):
    proxy_url, instance, port = proxy
    r = get(proxy_url, f"http://public.example.com:{port}/x?y=1")
    assert r.status_code == 200 and r.text.startswith("hello /x?y=1")
    assert instance.stats.allowed == 1


def test_post_body_forwarded(proxy):
    proxy_url, _, port = proxy
    with httpx.Client(proxy=proxy_url, timeout=5) as client:
        assert client.post(f"http://public.example.com:{port}/", content=b"payload").text == "payload"


@pytest.mark.parametrize(("host", "code"), [
    ("internal.example.com", "resolves_to_blocked_ip"), ("metadata.example.com", "resolves_to_blocked_ip"),
    ("rebind.example.com", "resolves_to_blocked_ip"), ("mapped.example.com", "resolves_to_blocked_ip"),
    ("nx.example.com", "dns_failure"), ("127.0.0.1", "ip_address"), ("localhost", "no_suffix"),
])
def test_blocks_internal_targets(proxy, host, code):
    proxy_url, instance, port = proxy
    r = get(proxy_url, f"http://{host}:{port}/")
    assert r.status_code == 403
    assert r.headers["X-Category-Blocked"] == code
    assert instance.stats.blocked[code] >= 1


def test_blocks_disallowed_port(proxy):
    proxy_url, _, _ = proxy
    assert get(proxy_url, "http://public.example.com:22/").headers["X-Category-Blocked"] == "port"


def raw(proxy_url, payload: bytes) -> bytes:
    host, port = proxy_url.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.sendall(payload)
        chunks = []
        while data := s.recv(65536):
            chunks.append(data)
        return b"".join(chunks)


def test_connect_tunnel_allowed(proxy):
    proxy_url, _, port = proxy
    host, pport = proxy_url.removeprefix("http://").split(":")
    with socket.create_connection((host, int(pport)), timeout=5) as s:
        s.sendall(f"CONNECT public.example.com:{port} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert s.recv(1024).startswith(b"HTTP/1.1 200")
        s.sendall(b"GET /tunnel HTTP/1.1\r\nHost: public.example.com\r\nConnection: close\r\n\r\n")
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    assert b"hello /tunnel" in data


@pytest.mark.parametrize("target", ["internal.example.com:443", "127.0.0.1:443", "public.example.com:25",
                                    "[::1]:443", "garbage"])
def test_connect_blocked(proxy, target):
    proxy_url, _, _ = proxy
    assert raw(proxy_url, f"CONNECT {target} HTTP/1.1\r\n\r\n".encode()).startswith(b"HTTP/1.1 403")


def test_https_must_use_connect_and_garbage_rejected(proxy):
    proxy_url, _, port = proxy
    assert b"X-Category-Blocked: scheme" in raw(proxy_url, b"GET https://public.example.com/ HTTP/1.1\r\n\r\n")
    assert raw(proxy_url, b"NONSENSE\r\n\r\n").startswith(b"HTTP/1.1 400")
    assert raw(proxy_url, b"GET http://public.example.com/ HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n") \
        .startswith(b"HTTP/1.1 4")


def test_second_request_on_same_connection_is_not_forwarded(proxy):
    """A keep-alive client must not be able to send a second (different-host) request through an approved
    connection: the proxy closes the connection after one response."""
    proxy_url, _, port = proxy
    data = raw(proxy_url, (f"GET http://public.example.com:{port}/one HTTP/1.1\r\nHost: a\r\n\r\n"
                           f"GET http://internal.example.com:{port}/two HTTP/1.1\r\nHost: b\r\n\r\n").encode())
    assert b"hello /one" in data and b"/two" not in data


def test_byte_limit(proxy, monkeypatch):
    proxy_url, instance, port = proxy
    Handler.big = True

    def big(self):
        body = b"x" * 50_000
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    monkeypatch.setattr(Handler, "do_GET", big)
    with pytest.raises(httpx.HTTPError):
        get(proxy_url, f"http://public.example.com:{port}/big")
    assert instance.stats.blocked.get("byte_limit")
