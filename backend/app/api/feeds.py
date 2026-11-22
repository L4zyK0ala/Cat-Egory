"""Feed download endpoints. Files are pre-built (``category.feeds.build``) and served from the object store."""
from __future__ import annotations

from datetime import UTC
from email.utils import format_datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.config import get_settings
from database.models import FeedRelease
from database.session import get_session
from feeds.builder import FEED_NAME_RE, MEDIA_TYPES

from ..demand import record_feed
from ..deps import Principal, get_principal, get_redis, rate_limit
from ..errors import ApiError

router = APIRouter(prefix="/api/v1/feeds", tags=["feeds"], dependencies=[Depends(rate_limit("read"))])
ALIASES = {"rpz": "security.rpz", "all-domains.csv": "all.csv", "all-domains.json": "all.json"}


def get_feed_store():
    from crawler.storage import S3Store

    return S3Store(get_settings())


class FeedOut(BaseModel):
    name: str
    url: str
    version: str
    records: int
    sha256: str
    license: str
    taxonomy_version: str
    generated_at: str


def _latest(session: Session):
    ids = select(func.max(FeedRelease.id)).group_by(FeedRelease.feed)
    return session.scalars(select(FeedRelease).where(FeedRelease.id.in_(ids)).order_by(FeedRelease.feed))


@router.get("", response_model=list[FeedOut], summary="Index of available feeds (latest versions)")
def index(response: Response, session: Session = Depends(get_session)) -> list[FeedOut]:
    response.headers["Cache-Control"] = "public, max-age=300"
    return [FeedOut(name=r.feed, url=f"/api/v1/feeds/{r.feed}", version=r.version, records=r.record_count,
                    sha256=r.sha256, license=r.license, taxonomy_version=r.taxonomy_version,
                    generated_at=r.generated_at.isoformat()) for r in _latest(session)]


@router.get("/{name}", summary="Download a feed (txt, hosts, rpz, csv, json). `rpz` = all security threats.",
            responses={200: {"content": {"text/plain": {}, "text/csv": {}, "application/json": {}}}})
def download(name: str, request: Request, session: Session = Depends(get_session),
             store=Depends(get_feed_store), principal: Principal = Depends(get_principal),
             client=Depends(get_redis)) -> Response:
    name = ALIASES.get(name, name)
    if not FEED_NAME_RE.match(name):
        raise ApiError(404, "not_found", "unknown feed name")
    release = session.scalar(select(FeedRelease).where(FeedRelease.feed == name).order_by(FeedRelease.id.desc()).limit(1))
    if release is None:
        raise ApiError(404, "not_found", "feed not built yet (or unknown category)")
    record_feed(client, request, principal, name)  # a real feed only (unknown names are 404 above)
    etag = f'"{release.sha256}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=900", "X-Feed-Version": release.version,
               "Last-Modified": format_datetime(release.generated_at.astimezone(UTC), usegmt=True),
               "Content-Disposition": f'inline; filename="cat-egory-{name}"'}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(store.get(release.object_key), media_type=MEDIA_TYPES[name.rsplit(".", 1)[-1]], headers=headers)


@router.head("/{name}", include_in_schema=False)
def head(name: str, request: Request, session: Session = Depends(get_session)) -> Response:
    """Link checkers and uptime monitors ask with HEAD first: the same headers as a download, no body, not counted."""
    name = ALIASES.get(name, name)
    release = (session.scalar(select(FeedRelease).where(FeedRelease.feed == name).order_by(FeedRelease.id.desc()).limit(1))
               if FEED_NAME_RE.match(name) else None)
    if release is None:
        return Response(status_code=404)
    etag = f'"{release.sha256}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=900", "X-Feed-Version": release.version,
               "Last-Modified": format_datetime(release.generated_at.astimezone(UTC), usegmt=True),
               "Content-Type": MEDIA_TYPES[name.rsplit(".", 1)[-1]]}
    return Response(status_code=304 if request.headers.get("if-none-match") == etag else 200, headers=headers)
