"""SSRF guard: decide whether an address or URL may be fetched.

Used in three places (defence in depth):
1. before a scan is dispatched (DNS answers of the target domain),
2. in the egress proxy at *connect time* for every connection the browser makes (defeats DNS rebinding,
   redirects to internal hosts and sub-resources pointing inward),
3. on the final result (redirect chain hosts) as a post-hoc check.

Only globally routable unicast addresses are allowed. IPv6 forms that embed an IPv4 address (IPv4-mapped,
IPv4-compatible, NAT64, 6to4, Teredo) are judged by the embedded IPv4 address as well.
"""
from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from common.domains import DomainError, normalize_domain

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Explicit lists on top of Python's is_global, so the policy does not silently change with Python versions.
_BLOCKED_V4 = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32",
)]
_BLOCKED_V6 = [ipaddress.ip_network(n) for n in (
    "::/128", "::1/128", "::ffff:0:0/96", "::/96", "64:ff9b::/96", "64:ff9b:1::/48", "100::/64", "2001::/23",
    "2001:db8::/32", "2002::/16", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8",
)]
ALLOWED_PORTS = frozenset({80, 443})
ALLOWED_SCHEMES = frozenset({"http", "https"})


class BlockedError(Exception):
    """The target is not allowed. ``code`` is stable and stored as ``scan_results.blocked_reason``."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _embedded_v4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    found = []
    if ip.ipv4_mapped:
        found.append(ip.ipv4_mapped)
    if ip.sixtofour:
        found.append(ip.sixtofour)
    if ip.teredo:
        found.extend(ip.teredo)  # (server, client)
    packed = ip.packed
    if packed[:12] == b"\x00" * 12:  # IPv4-compatible ::a.b.c.d (deprecated)
        found.append(ipaddress.IPv4Address(packed[12:]))
    if ip in ipaddress.ip_network("64:ff9b::/96"):  # NAT64
        found.append(ipaddress.IPv4Address(packed[12:]))
    return found


def why_blocked(ip: IPAddress | str) -> str | None:
    """Return a reason code if ``ip`` must not be contacted, else None."""
    if isinstance(ip, str):
        try:
            ip = ipaddress.ip_address(ip.split("%", 1)[0])  # drop IPv6 zone id
        except ValueError:
            return "invalid_ip"
    if isinstance(ip, ipaddress.IPv6Address):
        for v4 in _embedded_v4(ip):
            if why_blocked(v4):
                return "embedded_private_ipv4"
        if any(ip in net for net in _BLOCKED_V6):
            return "special_ipv6"
    elif any(ip in net for net in _BLOCKED_V4):
        if ip in ipaddress.ip_network("169.254.0.0/16"):
            return "link_local_or_metadata"
        return "private_or_reserved_ipv4"
    if not ip.is_global or ip.is_multicast:
        return "not_global"
    return None


def is_public(ip: IPAddress | str) -> bool:
    return why_blocked(ip) is None


Resolver = Callable[[str, int], list[str]]


def system_resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


@dataclass(frozen=True, slots=True)
class Target:
    scheme: str
    host: str  # normalised ASCII hostname
    port: int


def check_url(url: str, *, allowed_ports: Iterable[int] = ALLOWED_PORTS) -> Target:
    """Static checks on a URL (no DNS): scheme, credentials, port, public hostname (no IP literals)."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise BlockedError("invalid_url", str(exc)) from exc
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise BlockedError("scheme", scheme or "(none)")
    if parts.username or parts.password:
        raise BlockedError("credentials")
    port = port or (443 if scheme == "https" else 80)
    if port not in set(allowed_ports):
        raise BlockedError("port", str(port))
    try:
        host = normalize_domain(parts.hostname or "").ascii
    except DomainError as exc:
        raise BlockedError(exc.code, str(exc)) from exc
    return Target(scheme, host, port)


def resolve_public(host: str, port: int, *, resolver: Resolver = system_resolve,
                   allow: Callable[[str], bool] = is_public) -> list[str]:
    """Resolve ``host`` and return its addresses, refusing if ANY answer is non-public.

    Refusing on any bad answer (instead of filtering) prevents an attacker from mixing a public and an
    internal record and relying on the client's address selection.
    """
    try:
        addresses = resolver(host, port)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise BlockedError("dns_failure", type(exc).__name__) from exc
    if not addresses:
        raise BlockedError("dns_no_answer")
    bad = [a for a in addresses if not allow(a)]
    if bad:
        raise BlockedError("resolves_to_blocked_ip", why_blocked(bad[0]) or "policy")
    return addresses
