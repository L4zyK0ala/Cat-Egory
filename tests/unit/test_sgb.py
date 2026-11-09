from threats.sgb import matches_for

MODELS = [
    {"id": 1, "url": "evil.example.net", "type": "domain", "desc": "PH", "criticality_level": 4, "date": "2026-11-09"},
    {"id": 2, "url": "login.evil.example.net", "type": "domain", "desc": "BP", "criticality_level": 3},
    {"id": 3, "url": "https://evil.example.net/odeme?x=1", "type": "url", "desc": "PH"},
    {"id": 4, "url": "notevil.example.net", "type": "domain", "desc": "PH"},          # substring hit of the search
    {"id": 5, "url": "evil.example.net.attacker.org", "type": "domain", "desc": "PH"},
    {"id": 6, "url": "203.0.113.7", "type": "ip", "desc": "CA"},
    {"id": 7, "url": "EVIL.example.net", "type": "domain", "desc": "MD"},             # case
]


def test_only_the_domain_its_subdomains_and_urls_on_it_match():
    got = {m["id"]: m["scope"] for m in matches_for("evil.example.net", MODELS)}
    assert got == {1: "exact", 2: "subdomain", 3: "url", 7: "exact"}


def test_match_keeps_the_lists_fields():
    m = matches_for("evil.example.net", MODELS)[0]
    assert (m["category"], m["criticality"], m["date"], m["type"]) == ("PH", 4, "2026-11-09", "domain")


def test_idn_values_are_compared_in_ascii():
    assert matches_for("xn--bcher-kva.de", [{"id": 9, "url": "bücher.de", "type": "domain"}])[0]["id"] == 9
