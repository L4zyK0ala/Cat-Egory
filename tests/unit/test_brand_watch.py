import idna
import pytest

from integrations.brand_watch import Brand, Watchlist, default_watchlist, skeleton


@pytest.mark.parametrize(("name", "brand"), [
    ("garantibbva-giris.com", "Garanti BBVA"), ("garant1bbva.net", "Garanti BBVA"),
    ("ptt-kargo-odeme.com", "PTT"), ("edevlet-destek.org", "e-Devlet"), ("e-devlet-giris.com", "e-Devlet"),
    ("hgs-ceza-sorgu.com", "HGS"), ("ha1kbank-giris.xyz", "Halkbank"), ("*.yapikredi-kampanya.top", "Yapı Kredi"),
    ("isbank.secure-login.top", "İş Bankası"), ("trendyoll-indirim.shop", "Trendyol"),
    (idna.encode("garantıbbva.com", uts46=True).decode(), "Garanti BBVA"),        # dotless ı (IDN)
    (idna.encode("аkbank.com", uts46=True).decode(), "Akbank"),                    # Cyrillic а
])
def test_lookalikes_match(name, brand):
    assert default_watchlist().match(name).brand.name == brand


@pytest.mark.parametrize("name", [
    "garantibbva.com.tr", "mobil.garantibbva.com.tr",   # official
    "ptt.gov.tr", "kurum.gov.tr", "uni.edu.tr",         # only vetted holders can register these
    "optt.com", "mytebsite.com", "ziraatodasi.org.tr",  # short tokens need a whole part; "ziraat" alone is farming
    "wikipedia.org", "trendyol.com", "not a name", "", "*.",
])
def test_no_match(name):
    assert default_watchlist().match(name) is None


def test_skeleton_folds_turkish_and_lookalikes():
    assert skeleton("VakıfBank") == skeleton("vakifbank") == skeleton("vak1fbank")
    assert skeleton("rnobil") == skeleton("mobil")


def test_custom_watchlist():
    w = Watchlist([Brand("Acme", "bank", substrings=("acmebank",), tokens=("acb",), official=("acmebank.com",))])
    assert w.match("acmebank-login.net").matched == "acmebank"
    assert w.match("pay-acb.net").matched == "acb"
    assert w.match("www.acmebank.com") is None
