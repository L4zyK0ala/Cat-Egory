"""Categories that follow from who may register a domain, not from what a crawler saw.

Some registries only allocate a suffix to one kind of holder, after checking documents. For Türkiye, TRABİS
publishes this for the "belgeli" (documented) suffixes, e.g. ".gov.tr: Kamu kurum ve kuruluşlarına tahsis edilir"
(https://www.trabis.gov.tr/content/Belgeli, checked 2026-11-08; governing text: BTK decision 346-2020).

Such a domain's category is therefore known without visiting it. The rule is used only as a fallback: when a
scan of a live domain fails or is refused (several Turkish public sites drop requests from any user agent that
says "bot", and Cat-egory never hides that it is one) and nothing better is known. Any classification from the
AI, a reviewer, the community or a licensed source takes precedence; a later successful scan may replace it.

Assignments carry origin "rule" and the data source ``cat-egory-registry-rules``, so the public API and feeds
show where they came from. Only suffixes whose holders map to exactly one category are listed.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from database.models import AssignmentOrigin, ClassificationStatus, Domain

from .engine import apply_categories, current_assignments
from .taxonomy import Taxonomy, default_taxonomy

RULES_SOURCE = "cat-egory-registry-rules"
TRABIS = "https://www.trabis.gov.tr/content/Belgeli"


@dataclass(frozen=True, slots=True)
class RegistryRule:
    suffix: str
    category: str
    holders: str
    reference: str = TRABIS


RULES: tuple[RegistryRule, ...] = (
    RegistryRule("gov.tr", "government", "public institutions and bodies"),
    RegistryRule("bel.tr", "government", "municipalities in the Ministry of Interior's records"),
    RegistryRule("pol.tr", "government", "the General Directorate of Security (police) and its units"),
    RegistryRule("tsk.tr", "government", "units of the Turkish Armed Forces"),
    RegistryRule("edu.tr", "education", "higher-education institutions recognised by YÖK"),
    RegistryRule("k12.tr", "education", "the Ministry of National Education and schools it approved"),
    RegistryRule("av.tr", "legal", "lawyers, law offices and law partnerships"),
    RegistryRule("dr.tr", "health-information", "medical doctors and doctor partnerships"),
    RegistryRule("kep.tr", "email", "authorised registered e-mail (KEP) service providers"),
)
_BY_SUFFIX = {r.suffix: r for r in RULES}


def rule_for(name: str) -> RegistryRule | None:
    """The rule for a normalised (ASCII, lowercase) domain name, matching whole labels only."""
    labels = name.lower().rstrip(".").split(".")
    for i in range(1, len(labels)):  # a holder's name is required: "gov.tr" itself matches nothing
        if rule := _BY_SUFFIX.get(".".join(labels[i:])):
            return rule
    return None


def apply_rule(session: Session, domain: Domain, taxonomy: Taxonomy | None = None) -> RegistryRule | None:
    """Classify ``domain`` from its suffix if a rule exists and nothing else classified it. Returns the rule
    applied, or None."""
    rule = rule_for(domain.normalized_domain)
    if rule is None:
        return None
    if any(row.origin != AssignmentOrigin.RULE for row in current_assignments(session, domain.id)):
        return None
    apply_categories(session, domain, primary=rule.category, secondary=[], confidence=None,
                     origin=AssignmentOrigin.RULE, taxonomy=taxonomy or default_taxonomy(),
                     status=ClassificationStatus.CLASSIFIED, source_slug=RULES_SOURCE)
    return rule
