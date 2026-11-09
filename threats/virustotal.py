"""Second opinion for reviewers from VirusTotal: how many security vendors flag a threat domain.

API v3, ``GET /domains/{domain}`` with the operator's own key: a lookup of what VirusTotal already knows. **Nothing
is ever submitted for scanning, and no URL is sent**: submissions are shared with VirusTotal's community, and
phishing URLs can carry a victim's e-mail address.

**Internal use only, off without a key.** The public API is limited to 500 requests a day and 4 a minute and must
not be used in commercial products or services (docs.virustotal.com/reference/public-vs-premium-api). The operator
decides whether the terms fit their use. Results go to ``external_listings`` for reviewers: never on the public
site, in the public API or in feeds, and they never create a threat flag.

Reading the number: 0 detections does not mean safe. Most look-alikes come from new certificates, and vendors have
often not seen such a domain yet. One vendor alone can be a false alarm.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from . import listings

SOURCE = "virustotal"
API = "https://www.virustotal.com/api/v3/domains/{domain}"
GUI = "https://www.virustotal.com/gui/domain/{domain}"
USER_AGENT = "Cat-Egory threat cross-check (internal; https://github.com/L4zyK0ala/Cat-Egory)"
MAX_ENGINES = 20
FLAGGING = ("malicious", "suspicious")


def summary(payload: dict) -> dict | None:
    """The counts and the vendors that flag the domain. None if VirusTotal has never analysed it."""
    attrs = (payload.get("data") or {}).get("attributes") or {}
    stats = attrs.get("last_analysis_stats")
    if not stats:
        return None
    counts = {k: int(stats.get(k) or 0) for k in ("malicious", "suspicious", "harmless", "undetected")}
    flagged = [{"name": str(r.get("engine_name") or name)[:100], "category": r.get("category"),
                "result": str(r.get("result") or "")[:100]}
               for name, r in (attrs.get("last_analysis_results") or {}).items() if r.get("category") in FLAGGING]
    flagged.sort(key=lambda e: (FLAGGING.index(e["category"]), e["name"]))
    when = attrs.get("last_analysis_date")
    return {**counts, "total": sum(counts.values()), "engines": flagged[:MAX_ENGINES],
            "last_analysis": datetime.fromtimestamp(int(when), UTC).isoformat() if when else None}


def is_listed(matches: list[dict]) -> bool:
    return any(m["malicious"] + m["suspicious"] > 0 for m in matches)


def http_fetch(client: httpx.Client) -> listings.Fetch:
    def fetch(domain: str) -> list[dict]:
        r = client.get(API.format(domain=quote(domain, safe="")))
        if r.status_code == 404:  # VirusTotal does not know this domain
            return []
        r.raise_for_status()
        result = summary(r.json())
        return [result] if result else []
    return fetch


def client(api_key: str) -> httpx.Client:
    return httpx.Client(timeout=30, follow_redirects=False,
                        headers={"x-apikey": api_key, "User-Agent": USER_AGENT, "Accept": "application/json"})


def check(session: Session, fetch: listings.Fetch, *, limit: int, max_age_hours: int = 72, pause: float = 16.0,
          **kw: Callable | bool) -> dict:
    return listings.check(session, SOURCE, fetch, is_listed=is_listed, limit=limit, max_age_hours=max_age_hours,
                          pause=pause, **kw)
