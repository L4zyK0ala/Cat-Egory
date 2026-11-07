import pytest

from crawler.ssrf import BlockedError, check_url, is_public, resolve_public, why_blocked


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.255.255.254", "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.1", "169.254.169.254",
    "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "192.0.2.1", "198.51.100.1", "203.0.113.1",
    "198.18.0.1", "240.0.0.1",
    "::1", "::", "fe80::1", "fe80::1%eth0", "fc00::1", "fd12:3456::1", "ff02::1", "2001:db8::1",
    "::ffff:127.0.0.1", "::ffff:169.254.169.254", "::127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::1",
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",
    "172.17.0.2",  # Docker default bridge
])
def test_blocked_addresses(ip):
    assert why_blocked(ip) is not None


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.215.14", "2606:4700:4700::1111", "2a00:1450:4001::200e"])
def test_public_addresses(ip):
    assert is_public(ip)


def test_garbage_is_blocked():
    assert why_blocked("not-an-ip") == "invalid_ip"


@pytest.mark.parametrize(("url", "code"), [
    ("file:///etc/passwd", "scheme"), ("gopher://example.com/", "scheme"), ("ftp://example.com/", "scheme"),
    ("http://user:pw@example.com/", "credentials"), ("http://example.com:22/", "port"),
    ("http://example.com:6379/", "port"), ("http://127.0.0.1/", "ip_address"), ("http://[::1]/", "ip_address"),
    ("http://2130706433/", "no_suffix"), ("http://0x7f.0.0.1/", "no_suffix"), ("http://localhost/", "no_suffix"),
    ("http://metadata.google.internal/", "special_use"), ("http://router.lan/", "special_use"),
    ("http://example.com:99999/", "invalid_url"),
])
def test_check_url_rejects(url, code):
    with pytest.raises(BlockedError) as exc:
        check_url(url)
    assert exc.value.code == code


def test_check_url_accepts():
    t = check_url("HTTPS://Bücher.de/path?q=1")
    assert (t.scheme, t.host, t.port) == ("https", "xn--bcher-kva.de", 443)


def fake(answers):
    return lambda host, port: answers[host]


def test_resolve_public_requires_all_answers_public():
    assert resolve_public("ok.com", 443, resolver=fake({"ok.com": ["8.8.8.8"]})) == ["8.8.8.8"]
    for answers in (["10.0.0.1"], ["8.8.8.8", "127.0.0.1"], ["::ffff:10.0.0.1"], []):
        with pytest.raises(BlockedError):
            resolve_public("x.com", 443, resolver=fake({"x.com": answers}))


def test_resolve_failure():
    import socket

    def boom(host, port):
        raise socket.gaierror("nxdomain")

    with pytest.raises(BlockedError) as exc:
        resolve_public("nx.com", 443, resolver=boom)
    assert exc.value.code == "dns_failure"
