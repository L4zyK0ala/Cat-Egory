"""Versioned, hierarchical category taxonomy loaded from ``taxonomy.yaml``."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).with_name("taxonomy.yaml")
KINDS = ("content", "risk")


class TaxonomyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CategoryDef:
    id: str
    code: int
    kind: str
    name: str
    description: str
    parent: str | None = None
    group: bool = False
    deprecated_in: str | None = None
    replaced_by: str | None = None

    @property
    def assignable(self) -> bool:
        return not self.group and self.deprecated_in is None


@dataclass(frozen=True)
class Taxonomy:
    version: str
    released: str
    license: str
    categories: dict[str, CategoryDef] = field(repr=False)

    def get(self, category_id: str) -> CategoryDef | None:
        return self.categories.get(category_id)

    def assignable_ids(self, kind: str) -> list[str]:
        return [c.id for c in self.categories.values() if c.kind == kind and c.assignable]

    def children(self, parent_id: str) -> list[CategoryDef]:
        return [c for c in self.categories.values() if c.parent == parent_id]

    def prompt_listing(self, kind: str) -> str:
        """Compact ``id: name — description`` list for model prompts, grouped by parent."""
        lines: list[str] = []
        for group in (c for c in self.categories.values() if c.group and c.kind == kind):
            lines.append(f"# {group.name}")
            lines.extend(f"- {c.id}: {c.description}" for c in self.children(group.id) if c.assignable)
        return "\n".join(lines)


def _validate(raw: dict) -> Taxonomy:
    for key in ("version", "released", "categories"):
        if key not in raw:
            raise TaxonomyError(f"taxonomy is missing '{key}'")
    cats: dict[str, CategoryDef] = {}
    codes: set[int] = set()
    for item in raw["categories"]:
        cat = CategoryDef(
            id=item["id"], code=int(item["code"]), kind=item["kind"], name=item["name"],
            description=item["description"], parent=item.get("parent"), group=bool(item.get("group", False)),
            deprecated_in=item.get("deprecated_in"), replaced_by=item.get("replaced_by"),
        )
        if cat.id in cats:
            raise TaxonomyError(f"duplicate category id: {cat.id}")
        if cat.code in codes:
            raise TaxonomyError(f"duplicate category code: {cat.code}")
        if cat.kind not in KINDS:
            raise TaxonomyError(f"{cat.id}: unknown kind {cat.kind!r}")
        cats[cat.id] = cat
        codes.add(cat.code)
    for cat in cats.values():
        if cat.parent is not None:
            parent = cats.get(cat.parent)
            if parent is None:
                raise TaxonomyError(f"{cat.id}: unknown parent {cat.parent!r}")
            if parent.kind != cat.kind:
                raise TaxonomyError(f"{cat.id}: kind differs from parent {parent.id}")
            if not parent.group:
                raise TaxonomyError(f"{cat.id}: parent {parent.id} is not a group")
        elif not cat.group:
            raise TaxonomyError(f"{cat.id}: leaf category without parent")
        if cat.replaced_by is not None and cat.replaced_by not in cats:
            raise TaxonomyError(f"{cat.id}: unknown replacement {cat.replaced_by!r}")
    return Taxonomy(version=str(raw["version"]), released=str(raw["released"]),
                    license=str(raw.get("license", "CC-BY-4.0")), categories=cats)


def load_taxonomy(path: str | Path | None = None) -> Taxonomy:
    with open(path or DEFAULT_PATH, encoding="utf-8") as fh:
        return _validate(yaml.safe_load(fh))


@lru_cache
def default_taxonomy() -> Taxonomy:
    return load_taxonomy()
