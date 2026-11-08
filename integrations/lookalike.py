"""Look-alikes of the most popular domains (homographs and one-letter typos), found automatically.

The protected list is the top ``CATEGORY_LOOKALIKE_TOP_N`` registrable domains of the approved discovery source
``majestic-million-top`` (refreshed weekly by the source sync), so no hand-written list is needed. Two signals:

* **homograph** — the name's label reads the same as a protected label once look-alike characters are folded
  (integrations/brand_watch.skeleton: Cyrillic а→a, 0→o, 1/l→i, rn→m, …) but is not the same text:
  ``аmazon.com`` (Cyrillic а), ``amaz0n.net``. A plain TLD swap (``amazon.de``) is *not* flagged: it is very often
  the owner's own site.
* **typo** — same public suffix, labels of ≥ 6 characters, edit distance 1 (one letter added, removed, replaced or two
  swapped): ``amazom.com``, ``gooogle.com``. Found with a deletion index, so checking a name is O(label length).

A hit never sets a public flag by itself: the domain goes to a person (classification/engine.py), who may confirm
the risk category ``lookalike-domain``. Big companies register their own look-alikes; those must not be blocked.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import idna
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.domains import DomainError, normalize_domain

from .brand_watch import skeleton

log = logging.getLogger("category.lookalike")
TOP_SOURCE = "majestic-million-top"
MIN_HOMOGRAPH = 4   # labels shorter than this ("t.co", "x.com") collide with too many ordinary names
MIN_TYPO = 6


@dataclass(frozen=True, slots=True)
class Lookalike:
    name: str
    target: str   # the protected domain it imitates
    kind: str     # homograph | typo


def _osa1(a: str, b: str) -> bool:
    """True if a and b differ by exactly one edit (insert, delete, substitute or swap of neighbours)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _deletes(s: str) -> set[str]:
    return {s} | {s[:i] + s[i + 1:] for i in range(len(s))}


def _label(name: str, suffix: str) -> str:
    label = name[: -len(suffix) - 1] if suffix and name.endswith("." + suffix) else name
    try:
        return idna.decode(label)
    except idna.IDNAError:
        return label


@dataclass
class TopIndex:
    domains: set[str] = field(default_factory=set)
    by_skeleton: dict[str, list[tuple[str, str]]] = field(default_factory=dict)   # skeleton -> [(domain, label)]
    by_delete: dict[tuple[str, str], set[tuple[str, str]]] = field(default_factory=dict)  # (del, suffix) -> {(dom, sk)}
    delete_keys: set[str] = field(default_factory=set)  # every deletion string, for the cheap pre-filter

    @classmethod
    def build(cls, registrable_domains: list[str]) -> TopIndex:
        idx = cls()
        for raw in registrable_domains:
            try:
                nd = normalize_domain(raw)
            except DomainError:
                continue
            if nd.registrable != nd.ascii:
                continue
            idx.domains.add(nd.ascii)
            label = _label(nd.ascii, nd.public_suffix)
            sk = skeleton(label)
            if len(sk) >= MIN_HOMOGRAPH:
                idx.by_skeleton.setdefault(sk, []).append((nd.ascii, label))
            if len(sk) >= MIN_TYPO:
                for d in _deletes(sk):
                    idx.by_delete.setdefault((d, nd.public_suffix), set()).add((nd.ascii, sk))
                    idx.delete_keys.add(d)
        return idx

    def protected(self, name: str) -> bool:
        """True if the name is a protected domain or under one (a big share of all certificates: CDN and cloud
        subdomains of popular sites). Plain set lookups, no normalisation."""
        parts = name.split(".")
        return any(".".join(parts[i:]) in self.domains for i in range(len(parts) - 1))

    def _maybe(self, name: str) -> bool:
        """Cheap pre-filter without domain normalisation: could the registrable label be a protected label or one
        edit away from it? Only the label left of the suffix is judged by check(), so only it is tested here: the
        second-to-last label, and the third-to-last for two-part suffixes such as .com.tr or .co.uk. (Subdomain
        labels like "mail" would otherwise match popular domains such as mail.ru on every second certificate.)"""
        parts = name.split(".")
        for label in parts[-3:-1]:
            if label.startswith("xn--"):
                try:
                    label = idna.decode(label)
                except idna.IDNAError:
                    continue
            sk = skeleton(label)
            if sk in self.by_skeleton:
                return True
            if len(sk) >= MIN_TYPO - 1 and not self.delete_keys.isdisjoint(_deletes(sk)):
                return True
        return False

    def check(self, raw: str) -> Lookalike | None:
        name = raw.strip().lower().rstrip(".").removeprefix("*.")
        if not name or not self.domains or self.protected(name) or not self._maybe(name):
            return None
        try:
            nd = normalize_domain(name)
        except DomainError:
            return None
        if nd.registrable in self.domains:
            return None  # a protected domain or one of its subdomains
        label = _label(nd.registrable, nd.public_suffix)
        sk = skeleton(label)
        for target, target_label in self.by_skeleton.get(sk, []) if len(sk) >= MIN_HOMOGRAPH else []:
            if label != target_label:
                return Lookalike(nd.registrable, target, "homograph")
        if len(sk) >= MIN_TYPO - 1:
            for d in _deletes(sk):
                for target, target_sk in self.by_delete.get((d, nd.public_suffix), ()):
                    if _osa1(sk, target_sk):
                        return Lookalike(nd.registrable, target, "typo")
        return None


def load_top_index(session: Session, top_n: int) -> TopIndex:
    from database.models import DataSource, Domain, DomainSourceRecord

    rows = session.scalars(
        select(Domain.normalized_domain).join(DomainSourceRecord, DomainSourceRecord.domain_id == Domain.id)
        .join(DataSource, DataSource.id == DomainSourceRecord.source_id)
        .where(DataSource.slug == TOP_SOURCE, DataSource.enabled, DomainSourceRecord.is_listed,
               DomainSourceRecord.source_rank <= top_n)
        .order_by(DomainSourceRecord.source_rank))
    return TopIndex.build(list(rows))


_cache: tuple[float, int, TopIndex] | None = None


def top_index(session: Session, top_n: int, max_age: float = 3600.0) -> TopIndex:
    """Process-wide cache, rebuilt at most hourly (the source itself changes weekly)."""
    global _cache
    if _cache is None or _cache[1] != top_n or time.monotonic() - _cache[0] > max_age:
        _cache = (time.monotonic(), top_n, load_top_index(session, top_n))
        log.info("top-domain index loaded", extra={"domains": len(_cache[2].domains)})
    return _cache[2]
