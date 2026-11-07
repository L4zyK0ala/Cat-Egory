import pytest

from common.domains import DomainError, normalize_domain, normalize_url


@pytest.mark.parametrize(
    ("raw", "ascii_", "registrable"),
    [
        ("Example.COM.", "example.com", "example.com"),
        ("  www.example.co.uk ", "www.example.co.uk", "example.co.uk"),
        ("https://Bücher.de:443/path#x", "xn--bcher-kva.de", "xn--bcher-kva.de"),
        ("xn--bcher-kva.de", "xn--bcher-kva.de", "xn--bcher-kva.de"),
        ("user.github.io", "user.github.io", "user.github.io"),  # private PSL suffix
        ("example.com:8080", "example.com", "example.com"),
        ("example.com/some/path", "example.com", "example.com"),
    ],
)
def test_normalize_valid(raw, ascii_, registrable):
    d = normalize_domain(raw)
    assert d.ascii == ascii_
    assert d.registrable == registrable


def test_display_form_is_unicode():
    assert normalize_domain("xn--bcher-kva.de").display == "bücher.de"


def test_turkish_letters():
    # UTS #46 (what browsers resolve): Ş -> ş. Note: İ maps to "i" + U+0307, NOT the Turkish lower-case "i",
    # so "ŞİRKET" and "şirket" are different hostnames on the wire. We follow the standard, not locale rules.
    assert normalize_domain("Şirket.com.tr").ascii == normalize_domain("şirket.com.tr").ascii
    assert normalize_domain("şirket.com.tr").display == "şirket.com.tr"
    assert normalize_domain("ŞİRKET.com.tr").ascii != normalize_domain("şirket.com.tr").ascii


def test_same_site_different_spellings_collapse():
    spellings = ["EXAMPLE.com", "example.com.", "http://example.com/", "example.com:443"]
    assert {normalize_domain(s).ascii for s in spellings} == {"example.com"}


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("", "empty"),
        ("localhost", "no_suffix"),
        ("10.0.0.1", "ip_address"),
        ("169.254.169.254", "ip_address"),
        ("[::1]", "ip_address"),
        ("http://[fd00::1]/", "ip_address"),
        ("router.home.arpa", "special_use"),
        ("printer.local", "special_use"),
        ("db.internal", "special_use"),
        ("abc.onion", "special_use"),
        ("http://user:pass@example.com", "credentials"),
        ("user@example.com", "credentials"),
        ("*.example.com", "wildcard"),
        ("a..b.com", "idna"),
        ("-bad-.com", "idna"),
        ("x" * 64 + ".com", "idna"),
        ("nosuffix", "no_suffix"),
    ],
)
def test_normalize_rejects(raw, code):
    with pytest.raises(DomainError) as exc:
        normalize_domain(raw)
    assert exc.value.code == code


def test_too_long():
    with pytest.raises(DomainError) as exc:
        normalize_domain(".".join(["a" * 60] * 5) + ".com")
    assert exc.value.code == "too_long"


def test_normalize_url():
    u = normalize_url("HTTPS://Bücher.DE:443?q=1#frag")
    assert u.url == "https://xn--bcher-kva.de/?q=1"
    assert len(u.sha256) == 64
    assert normalize_url("example.com").url == "http://example.com/"
    assert normalize_url("http://example.com:8080/a").url == "http://example.com:8080/a"


@pytest.mark.parametrize("raw", ["ftp://example.com/", "file:///etc/passwd", "javascript:alert(1)",
                                 "http://u:p@example.com/", "http://127.0.0.1/", "http://example.com:99999/"])
def test_normalize_url_rejects(raw):
    with pytest.raises(DomainError):
        normalize_url(raw)
