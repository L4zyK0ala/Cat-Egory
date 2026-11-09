"""How VirusTotal's answer is shown to reviewers (admin API only; never public)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.models import ExternalListing
from threats import listings, virustotal


class VtEngineOut(BaseModel):
    name: str
    category: str
    result: str


class VtOut(BaseModel):
    """VirusTotal's last answer. Internal: never published, and never a threat flag by itself."""
    known: bool = Field(description="false: VirusTotal has no analysis of this domain")
    flagged: bool = Field(description="at least one vendor says malicious or suspicious")
    malicious: int
    suspicious: int
    total: int
    last_analysis: str | None
    engines: list[VtEngineOut]
    checked_at: datetime
    link: str


def vt_out(row: ExternalListing | None, domain: str) -> VtOut | None:
    if row is None:
        return None
    m = (row.matches or [{}])[0]
    return VtOut(known=bool(row.matches), flagged=row.listed, malicious=m.get("malicious", 0),
                 suspicious=m.get("suspicious", 0), total=m.get("total", 0), last_analysis=m.get("last_analysis"),
                 engines=[VtEngineOut(**e) for e in m.get("engines", [])], checked_at=row.checked_at,
                 link=virustotal.GUI.format(domain=domain))


def vt_listings(session: Session, domain_ids: set[int]) -> dict[int, ExternalListing]:
    return listings.latest(session, virustotal.SOURCE, domain_ids)
