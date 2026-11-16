"""Idempotent reference-data sync: taxonomy → ``categories`` / ``taxonomy_versions`` and the built-in sources.

Run with ``python -m database.seed``. Categories are upserted, never deleted (IDs are permanent).
"""
from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from classification.taxonomy import Taxonomy, default_taxonomy
from common.config import get_settings
from common.logging import configure_logging

from .models import Category, DataSource, SourceKind, SourceLicense, TaxonomyVersion
from .session import session_factory

log = logging.getLogger("category.seed")

# Licences we are certain about because we choose them ourselves. Third-party licences are reviewed per source
# (integrations/sources.yaml) and only inserted once verified.
BUILTIN_LICENSES = [
    {"slug": "CC-BY-4.0", "name": "Creative Commons Attribution 4.0 International",
     "url": "https://creativecommons.org/licenses/by/4.0/", "redistribution_allowed": True,
     "commercial_use_allowed": True, "attribution_required": True, "share_alike": False},
    {"slug": "internal", "name": "Internal / not for redistribution", "url": None, "redistribution_allowed": False,
     "commercial_use_allowed": False, "attribution_required": False, "share_alike": False},
]
BUILTIN_SOURCES = [
    {"slug": "cat-egory-ai", "name": "Cat-egory AI classification", "kind": SourceKind.AI, "license": "CC-BY-4.0",
     "attribution": "Cat-egory contributors"},
    {"slug": "cat-egory-review", "name": "Cat-egory human review", "kind": SourceKind.MANUAL, "license": "CC-BY-4.0",
     "attribution": "Cat-egory contributors"},
    {"slug": "manual", "name": "Manual entry", "kind": SourceKind.MANUAL, "license": "CC-BY-4.0",
     "attribution": "Cat-egory contributors"},
    {"slug": "community", "name": "Community submissions", "kind": SourceKind.COMMUNITY, "license": "CC-BY-4.0",
     "attribution": "Cat-egory community"},
    {"slug": "file-import", "name": "Private file import", "kind": SourceKind.FILE_IMPORT, "license": "internal",
     "attribution": None},
    # Categories that follow from who may register a domain (classification/registry_rules.py). Our own rule set,
    # derived from the registry's published allocation policy; no third-party data is copied.
    {"slug": "cat-egory-registry-rules", "name": "Cat-egory registry allocation rules", "kind": SourceKind.RULE,
     "license": "CC-BY-4.0", "attribution": "Cat-egory contributors"},
    # Host names from public Certificate Transparency logs that look like Turkish brands (integrations/ct_watch.py).
    # Names are facts from public logs; the selection is ours.
    {"slug": "ct-brand-watch", "name": "Certificate Transparency brand watch", "kind": SourceKind.DATASET,
     "license": "CC-BY-4.0", "attribution": "Cat-egory contributors"},
    # Our sister project's own findings, proven on the page (threats/octryx.py). Never another list's verdict.
    {"slug": "octryx", "name": "Octryx brand impersonation findings", "kind": SourceKind.DATASET,
     "license": "CC-BY-4.0", "attribution": "Octryx"},
    # Names several different visitors looked up while we did not know them (backend/app/demand.py). Names only.
    {"slug": "demand", "name": "Visitor demand", "kind": SourceKind.COMMUNITY, "license": "CC-BY-4.0",
     "attribution": "Cat-egory visitors"},
]


def sync_taxonomy(session: Session, taxonomy: Taxonomy) -> int:
    rows = [
        {"id": c.id, "code": c.code, "parent_id": c.parent, "kind": c.kind, "name": c.name,
         "description": c.description, "is_group": c.group, "introduced_in": taxonomy.version,
         "deprecated_in": c.deprecated_in, "replaced_by_id": None}
        for c in taxonomy.categories.values()
    ]
    stmt = insert(Category).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Category.id],
        set_={k: stmt.excluded[k] for k in ("parent_id", "kind", "name", "description", "is_group", "deprecated_in")},
    )
    session.execute(stmt)
    for c in taxonomy.categories.values():  # second pass: replacements may point forward
        if c.replaced_by:
            session.execute(update(Category).where(Category.id == c.id).values(replaced_by_id=c.replaced_by))

    if session.scalar(select(TaxonomyVersion).where(TaxonomyVersion.version == taxonomy.version)) is None:
        session.add(TaxonomyVersion(version=taxonomy.version, notes=f"released {taxonomy.released}"))
        session.flush()
    session.execute(update(TaxonomyVersion).values(is_current=False))
    session.execute(update(TaxonomyVersion).where(TaxonomyVersion.version == taxonomy.version).values(is_current=True))
    return len(rows)


def sync_builtin_sources(session: Session) -> None:
    for lic in BUILTIN_LICENSES:
        session.execute(insert(SourceLicense).values(**lic).on_conflict_do_nothing(index_elements=[SourceLicense.slug]))
    licenses = {lic.slug: lic.id for lic in session.scalars(select(SourceLicense))}
    for src in BUILTIN_SOURCES:
        values = {k: v for k, v in src.items() if k != "license"} | {"license_id": licenses[src["license"]],
                                                                     "enabled": True}
        session.execute(insert(DataSource).values(**values).on_conflict_do_nothing(index_elements=[DataSource.slug]))


def main() -> None:
    configure_logging(get_settings().log_level)
    taxonomy = default_taxonomy()
    with session_factory()() as session, session.begin():
        count = sync_taxonomy(session, taxonomy)
        sync_builtin_sources(session)
    log.info("seed complete", extra={"taxonomy_version": taxonomy.version, "categories": count})


if __name__ == "__main__":
    main()
