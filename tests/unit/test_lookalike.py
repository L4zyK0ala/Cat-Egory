import idna
import pytest

from integrations.lookalike import TopIndex, _osa1

TOP = TopIndex.build(["google.com", "amazon.com", "facebook.com", "wikipedia.org", "linkedin.com", "t.co",
                      "paypal.com", "bbc.co.uk", "sub.example.com"])


def puny(name: str) -> str:
    return name if name.isascii() else idna.encode(name, uts46=True).decode()


@pytest.mark.parametrize(("name", "kind", "target"), [
    ("аmazon.com", "homograph", "amazon.com"),      # Cyrillic а (the classic attack)
    ("amaz0n.net", "homograph", "amazon.com"),       # digit for letter, any suffix
    ("googIe.com", "homograph", "google.com"),       # capital I for l
    ("wikipedla.org", "homograph", "wikipedia.org"),
    ("paypa1.com", "homograph", "paypal.com"),
    ("amazom.com", "typo", "amazon.com"), ("amzaon.com", "typo", "amazon.com"), ("gooogle.com", "typo", "google.com"),
    ("facebok.com", "typo", "facebook.com"), ("login.amazom.com", "typo", "amazon.com"),
])
def test_lookalikes(name, kind, target):
    hit = TOP.check(puny(name))
    assert (hit.kind, hit.target) == (kind, target)


@pytest.mark.parametrize("name", [
    "amazon.com", "www.amazon.com",                  # the protected domain and its subdomains
    "amazon.de", "wikipedia.com", "paypal.co.uk",   # TLD swaps are usually the owner's own sites
    "t.co", "x.co", "bbc.com",                       # too short to judge
    "pineapple.com", "example.org", "", "not a name",
    "amazon-shop.org",                               # added words are for curated brands (ct_watchlist.yaml)
])
def test_not_lookalikes(name):
    assert TOP.check(name) is None


def test_edit_distance_one():
    assert _osa1("amazon", "amazom") and _osa1("amazon", "amzaon") and _osa1("google", "gooogle")
    assert not _osa1("amazon", "amazon") and not _osa1("amazon", "amzaom") and not _osa1("abc", "abcde")


def test_empty_index_matches_nothing():
    assert TopIndex.build([]).check("amaz0n.com") is None
