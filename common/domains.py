"""Domain and URL normalisation.

Every domain that enters the system (manual input, imports, community, third-party lists) goes through
:func:`normalize_domain`, so the same site is always stored under one ``normalized_domain``:

* Unicode is mapped with UTS #46 and stored as ASCII (punycode); the Unicode form is kept for display.
* Lower case, no trailing dot, no port, no credentials, no wildcard.
* The host must sit under a known public suffix (bundled Public Suffix List snapshot, no network access).
* IP literals and special-use names (``localhost``, ``.local``, ``.internal``, ``.arpa``, ``.onion`` ...)
  are rejected: they are never valid targets for a public categorisation database.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import idna
import tldextract

MAX_DOMAIN_LENGTH = 253
_LABEL_RE = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$")
_ALLOWED_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
# RFC 6761 / 6762 / 8375 / 9476 special-use names and widely used private TLDs.
_SPECIAL_USE_SUFFIXES = (
    "localhost", "local", "internal", "intranet", "private", "corp", "home", "lan", "test", "invalid",
    "example", "alt", "onion", "arpa", "home.arpa",
)

# Bundled PSL snapshot only (suffix_list_urls=()) -> deterministic, no outbound HTTP at runtime.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True)


class DomainError(ValueError):
    """Input is not a valid public domain. ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class NormalizedDomain:
    ascii: str  # stored as domains.normalized_domain
    display: str  # Unicode form for humans
    registrable: str  # eTLD+1 (respecting private suffixes such as github.io)
    public_suffix: str


@dataclass(frozen=True, slots=True)
class NormalizedUrl:
    url: str
    domain: NormalizedDomain
    sha256: str


def _strip_host(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise DomainError("empty", "domain is empty")
    if "://" in value:
        parts = urlsplit(value)
        if parts.username or parts.password:
            raise DomainError("credentials", "URLs with credentials are not accepted")
        value = parts.hostname or ""
    else:
        value = value.split("/", 1)[0]
        if "@" in value:
            raise DomainError("credentials", "URLs with credentials are not accepted")
        if value.startswith("["):
            raise DomainError("ip_address", "IP addresses are not domains")
        value = value.rsplit(":", 1)[0] if value.count(":") == 1 else value
    return value.strip().rstrip(".")


def normalize_domain(raw: str) -> NormalizedDomain:
    host = _strip_host(raw)
    if not host:
        raise DomainError("empty", "domain is empty")
    if "*" in host:
        raise DomainError("wildcard", "wildcards are not accepted")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise DomainError("ip_address", "IP addresses are not domains")

    try:
        ascii_host = idna.encode(host, uts46=True, std3_rules=False, transitional=False).decode("ascii")
    except idna.IDNAError as exc:
        if "too long" in str(exc).lower() and "label" not in str(exc).lower():
            raise DomainError("too_long", "domain is longer than 253 characters") from exc
        raise DomainError("idna", f"invalid internationalised domain: {exc}") from exc
    ascii_host = ascii_host.lower()

    if len(ascii_host) > MAX_DOMAIN_LENGTH:
        raise DomainError("too_long", "domain is longer than 253 characters")
    labels = ascii_host.split(".")
    if len(labels) < 2:
        raise DomainError("no_suffix", "domain has no public suffix")
    for label in labels:
        if not _LABEL_RE.match(label):
            raise DomainError("invalid_label", f"invalid label: {label!r}")
    if all(label.isdigit() for label in labels):
        raise DomainError("ip_address", "IP addresses are not domains")

    for special in _SPECIAL_USE_SUFFIXES:
        if ascii_host == special or ascii_host.endswith("." + special):
            raise DomainError("special_use", f"special-use name: .{special}")

    parts = _EXTRACT(ascii_host)
    if not parts.suffix or not parts.domain:
        raise DomainError("no_suffix", "domain is not under a known public suffix")
    registrable = parts.top_domain_under_public_suffix

    try:
        display = idna.decode(ascii_host)
    except idna.IDNAError:
        display = ascii_host
    return NormalizedDomain(ascii=ascii_host, display=display, registrable=registrable, public_suffix=parts.suffix)


def normalize_url(raw: str) -> NormalizedUrl:
    """Canonical form of an http(s) URL: lower-case scheme/host, punycode host, default port and fragment
    removed, empty path becomes ``/``. Query strings are kept verbatim (their order can be meaningful)."""
    value = raw.strip()
    if "://" not in value:
        value = "http://" + value
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise DomainError("scheme", f"unsupported scheme: {scheme}")
    if parts.username or parts.password:
        raise DomainError("credentials", "URLs with credentials are not accepted")
    try:
        port = parts.port
    except ValueError as exc:
        raise DomainError("port", "invalid port") from exc
    domain = normalize_domain(parts.hostname or "")
    netloc = domain.ascii if port in (None, _DEFAULT_PORTS[scheme]) else f"{domain.ascii}:{port}"
    url = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    return NormalizedUrl(url=url, domain=domain, sha256=hashlib.sha256(url.encode()).hexdigest())
