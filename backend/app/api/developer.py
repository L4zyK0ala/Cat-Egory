"""Self-service developer keys: anyone can take a read-only key with its own limits, without an account.

No personal data is asked: a key belongs to one system account (``DEVELOPER_ACCOUNT_EMAIL``), carries an optional
label, has no scopes (nothing beyond the public API) and expires after ``developer_key_days``. The key is shown
once; only its hash is stored. Abuse limits: ``developer_keys_per_client_per_day`` per client (keyed IP hash) and
``developer_keys_per_day`` overall. Usage counts against ``developer_key_per_minute`` and a daily quota (deps.py).
A key holder can see its own usage and revoke it with the key itself.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import redis
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.config import get_settings
from database.models import ApiKey, Role, User
from database.session import get_session

from .. import audit
from ..deps import Principal, daily_usage, get_principal, get_redis, rate_limit
from ..errors import ApiError
from ..security import DEVELOPER_ACCOUNT_EMAIL, generate_api_key

router = APIRouter(prefix="/api/v1/developer", tags=["developer"])
LABEL_PREFIX = "developer: "


class KeyIn(BaseModel):
    label: str = Field(default="", max_length=60, description="optional, to tell your keys apart")
    accept_terms: bool = Field(description="the data is CC BY 4.0: credit 'Cat-egory contributors'")


class KeyOut(BaseModel):
    key: str = Field(description="send as 'Authorization: Bearer <key>'; shown only once")
    prefix: str
    label: str
    per_minute: int
    per_day: int
    expires_at: datetime


class KeyInfo(BaseModel):
    prefix: str
    label: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    used_today: int
    per_minute: int
    per_day: int


def developer_account(session: Session) -> User:
    user = session.scalar(select(User).where(User.email == DEVELOPER_ACCOUNT_EMAIL))
    if user is None:
        user = User(email=DEVELOPER_ACCOUNT_EMAIL, display_name="Developer keys", role=Role.CONTRIBUTOR,
                    password_hash=None, is_active=True)
        session.add(user)
        session.flush()
    return user


def _clean_label(raw: str) -> str:
    return "".join(c for c in raw if c.isprintable()).strip()[:60]


@router.post("/keys", response_model=KeyOut, status_code=201, dependencies=[Depends(rate_limit("write"))],
             summary="Take a read-only developer key (no account, shown once)")
def create_key(body: KeyIn, request: Request, principal: Principal = Depends(get_principal),
               client: redis.Redis = Depends(get_redis), session: Session = Depends(get_session)) -> KeyOut:
    settings = get_settings()
    if not body.accept_terms:
        raise ApiError(422, "terms_not_accepted", "accept the data licence terms to take a key")
    account = developer_account(session)
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if (session.scalar(select(func.count()).select_from(ApiKey).where(
            ApiKey.user_id == account.id, ApiKey.created_at >= day_start)) or 0) >= settings.developer_keys_per_day:
        raise ApiError(429, "too_many_keys_today", "no more developer keys today; try again tomorrow")
    who = principal.fingerprint or principal.client_ip or "unknown"
    try:
        pipe = client.pipeline()
        pipe.incr(f"devkeys:{who}:{day_start.date().isoformat()}")
        pipe.expire(f"devkeys:{who}:{day_start.date().isoformat()}", 2 * 86400)
        taken = int(pipe.execute()[0])
    except redis.RedisError as exc:
        raise ApiError(503, "unavailable", "keys cannot be issued right now") from exc
    if taken > settings.developer_keys_per_client_per_day:
        raise ApiError(429, "too_many_keys_today", "you took enough keys today; try again tomorrow")

    label = _clean_label(body.label)
    new = generate_api_key()
    expires = datetime.now(UTC) + timedelta(days=settings.developer_key_days)
    key = ApiKey(user_id=account.id, name=f"{LABEL_PREFIX}{label}"[:100], prefix=new.prefix, key_hash=new.key_hash,
                 scopes=[], rate_limit_per_minute=settings.developer_key_per_minute, expires_at=expires)
    session.add(key)
    session.flush()
    audit.record(session, action="developer_key.create", target_type="api_key", target_id=key.id,
                 after={"prefix": new.prefix}, principal=principal, request_id=getattr(request.state, "request_id", None))
    session.commit()
    return KeyOut(key=new.key, prefix=new.prefix, label=label, per_minute=settings.developer_key_per_minute,
                  per_day=settings.developer_key_daily_quota, expires_at=expires)


def _own_key(principal: Principal, session: Session) -> ApiKey:
    if not principal.authenticated:
        raise ApiError(401, "authentication_required", "send your developer key", headers={"WWW-Authenticate": "Bearer"})
    key = session.get(ApiKey, principal.api_key_id)
    if principal.daily_quota is None or key is None:
        raise ApiError(403, "not_a_developer_key", "this endpoint is for self-service developer keys")
    return key


@router.get("/key", response_model=KeyInfo, dependencies=[Depends(rate_limit("read"))],
            summary="Your developer key: limits and today's usage")
def key_info(principal: Principal = Depends(get_principal), client: redis.Redis = Depends(get_redis),
             session: Session = Depends(get_session)) -> KeyInfo:
    settings = get_settings()
    key = _own_key(principal, session)
    try:
        used, _ = daily_usage(client, key.id)
    except redis.RedisError:
        used = 0
    return KeyInfo(prefix=key.prefix, label=key.name.removeprefix(LABEL_PREFIX), created_at=key.created_at,
                   expires_at=key.expires_at, last_used_at=key.last_used_at, used_today=used,
                   per_minute=key.rate_limit_per_minute or settings.developer_key_per_minute,
                   per_day=settings.developer_key_daily_quota)


@router.post("/key/revoke", dependencies=[Depends(rate_limit("write"))], summary="Revoke your developer key")
def revoke(request: Request, principal: Principal = Depends(get_principal),
           session: Session = Depends(get_session)) -> dict:
    key = _own_key(principal, session)
    key.revoked_at = datetime.now(UTC)
    audit.record(session, action="developer_key.revoke", target_type="api_key", target_id=key.id,
                 principal=principal, request_id=getattr(request.state, "request_id", None))
    session.commit()
    return {"revoked": True}
