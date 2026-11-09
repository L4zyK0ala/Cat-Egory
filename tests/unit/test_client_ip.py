"""Who the rate limiter thinks is asking. A spoofable answer here would disable the limits."""
from types import SimpleNamespace

from backend.app.deps import client_ip
from common.config import Settings

TUNNEL = "10.250.61.250"
S = Settings(_env_file=None, trusted_proxies=[f"{TUNNEL}/32"])


def request(peer, header=None):
    return SimpleNamespace(client=SimpleNamespace(host=peer) if peer else None,
                           headers={"cf-connecting-ip": header} if header else {})


def test_the_tunnel_states_the_visitor():
    assert client_ip(request(TUNNEL, "203.0.113.7"), S) == "203.0.113.7"


def test_nobody_else_can():
    assert client_ip(request("198.51.100.9", "203.0.113.7"), S) == "198.51.100.9"


def test_nothing_is_trusted_by_default():
    assert client_ip(request(TUNNEL, "203.0.113.7"), Settings(_env_file=None)) == TUNNEL


def test_a_bad_or_missing_header_falls_back_to_the_connection():
    assert client_ip(request(TUNNEL, "not-an-ip"), S) == TUNNEL
    assert client_ip(request(TUNNEL), S) == TUNNEL


def test_ipv6_visitors_share_their_64():
    a = client_ip(request(TUNNEL, "2001:db8:1:2:aaaa::1"), S)
    assert a == "2001:db8:1:2::" == client_ip(request(TUNNEL, "2001:db8:1:2:bbbb::9"), S)


def test_unusual_peers_do_not_break_the_request():
    assert client_ip(request("testclient"), S) == "testclient"
    assert client_ip(request(None), S) is None
