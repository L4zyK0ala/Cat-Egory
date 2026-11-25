"""Our own page-proof threat detector (THREATS.md, phases 2 and 3).

Deterministic rules, not a model's opinion. A scanned page is judged by two of them:

**Phishing:** it asks for a secret (a password, a card number or CVV, an SMS / one-time code, a Turkish ID number).
**Investment scam:** it asks for a phone number and talks investment ("yatırım", "alım satım", "yapay zeka",
"resmi web sitesi", trading, profit, ...: at least two such terms), the fake "AI trading platform of <bank>" pages.

Either way, all of this must hold as well:

* it claims a watched brand, Turkish (integrations/ct_watchlist.yaml) or worldwide (integrations/global_brands.yaml):
  in its title or description, else by a distinctive brand string in its host name (unless the page goes by that
  host word as its own, different name: "Fraisbank" is not İş Bankası), else as the only watched brand its first
  lines name (a page naming several brands only in its text lists them, e.g. as payment methods, and claims none);
* it is not that brand's official site, nor any popular, official or restricted-suffix host (threats/guard.py),
  nor under a registrable domain that a popularity list ranks (Majestic top list, Chrome UX Report Türkiye):
  a brand group's own sites abroad (vodafone.co.uk) are established sites, not phishing;
* and the host is suspicious by itself: its name imitates a brand or a top site, a threat list names it
  (threats/feeds.py), or its certificate is under 30 days old.

History: ``page_proven_v1`` (2026-11-17) measured 40 % precision: it called Vodafone UK's own sign-in pages and a
gambling site's list of banks phishing, and missed pages whose brand was only in their logo and address.
``page_proven_v2`` added the popularity, host-name and listed-brands parts (replayed on v1's pages: no wrong flag
left). ``page_proven_v3`` (2026-11-18) added the investment-scam rule and the worldwide brands (Cat-egory lists
threats worldwide, not only Turkish ones). A page's language is recorded (``turkish``) but not required: phishing of
Turkish brands abroad (Ziraat in Greece) is phishing too.
``page_proven_v4`` (2026-11-25) answers v3's nine wrong flags out of 94 that people decided, all of them a brand the
page does not claim: a brand string inside another word (chickENPARAdise, primISBANK, peNABIZama, PAPARAzzi) now
counts in a page's text only alone or with the words phishing glues to it (``_AFFIXES``), and in a host name not
when the letters before it come from the page's own name (Chicken Paradise, Primis); the Turkish company of an
international group (``group`` in the watchlist: Vodafone TR, QNB, DenizBank) is not claimed by a non-Turkish page
under another country's domain (vodafone.al, a .cz partner portal); a brand only in a subdomain of an unremarkable
site (vodafone.pyramid-online.net, a customer's space at a service) needs a threat list or a new certificate as
well; and a social platform (WhatsApp, Telegram, ...) that a page names only in its description or text is an
integration, not a claim.

**Phase 2 (shadow):** every verdict on a threat candidate, and every positive one, is stored in
``detector_verdicts`` and later compared with what a person decided (``measure``). Nothing is flagged.

**Phase 3 (on its own):** with ``CATEGORY_THREAT_DETECTOR_AUTO=true`` a positive verdict flags the domain (phishing,
or scam for an investment scam; plus look-alike when the name imitates a brand) from the source
``cat-egory-detector`` — or only the page's URL, when the scan was of one page a list named (threats/pages.py) — but
only while the rule has earned it: at least ``threat_detector_min_decisions`` people's decisions on its positive
verdicts, at least ``threat_detector_min_precision`` of them right, at most ``threat_detector_max_per_day`` flags a
day. Every ``threat_detector_sample_every``-th flag still goes to a person, so the measurement goes on; any flag
can be withdrawn like a reviewer's (it is one of our own sources).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from common.config import Settings
from common.timeutil import local_midnight
from database.models import (
    DataSource,
    DetectorVerdict,
    Domain,
    ReviewStatus,
    ReviewTask,
    ScanResult,
    ThreatFeedEntry,
    ThreatIntelligence,
)

log = logging.getLogger("category.threats.detector")
RULE = "page_proven_v4"
SOURCE = "cat-egory-detector"  # = classification.engine.DETECTOR_SOURCE
FRESH_CERT_DAYS = 30
POPULAR_SOURCES = ("majestic-million-top", "crux-top-tr")  # registrable domains these rank are established sites
PHISHING, INVESTMENT_SCAM = "phishing", "investment_scam"
THREATS = {PHISHING: "phishing", INVESTMENT_SCAM: "scam"}  # what a positive verdict of each kind flags

# Letters only Turkish (and its close kin) writes, and words a Turkish login or payment page uses. Folded words:
# ı→i, ş→s, ğ→g, ü→u, ö→o, ç→c.
_TR_LETTERS = set("ğĞıİşŞ")
_TR_WORDS = frozenset({"giris", "sifre", "sifrenizi", "parola", "musteri", "hesap", "hesabiniz", "hesabinizi",
                       "kullanici", "dogrulama", "kimlik", "sube", "subesi", "lutfen", "islem", "odeme", "kredi",
                       "numarasi", "numaraniz", "icin", "tiklayin", "devam", "bilgileriniz", "guvenli", "kodu"})
_FOLD = str.maketrans("ışğüöçİÇŞĞÜÖ", "isguociCSGUO")
# Investment talk, Turkish and English (folded, lower case). A term matches where a word starts with it (Turkish
# adds endings: "yatırımınızı"); those in _INVEST_EXACT only as a whole word.
_INVEST = ("yatirim", "alim satim", "yapay zeka", "borsa", "kripto", "resmi web sitesi", "resmi site", "kazanc",
           "getiri", "analist", "hisse", "pasif gelir", "fon", "trading", "trade", "invest", "official website",
           "official site", "profit", "crypto", "forex", "passive income", "bitcoin")
_INVEST_EXACT = frozenset({"fon", "trade"})

_ATTR = r"""\b(?:name|id|placeholder|aria-label)\s*=\s*['"]?[^'">]*"""
_SECRETS = {
    "password": re.compile(r"""<input\b[^>]*\btype\s*=\s*['"]?password""", re.I),
    "card": re.compile(r"""<input\b[^>]*(?:autocomplete\s*=\s*['"]?cc-(?:number|csc|exp)|"""
                       + _ATTR + r"""(?:card[-_ ]?(?:no|num|number)|kart[-_ ]?(?:no|num|numara)|cvv|cvc|ccnum|cardnumber))""",
                       re.I),
    "sms_code": re.compile(r"""<input\b[^>]*""" + _ATTR + r"""(?:\botp\b|sms[-_ ]?(?:kod|code)|onay[-_ ]?kod|"""
                           r"""dogrulama[-_ ]?kod|doğrulama[-_ ]?kod)""", re.I),
    "tr_id": re.compile(r"""<input\b[^>]*""" + _ATTR + r"""(?:tckn|tc[-_ ]?kimlik|kimlik[-_ ]?no)""", re.I),
}
_PHONE = re.compile(r"""<input\b[^>]*(?:\btype\s*=\s*['"]?tel\b|autocomplete\s*=\s*['"]?tel\b|"""
                    + _ATTR + r"""(?:phone|telefon|tel[-_ ]?no|gsm|\bcep|mobile|msisdn))""", re.I)


@dataclass
class Page:
    host: str
    title: str | None
    description: str | None
    text: str | None
    html: str | None
    cert_valid_from: float | None  # seconds since the epoch
    scanned_at: datetime
    language: str | None = None  # the page's declared language (html lang), if any


@dataclass(frozen=True)
class Claim:
    brand: str
    official: tuple[str, ...]
    via: str  # "page" (title / description / the only brand in the text) or "host" (its address names the brand)
    subdomain: bool = False  # via the host, and the brand is only in a subdomain, not in the registrable name


def secrets(html: str | None) -> list[str]:
    return [name for name, pattern in _SECRETS.items() if html and pattern.search(html)]


def phone_form(html: str | None) -> bool:
    return bool(html and _PHONE.search(html))


def _watchlists():
    from integrations.brand_watch import default_watchlist, global_watchlist

    return default_watchlist(), global_watchlist()


def _words(text: str) -> str:
    from integrations.brand_watch import skeleton

    return " " + re.sub(r"[^a-z0-9]+", " ", skeleton(text)) + " "


# What phishing glues to a brand's name in one word: "myenpara", "ziraatbankgiris", "garantibbvamobil", "edevlet2".
# Any other letters make it a different word: chickENPARAdise, primISBANK, peNABIZama, PAPARAzzi (page_proven_v4).
_AFFIX_WORDS = ("www", "m", "e", "my", "the", "secure", "guvenli", "online", "login", "signin", "giris", "mobil",
                      "mobile", "app", "web", "net", "internet", "sube", "tr", "turkiye", "destek", "support", "help",
                      "hesap", "account", "musteri", "verify", "dogrula", "dogrulama", "onay", "update", "guncelle",
                      "bonus", "kampanya", "odeme", "pay", "kart", "card", "kredi", "bireysel", "kurumsal", "info",
                      "official", "resmi", "service", "servis", "portal")


@lru_cache(maxsize=1)
def _affix_set() -> frozenset[str]:
    from integrations.brand_watch import skeleton

    return frozenset(skeleton(a) for a in _AFFIX_WORDS)  # compared with skeletons: "mobil" is "mobii" there


def _affixes(part: str) -> bool:
    """Whether ``part`` (a skeleton) is nothing but phishing affixes and digits ("", "my", "secure2", "oniinegiris")."""
    if not part or part.isdigit():
        return True
    return any(part.startswith(a) and _affixes(part[len(a):]) for a in _affix_set()) or \
        (part[0].isdigit() and _affixes(part.lstrip("0123456789")))


def glued(word: str, needle: str, *, any_suffix: bool = False) -> bool:
    """Whether ``word`` is the brand string ``needle`` alone or with phishing affixes around it. ``any_suffix``: a
    word that starts with the brand string counts whatever follows (host names: "vodafonepartner", "ziraatbankx")."""
    i = word.find(needle)
    while i != -1:
        rest = word[i + len(needle):]
        if _affixes(word[:i]) and (_affixes(rest) or (any_suffix and i == 0)):
            return True
        i = word.find(needle, i + 1)
    return False


def brands_named(text: str | None) -> list:
    """Watched brands a text names, Turkish first: by the brand's name as a phrase, by a whole token, or by a
    distinctive substring alone or with phishing affixes ("garantibbvamobil" yes; "fraisbank" is not İş Bankası,
    "Paparazzi" is not Papara)."""
    from integrations.brand_watch import skeleton

    if not text:
        return []
    words = _words(text)
    found = []
    for watchlist in _watchlists():
        for brand in watchlist.brands:
            name = re.sub(r"[^a-z0-9]+", " ", skeleton(brand.name)).strip()
            if (name and f" {name} " in words) or any(f" {skeleton(t)} " in words for t in brand.tokens) \
                    or any(_in_words(words, skeleton(s).replace("-", "")) for s in brand.substrings):
                found.append(brand)
    return found


def _in_words(words: str, needle: str) -> bool:
    return needle in words and any(glued(w, needle) for w in words.split() if needle in w)


# Another company's name on a page that is not about it: "Sign in with Google", "Continue with Apple", "Facebook ile
# giriş yap" (a login button), "Map data ©2026 Google" (an embedded map, trippd.com 2026-11-18), "protected by
# reCAPTCHA and the Google Privacy Policy" (a form widget), "Powered by ...". Taken out before looking for a claim.
# A copied "© 2026 PayPal" footer stays: that is the page presenting itself as the brand.
_WIDGETS = re.compile(r"""map data\s*(?:©|\(c\))?[^\n|]{0,40}|google\s+maps|keyboard\s+shortcuts|"""
                      r"""protected\s+by\s+recaptcha[^\n.]*|recaptcha|powered\s+by\s+[\w.]+""", re.I)
_SOCIAL_LOGIN = re.compile(
    r"""(?:\b(?:sign|log)[\s-]*(?:in|on|up)\s+(?:with|using|via)\s+[\w.]+(?:\s+account)?|\bcontinue\s+with\s+[\w.]+"""
    r"""|\b[\w.]+\s+(?:hesab\w*\s+)?ile\s+(?:giri\w*|devam\w*|oturum\w*|ba\w*lan\w*|kaydol\w*|kay\w*t\s+ol\w*|"""
    r"""\w*ye\s+ol\w*))""", re.I)


def _no_social_login(text: str | None) -> str | None:
    return _WIDGETS.sub(" ", _SOCIAL_LOGIN.sub(" ", text)) if text else text


def claimed_brand(page: Page) -> Claim | None:
    """The watched brand a page presents itself as, or None (see the module docstring for the order)."""
    title, description = _no_social_login(page.title), _no_social_login(page.description)
    fits = lambda b: not _group_abroad(page, b)  # noqa: E731 - Vodafone CZ is not Vodafone TR
    in_title = brands_named(title)
    # a social platform counts from the title (or the host), not from a description that mentions it ("RuralZap:
    # chats on WhatsApp"): there it is an integration
    top = [b for b in brands_named(" ".join(x for x in (title, description) if x))
           if fits(b) and (b.sector != "social" or b in in_title)]
    if top:
        return _claim(top[0], "page")
    for watchlist in _watchlists():
        hit = watchlist.match(page.host)
        if hit is not None and hit.matched in hit.brand.substrings and fits(hit.brand) \
                and _glued_in_host(page, hit.matched) and not _own_name(page, hit.matched):
            # short tokens (ptt, hgs, ups) are too ambiguous in a host name
            return Claim(hit.brand.name, hit.brand.official, "host", _only_in_subdomain(hit))
    body = brands_named(_no_social_login((page.text or "")[:1500]))
    if len(body) == 1 and fits(body[0]) and body[0].sector != "social":
        return _claim(body[0], "page")
    return None  # none, or several named only in the text: a list (payment methods, partners), not a claim


def _glued_in_host(page: Page, matched: str) -> bool:
    """Whether the brand string stands in a host word as the brand: at the word's start ("ziraatbankgiris"), after
    phishing affixes ("myenpara") or after letters of no meaning ("acikdenizbankasyvvyi"), but not after letters the
    page's own name explains: chickENPARAdise is "Chicken Paradise", primISBANK is "Primis". A string spread over
    several words ("is-bank") is left to the other checks."""
    from integrations.brand_watch import skeleton

    needle = skeleton(matched).replace("-", "")
    words = [w for w in re.split(r"[^a-z0-9]+", skeleton(page.host)) if needle in w]
    if not words:
        return True
    names = _words(" ".join(x for x in (page.title, page.description) if x)).split()
    runs = ["".join(names[i:]) for i in range(len(names))]  # "Alicia Peña Bizama" explains "aliciapenabizama"
    for w in words:
        prefix = w[: w.find(needle)]
        if _affixes(prefix) or len(prefix) < 2 or not any(r.startswith(prefix) for r in runs):
            return True
    return False


def _only_in_subdomain(hit) -> bool:
    from integrations.brand_watch import skeleton

    label = hit.registrable.split(".", 1)[0]
    return skeleton(hit.matched).replace("-", "") not in skeleton(label).replace("-", "")


# Country codes used as generic names (.co, .io, .me ...): a page there says nothing about a country.
_GENERIC_CC = frozenset({"co", "io", "me", "tv", "cc", "ws", "ai", "ly", "to", "gg", "fm", "am", "la", "sh", "so",
                         "st", "nu", "vc", "pw", "bz"})


def _group_abroad(page: Page, brand) -> bool:
    """A page of the international group's company in another country: the brand is a group's Turkish company,
    the page is not Turkish and its host is under another country's domain (vodafone.al, partner.vodafone.cz)."""
    if not getattr(brand, "group", False) or turkish(page):
        return False
    cc = page.host.rsplit(".", 1)[-1]
    return len(cc) == 2 and cc != "tr" and cc not in _GENERIC_CC


def _claim(brand, via: str) -> Claim:
    return Claim(brand.name, brand.official, via)


def _own_name(page: Page, matched: str) -> bool:
    """Whether the host word holding the brand string is the page's own, different name: "fraisbank" in
    fraisbank.fr, and the page calls itself Fraisbank. A word that starts with the brand string (ziraatbank,
    garantibbvamobil) is never a different name."""
    from integrations.brand_watch import skeleton

    needle = skeleton(matched).replace("-", "")
    words = [w for w in re.split(r"[^a-z0-9]+", skeleton(page.host)) if needle in w and not w.startswith(needle)]
    if not words:
        return False
    text = _words(" ".join(x for x in (page.title, page.description, (page.text or "")[:1500]) if x))
    return any(f" {w} " in text for w in words)


def listed_brands(page: Page) -> list[str]:
    body = brands_named(_no_social_login((page.text or "")[:1500]))
    return [b.name for b in body] if len(body) > 1 else []


def _fold(text: str) -> str:
    """Lower case, Turkish dotless i and accents folded (ı→i, ş→s, î→i), anything else a space."""
    import unicodedata

    t = unicodedata.normalize("NFKD", text.translate(_FOLD).replace("İ", "i").lower())
    return " " + re.sub(r"[^a-z0-9]+", " ", "".join(c for c in t if not unicodedata.combining(c))) + " "


def invest_terms(page: Page) -> list[str]:
    """The investment terms a page uses in its title, description or first lines (distinct)."""
    words = _fold(" ".join(x for x in (page.title, page.description, (page.text or "")[:3000]) if x))
    return [t for t in _INVEST if (f" {t} " if t in _INVEST_EXACT else f" {t}") in words]


def turkish(page: Page) -> bool:
    """Whether the page speaks to Turks: its declared language, a .tr host, or Turkish letters / words in its text."""
    if (page.language or "").lower().startswith("tr") or page.host.endswith(".tr"):
        return True
    head = " ".join(x for x in (page.title, page.description, (page.text or "")[:3000]) if x)
    if sum(c in _TR_LETTERS for c in head) >= 2:
        return True
    return bool(_TR_WORDS & set(re.findall(r"[a-z]+", head.translate(_FOLD).lower())))


def popular(session: Session, host: str) -> str | None:
    """The registrable domain of ``host`` when a popularity list ranks it (an established site, e.g. vodafone.co.uk
    for login-qc5.dx-idm.vodafone.co.uk), else None. Shared hosting (x.github.io) is its own registrable domain."""
    from common.domains import DomainError, normalize_domain
    from database.models import DomainSourceRecord

    try:
        registrable = normalize_domain(host).registrable
    except DomainError:
        return None
    ranked = session.scalar(
        select(Domain.id).join(DomainSourceRecord, DomainSourceRecord.domain_id == Domain.id)
        .join(DataSource, DataSource.id == DomainSourceRecord.source_id)
        .where(Domain.normalized_domain == registrable, DataSource.slug.in_(POPULAR_SOURCES),
               DomainSourceRecord.is_listed).limit(1))
    return registrable if ranked is not None else None


def fresh_cert(page: Page) -> bool:
    if not page.cert_valid_from:
        return False
    issued = datetime.fromtimestamp(page.cert_valid_from, UTC)
    return timedelta(0) <= page.scanned_at - issued <= timedelta(days=FRESH_CERT_DAYS)


def asks(page: Page) -> bool:
    """Whether the page asks for anything the rules look at (a secret or a phone number)."""
    return bool(secrets(page.html)) or phone_form(page.html)


def judge(page: Page, *, suspicious_name: str | None, protected: str | None,
          popular: str | None = None) -> tuple[bool, dict]:
    """(positive, signals). ``suspicious_name``: why the host itself is suspect (a look-alike or list reason), if
    any. ``protected``: the popular / official / restricted owner the host belongs to, if any. ``popular``: the
    host's registrable domain when a popularity list ranks it. ``signals["kind"]``: which rule fired."""
    found = secrets(page.html)
    phone = phone_form(page.html)
    terms = invest_terms(page) if phone else []
    claim = claimed_brand(page)
    official = bool(claim) and any(page.host == o or page.host.endswith("." + o) for o in claim.official)
    fresh = fresh_cert(page)
    listed = bool(suspicious_name and suspicious_name.startswith("feed_listed:"))
    rest = claim is not None and not official and protected is None and popular is None \
        and (suspicious_name is not None or fresh) \
        and not (claim.subdomain and not (listed or fresh))  # a customer's space at a service, by its name alone
    kind = None
    if rest and found:
        kind = PHISHING
    elif rest and phone and len(terms) >= 2:
        kind = INVESTMENT_SCAM
    signals = {"kind": kind, "secrets": found, "phone": phone, "invest_terms": terms,
               "brand": claim.brand if claim else None, "brand_via": claim.via if claim else None,
               "listed_brands": listed_brands(page) if claim is None else [], "turkish": turkish(page),
               "official": official, "protected": protected, "popular": popular, "suspicious_name": suspicious_name,
               "fresh_cert": fresh}
    return kind is not None, signals


def page_of(session: Session, domain: Domain, scan: ScanResult, data) -> Page:
    tls = scan.tls or {}
    valid_from = tls.get("valid_from")
    return Page(host=domain.normalized_domain, title=data.title, description=data.meta_description, text=data.text,
                html=data.html, cert_valid_from=float(valid_from) if isinstance(valid_from, int | float) else None,
                scanned_at=scan.created_at or datetime.now(UTC), language=data.language_hint)


def verdict_for(session: Session, domain: Domain, scan: ScanResult, data) -> tuple[str | None, bool, dict] | None:
    """(why the host is suspect, positive, signals) for a scanned page; None when there is nothing to judge
    (nothing asked for and nothing suspect: the common case) or nothing to keep (a negative on a host that is not
    a threat candidate)."""
    from classification.engine import lookalike_reason

    from .guard import protected_owner

    suspicious = lookalike_reason(session, domain)
    page = page_of(session, domain, scan, data)
    if suspicious is None and not asks(page):
        return None
    name = domain.normalized_domain
    positive, signals = judge(page, suspicious_name=suspicious, protected=protected_owner(session, name),
                              popular=popular(session, name))
    if not positive and suspicious is None:
        return None
    return suspicious, positive, signals


# ----------------------------------------------------------------------------- measurement


def measure(session: Session, rule: str = RULE) -> dict:
    """The rule against people's decisions, one verdict per domain (its newest). A person decided when they closed
    a threat-candidate review of the domain after the verdict, or flagged it (reviewer record) at any time. Our own
    detector flags a person withdrew count as wrong."""
    newest = (select(func.max(DetectorVerdict.id)).where(DetectorVerdict.rule == rule)
              .group_by(DetectorVerdict.domain_id))
    return tally(session, list(session.scalars(select(DetectorVerdict).where(DetectorVerdict.id.in_(newest)))), rule)


def tally(session: Session, verdicts: list, rule: str) -> dict:
    """The confusion table of ``verdicts`` (anything with domain_id, positive, created_at, action) against people."""
    from classification.engine import REVIEW_SOURCE, threat_candidate

    ids = {v.domain_id for v in verdicts}
    if not ids:
        return {"rule": rule, "verdicts": 0, "positive": 0, "tp": 0, "fp": 0, "fn": 0, "tn": 0, "pending": 0,
                "precision": None, "recall": None, "listed": 0, "flagged": 0, "withdrawn": 0}
    slugs = dict(session.execute(select(DataSource.slug, DataSource.id)
                                 .where(DataSource.slug.in_([REVIEW_SOURCE, SOURCE]))).all())
    person = set(session.scalars(select(ThreatIntelligence.domain_id).where(
        ThreatIntelligence.domain_id.in_(ids), ThreatIntelligence.source_id == slugs.get(REVIEW_SOURCE),
        ThreatIntelligence.url_id.is_(None))))
    withdrawn = set(session.scalars(select(ThreatIntelligence.domain_id).where(
        ThreatIntelligence.domain_id.in_(ids), ThreatIntelligence.source_id == slugs.get(SOURCE),
        ~ThreatIntelligence.is_active, ThreatIntelligence.deactivated_at.is_not(None),
        or_(ThreatIntelligence.raw.is_(None), ~ThreatIntelligence.raw.has_key("expired_at")))))  # taken back by a
    # person, not expired with its dead site (threats/lifecycle.py)
    decided_at: dict[int, datetime] = dict(session.execute(
        select(ReviewTask.domain_id, func.max(ReviewTask.decided_at))
        .where(ReviewTask.domain_id.in_(ids), ReviewTask.status == ReviewStatus.DONE, threat_candidate())
        .group_by(ReviewTask.domain_id)).all())
    hosts = dict(session.execute(select(Domain.id, Domain.normalized_domain).where(Domain.id.in_(ids))).all())
    listed = set(session.scalars(select(ThreatFeedEntry.host).where(
        ThreatFeedEntry.host.in_(list(hosts.values())), ThreatFeedEntry.is_listed)))
    c = dict(tp=0, fp=0, fn=0, tn=0, pending=0, listed=0, flagged=0)
    for v in verdicts:
        outcome = _outcome(v, person, withdrawn, decided_at)
        c["flagged"] += v.action == "flagged"
        if v.positive:
            c["listed"] += hosts.get(v.domain_id) in listed
        c[outcome] += 1
    judged = c["tp"] + c["fp"]
    return {"rule": rule, "verdicts": len(verdicts), "positive": sum(v.positive for v in verdicts), **c,
            "precision": round(c["tp"] / judged, 4) if judged else None,
            "recall": round(c["tp"] / (c["tp"] + c["fn"]), 4) if c["tp"] + c["fn"] else None,
            "withdrawn": len(withdrawn)}


def _outcome(v, person: set[int], withdrawn: set[int], decided_at: dict[int, datetime]) -> str:
    good = v.domain_id in person
    bad = v.domain_id in withdrawn
    decided = good or bad or (decided_at.get(v.domain_id) is not None and decided_at[v.domain_id] >= v.created_at)
    if not decided:
        return "pending"
    if v.positive:
        return "tp" if good and not bad else "fp"
    return "fn" if good else "tn"


def qualified(stats: dict, settings: Settings) -> bool:
    judged = stats["tp"] + stats["fp"]
    return judged >= settings.threat_detector_min_decisions and stats["precision"] is not None \
        and stats["precision"] >= settings.threat_detector_min_precision


def flagged_today(session: Session, settings: Settings, now: datetime | None = None) -> int:
    start = local_midnight(now or datetime.now(UTC), settings.app_utc_offset_hours)
    return session.scalar(select(func.count()).select_from(DetectorVerdict).where(
        DetectorVerdict.action == "flagged", DetectorVerdict.created_at >= start)) or 0


# ----------------------------------------------------------------------------- the check after a scan


def check(session: Session, domain: Domain, scan: ScanResult, data, settings: Settings) -> DetectorVerdict | None:
    """Judge a scanned page (a domain's home page, or one page a list named: ``scan.url_id``). Stores the verdict
    when it is positive or the domain is a threat candidate (the negatives there are what recall is measured on);
    flags only in phase 3, within its limits."""
    judged = verdict_for(session, domain, scan, data)
    if judged is None:
        return None
    _, positive, signals = judged
    verdict = DetectorVerdict(domain_id=domain.id, scan_result_id=scan.id, url_id=scan.url_id, rule=RULE,
                              positive=positive, signals=signals)
    session.add(verdict)
    session.flush()
    if positive and settings.threat_detector_auto:
        _flag(session, domain, verdict, settings)
    return verdict


def _flag(session: Session, domain: Domain, verdict: DetectorVerdict, settings: Settings) -> None:
    from classification.engine import is_threat_candidate, record_threats
    from database.models import Url

    from .records import record_url_threats

    if flagged_today(session, settings) >= settings.threat_detector_max_per_day:
        return
    stats = measure(session)
    if not qualified(stats, settings):
        return
    threats = [THREATS[(verdict.signals or {}).get("kind") or PHISHING]]
    if verdict.url_id is not None:  # one page a list named: only that page (it may be a hacked, honest site)
        url = session.get(Url, verdict.url_id)
        record_url_threats(session, url.url, threats, source_slug=SOURCE, reference=f"detector:{RULE}")
    else:
        if any(w.match(domain.normalized_domain) for w in _watchlists()):
            threats.append("lookalike-domain")
        record_threats(session, domain, threats, source_slug=SOURCE, reference=f"detector:{RULE}")
    session.flush()
    verdict.action = "flagged"
    if (stats["flagged"] + 1) % settings.threat_detector_sample_every == 0:
        return  # this one stays open for a person: the measurement goes on
    for task in session.scalars(select(ReviewTask).where(ReviewTask.domain_id == domain.id,
                                                         ReviewTask.status == ReviewStatus.OPEN)):
        if is_threat_candidate(task.reason):
            task.status, task.decided_at = ReviewStatus.SKIPPED, datetime.now(UTC)
            task.decision_note = f"detector: flagged by {RULE} (precision {stats['precision']:.1%})"
    log.info("detector flagged a domain", extra={"domain": domain.normalized_domain, "threats": threats})


# ----------------------------------------------------------------------------- replaying a rule change


def replay(session: Session, store, from_rule: str = "page_proven_v1") -> dict:
    """Judge again, with the current rule, every page an earlier rule judged (its stored scan), and measure both
    against the same decisions of people. Read-only: nothing is stored, nothing flagged."""
    from types import SimpleNamespace

    from classification.engine import build_input

    newest = (select(func.max(DetectorVerdict.id)).where(DetectorVerdict.rule == from_rule)
              .group_by(DetectorVerdict.domain_id))
    old = list(session.scalars(select(DetectorVerdict).where(DetectorVerdict.id.in_(newest))))
    new, changed, asking = [], [], []
    for v in old:
        domain = session.get(Domain, v.domain_id)
        scan = session.get(ScanResult, v.scan_result_id) if v.scan_result_id else None
        if domain is None or scan is None:
            continue
        judged = verdict_for(session, domain, scan, build_input(session, scan, store, use_screenshot=False))
        positive, signals = (judged[1], judged[2]) if judged else (False, {})
        new.append(SimpleNamespace(domain_id=v.domain_id, positive=positive, created_at=v.created_at, action=None))
        if not positive and signals.get("secrets"):
            asking.append({"domain": domain.normalized_domain, "signals": signals, "row": new[-1]})
        if positive != v.positive:
            changed.append({"domain": domain.normalized_domain, "was": v.positive, "now": positive,
                            "signals": signals, "domain_id": v.domain_id, "created_at": v.created_at})
    before, after = tally(session, old, from_rule), tally(session, new, RULE)
    if changed:  # what people decided on each changed page
        rows = [SimpleNamespace(domain_id=c["domain_id"], positive=c["now"], created_at=c["created_at"], action=None)
                for c in changed]
        for c, row in zip(changed, rows, strict=True):
            c["people"] = {"tp": "phishing", "fp": "not phishing", "fn": "phishing", "tn": "not phishing",
                           "pending": "undecided"}[_outcome_of(session, row)]
    # pages that ask for a secret, a person called phishing, and the rule still lets pass: what to look at next
    missed = [{"domain": a["domain"], "signals": a["signals"]} for a in asking if _outcome_of(session, a["row"]) == "fn"]
    return {"before": before, "after": after, "changed": changed, "missed": missed}


def _outcome_of(session: Session, v) -> str:
    one = tally(session, [v], RULE)
    return next(k for k in ("tp", "fp", "fn", "tn", "pending") if one[k])


def main(argv: list[str] | None = None) -> int:
    import argparse

    from common.config import get_settings
    from crawler.storage import S3Store
    from database.session import session_factory

    parser = argparse.ArgumentParser(prog="python -m threats.detector", description="Our page-proof threat detector")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("replay", help="judge an earlier rule's pages again with the current rule (read-only)")
    p.add_argument("--from-rule", default="page_proven_v1")
    args = parser.parse_args(argv)

    settings = get_settings()
    with session_factory()() as session:
        try:
            out = replay(session, S3Store(settings), args.from_rule)
        finally:
            session.rollback()  # read-only by design; nothing a check might have touched is kept
    keys = ("verdicts", "positive", "tp", "fp", "fn", "tn", "pending", "precision", "recall")
    for name in ("before", "after"):
        print(f"{out[name]['rule']:>16}: " + "  ".join(f"{k} {out[name][k]}" for k in keys))
    print(f"\nchanged verdicts: {len(out['changed'])}")
    for c in out["changed"]:
        print(f"  {c['domain']}: {'positive' if c['was'] else 'negative'} -> {'positive' if c['now'] else 'negative'}"
              f"  (people: {c['people']})  {_why(c['signals'])}")
    print(f"\nasks for a secret, people say phishing, still negative: {len(out['missed'])}")
    for m in out["missed"]:
        print(f"  {m['domain']}  {_why(m['signals'])}")
    return 0


def _why(s: dict) -> dict:
    why = {k: s.get(k) for k in ("secrets", "brand", "brand_via", "listed_brands", "popular", "protected", "official",
                                 "suspicious_name", "fresh_cert") if s.get(k) not in (None, [], False)}
    if s:
        why["turkish"] = s.get("turkish")
    return why


if __name__ == "__main__":
    raise SystemExit(main())
