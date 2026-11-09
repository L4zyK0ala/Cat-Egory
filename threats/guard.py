"""Guard against flagging a popular or official site as a threat by mistake.

E.g. api-glb-ause1a.smoot.apple.com (Apple's Spotlight/Siri backend) was once confirmed as "scam" in review.
Before a person's threat decision is stored for a domain that is, or is under, one of

* the popular sites of the look-alike index (Majestic top ``lookalike_top_n``),
* an official domain of a watched brand (integrations/ct_watchlist.yaml),
* a suffix only vetted holders can register (.gov.tr, .edu.tr, ...; classification/registry_rules.py),

the API answers 409 ``protected_domain`` and the client must confirm (``confirm_protected``). Look-alikes of these
sites are not affected: a look-alike is a different registrable domain.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from classification.registry_rules import rule_for
from common.config import get_settings
from integrations.brand_watch import default_watchlist
from integrations.lookalike import top_index


def protected_owner(session: Session, name: str) -> str | None:
    """What makes ``name`` (normalised ASCII) look legitimate: the popular/official domain it belongs to, or the
    restricted suffix. None if nothing."""
    parts = name.lower().rstrip(".").split(".")
    domains = top_index(session, get_settings().lookalike_top_n).domains
    for i in range(len(parts) - 1):
        if (candidate := ".".join(parts[i:])) in domains:
            return candidate
    if official := default_watchlist().official_domain(name):
        return official
    if rule := rule_for(name):
        return f".{rule.suffix}"
    return None
