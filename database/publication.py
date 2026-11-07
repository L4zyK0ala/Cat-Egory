"""What may be published.

A domain is *publishable* when at least one source that reported it allows redistribution (manual entry,
community, our own AI/review sources are CC BY 4.0; a private file import is ``internal`` and is not).
Listings, statistics by name and feeds only ever contain publishable domains; an anonymous lookup of a
non-publishable domain answers 404 so private imports cannot be probed.

A classification is *published* when the domain is ``classified`` or ``verified``. ``needs_review`` /
``reanalyze`` domains keep whatever earlier published classification they have.
"""
from __future__ import annotations

from sqlalchemy import and_, exists, select
from sqlalchemy.sql.elements import ColumnElement

from database.models import ClassificationStatus, DataSource, Domain, DomainSourceRecord, SourceLicense

PUBLISHED_STATUSES = (ClassificationStatus.CLASSIFIED, ClassificationStatus.VERIFIED)
OWN_DATA_LICENSE = "CC-BY-4.0"


def publishable() -> ColumnElement[bool]:
    return exists(
        select(DomainSourceRecord.id)
        .join(DataSource, DataSource.id == DomainSourceRecord.source_id)
        .join(SourceLicense, SourceLicense.id == DataSource.license_id)
        .where(DomainSourceRecord.domain_id == Domain.id, SourceLicense.redistribution_allowed)
    )


def published() -> ColumnElement[bool]:
    return and_(Domain.is_active, Domain.classification_status.in_(PUBLISHED_STATUSES), publishable())
