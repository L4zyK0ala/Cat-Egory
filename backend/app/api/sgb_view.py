"""How the national SGB/USOM list's answer is shown to reviewers (admin API only; never public)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import ExternalListing
from threats import sgb


class SgbMatchOut(BaseModel):
    id: int | str | None
    value: str
    type: str
    category: str | None
    category_name: str | None
    criticality: int | None = Field(description="1 = most critical, 10 = least")
    date: str | None
    scope: str = Field(description="exact domain, a subdomain, or a URL on the domain")
    link: str | None


class SgbOut(BaseModel):
    """National malicious-address list (SGB/USOM), last check. Internal: never published."""
    listed: bool
    checked_at: datetime
    matches: list[SgbMatchOut]


def sgb_out(row: ExternalListing | None) -> SgbOut | None:
    if row is None:
        return None
    return SgbOut(listed=row.listed, checked_at=row.checked_at, matches=[
        SgbMatchOut(id=m.get("id"), value=m.get("value", ""), type=m.get("type", ""), category=m.get("category"),
                    category_name=sgb.CATEGORIES.get(m.get("category") or ""), criticality=m.get("criticality"),
                    date=m.get("date"), scope=m.get("scope", "exact"),
                    link=sgb.DETAIL.format(id=m["id"]) if m.get("id") else None)
        for m in row.matches or []])


def sgb_listings(session: Session, domain_ids: set[int]) -> dict[int, ExternalListing]:
    if not domain_ids:
        return {}
    return {r.domain_id: r for r in session.scalars(select(ExternalListing).where(
        ExternalListing.source == sgb.SOURCE, ExternalListing.domain_id.in_(domain_ids)))}
