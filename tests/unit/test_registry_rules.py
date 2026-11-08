import pytest

from classification.registry_rules import RULES, rule_for
from classification.taxonomy import default_taxonomy


@pytest.mark.parametrize(("name", "category"), [
    ("ptt.gov.tr", "government"), ("www.resmigazete.gov.tr", "government"), ("kadikoy.bel.tr", "government"),
    ("metu.edu.tr", "education"), ("x.meb.k12.tr", "education"), ("buro.av.tr", "legal"),
    ("klinik.dr.tr", "health-information"), ("saglayici.kep.tr", "email"), ("PTT.GOV.TR.", "government"),
])
def test_rule_matches_whole_labels(name, category):
    assert rule_for(name).category == category


@pytest.mark.parametrize("name", [
    "gov.tr",                 # the suffix itself has no holder
    "notgov.tr", "mygov-tr.com", "gov.tr.example.com", "example.com.tr", "isbank.com.tr", "example.org",
    "foo.mil.tr",             # not in TRABİS's documented list: no rule
])
def test_no_rule(name):
    assert rule_for(name) is None


def test_rule_categories_exist_in_taxonomy():
    content = set(default_taxonomy().assignable_ids("content"))
    assert {r.category for r in RULES} <= content
    assert len({r.suffix for r in RULES}) == len(RULES)
