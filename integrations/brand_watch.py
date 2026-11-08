"""Match new host names against Turkish brands (integrations/ct_watchlist.yaml) to spot look-alike domains early.

Used by the certificate-transparency watcher: every name in a new certificate is checked; a match is added as a
domain from the source ``ct-brand-watch`` and scanned at high priority. Such domains always go to a person
(classification/engine.py): a phishing copy of a bank would otherwise be published as "banking".

Matching works on a *skeleton* of the name, the same for brand strings and candidates: lower case, IDN decoded,
Turkish letters folded (ı→i, ş→s, …), accents removed, common look-alikes folded (Cyrillic а→a, 0→o, 1/l→i,
rn→m, vv→w, …). ``substrings`` (long, distinctive) match anywhere; ``tokens`` (short, ambiguous) only as a whole
part between "-" or ".". Names under a brand's official domains, and under suffixes that only public bodies can
register (.gov.tr, .edu.tr, … — classification/registry_rules.py), never match.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import idna
import yaml

from common.domains import DomainError, normalize_domain

WATCHLIST = Path(__file__).with_name("ct_watchlist.yaml")

_TURKISH = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c", "â": "a", "î": "i", "û": "u"})
# Confusable characters seen in phishing names: Cyrillic/Greek letters that look Latin, digits and symbols.
_LOOKALIKE = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "к": "k", "м": "m", "т": "t", "в": "b",
    "н": "h", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ɡ": "g", "ո": "n", "α": "a", "ο": "o", "ρ": "p", "ν": "v",
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s", "!": "i", "|": "i",
    "l": "i",
})
_SPLIT = re.compile(r"[-.]+")


def skeleton(text: str) -> str:
    if text.isascii():  # nearly every certificate name: no Unicode normalisation needed
        return text.lower().translate(_LOOKALIKE).replace("rn", "m").replace("vv", "w")
    t = unicodedata.normalize("NFKC", text).lower().translate(_TURKISH)
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    return t.translate(_LOOKALIKE).replace("rn", "m").replace("vv", "w")


@dataclass(frozen=True, slots=True)
class Brand:
    name: str
    sector: str
    substrings: tuple[str, ...] = ()
    tokens: tuple[str, ...] = ()
    official: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.name} ({self.sector})"


@dataclass(frozen=True, slots=True)
class Match:
    name: str           # the normalised host name that matched (ASCII)
    registrable: str
    brand: Brand
    matched: str        # the brand string that matched


@dataclass
class Watchlist:
    brands: list[Brand]
    _subs: list[tuple[str, Brand, str]] = field(default_factory=list)
    _tokens: dict[str, tuple[Brand, str]] = field(default_factory=dict)
    _official: set[str] = field(default_factory=set)
    _quick: re.Pattern | None = None

    def __post_init__(self) -> None:
        for b in self.brands:
            self._subs += [(skeleton(s), b, s) for s in b.substrings]
            self._tokens.update({skeleton(t): (b, t) for t in b.tokens})
            self._official.update(o.lower() for o in b.official)
        # Cheap pre-filter: almost no certificate name contains any brand string, so most names are dropped by one
        # regex search before the (slower) domain normalisation. Hyphens are removed as in the joined check.
        needles = sorted({x for x, _, _ in self._subs} | set(self._tokens), key=len, reverse=True)
        self._quick = re.compile("|".join(map(re.escape, needles))) if needles else None

    def _maybe(self, name: str) -> bool:
        if self._quick is None:
            return False
        text = name
        if "xn--" in name:
            try:
                text = idna.decode(name)
            except idna.IDNAError:
                pass
        return self._quick.search(skeleton(text).replace("-", "")) is not None

    def _under_official(self, name: str) -> bool:
        parts = name.split(".")
        return any(".".join(parts[i:]) in self._official for i in range(len(parts) - 1))

    def _official_or_restricted(self, name: str, registrable: str) -> bool:
        from classification.registry_rules import rule_for

        if registrable in self._official or any(name == o or name.endswith("." + o) for o in self._official):
            return True
        return rule_for(name) is not None  # .gov.tr, .edu.tr, ...: only vetted holders can register these

    def match(self, raw: str) -> Match | None:
        name = raw.strip().lower().rstrip(".")
        if name.startswith("*."):
            name = name[2:]
        if not name or " " in name or self._under_official(name) or not self._maybe(name):
            return None
        try:
            nd = normalize_domain(name)
        except DomainError:
            return None
        if self._official_or_restricted(nd.ascii, nd.registrable):
            return None
        host = nd.ascii[: -len(nd.public_suffix) - 1] if nd.public_suffix and nd.ascii.endswith("." + nd.public_suffix) \
            else nd.ascii
        try:
            host = idna.decode(host)
        except idna.IDNAError:
            pass
        sk = skeleton(host)
        tokens = [t for t in _SPLIT.split(sk) if t]
        for tok in tokens:
            if hit := self._tokens.get(tok):
                return Match(nd.ascii, nd.registrable, hit[0], hit[1])
        joined = "".join(tokens)
        for sub, brand, original in self._subs:
            if sub in joined:
                return Match(nd.ascii, nd.registrable, brand, original)
        return None


def load_watchlist(path: Path = WATCHLIST) -> Watchlist:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Watchlist([Brand(name=b["name"], sector=b["sector"], substrings=tuple(b.get("substrings", [])),
                            tokens=tuple(b.get("tokens", [])), official=tuple(b.get("official", [])))
                      for b in raw["brands"]])


@lru_cache(maxsize=1)
def default_watchlist() -> Watchlist:
    return load_watchlist()
