"""Admin sign-in with email and password (admin instance only).

A successful sign-in creates a short-lived API key ("web sign-in": 12 hours, or 7 days with ``remember``) with
the user's role scopes, so the admin UI never needs a long-lived ``cat_`` key pasted in. Everything else keeps
working with Bearer keys as before. Sign-out revokes the key.

Brute force: the ``write`` rate limit per client, plus at most ``LOGIN_FAILURES`` wrong passwords per email in
``LOGIN_WINDOW`` seconds (Redis). Wrong email and wrong password give the same answer.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import redis
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import ApiKey, Role, User
from database.session import get_session

from .. import audit
from ..deps import Principal, get_principal, get_redis, rate_limit
from ..errors import ApiError
from ..security import EXPLICIT_SCOPES, ROLE_SCOPES, generate_api_key, hash_password, verify_password

router = APIRouter(prefix="/api/v1/admin/session", tags=["admin"], dependencies=[Depends(rate_limit("write"))])

SESSION_KEY_NAME = "web sign-in"
LOGIN_FAILURES = 5
LOGIN_WINDOW = 900  # seconds
SHORT, LONG = timedelta(hours=12), timedelta(days=7)
STAFF = (Role.ADMIN, Role.MODERATOR)
# Checked when the account does not exist or cannot sign in, so both cases take as long as a wrong password and
# the response time does not reveal which e-mail addresses have accounts.
_NO_ACCOUNT_HASH = hash_password(secrets.token_urlsafe(32))


class SignIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1000)
    remember: bool = Field(default=False, description="7 days instead of 12 hours")


class SessionOut(BaseModel):
    key: str = Field(description="send as 'Authorization: Bearer <key>'; shown only once")
    expires_at: datetime
    role: str


def _failures_key(email: str) -> str:
    return "category:login-fail:" + hashlib.sha256(email.encode()).hexdigest()[:32]


@router.post("", response_model=SessionOut, status_code=201, summary="Sign in with email and password")
def sign_in(body: SignIn, request: Request, principal: Principal = Depends(get_principal),
            client: redis.Redis = Depends(get_redis), session: Session = Depends(get_session)) -> SessionOut:
    email = body.email.strip().lower()
    fails = _failures_key(email)
    try:
        if int(client.get(fails) or 0) >= LOGIN_FAILURES:
            raise ApiError(429, "too_many_attempts", "too many failed sign-ins; try again in 15 minutes",
                           headers={"Retry-After": str(LOGIN_WINDOW)})
    except redis.RedisError as exc:
        raise ApiError(503, "unavailable", "sign-in is unavailable right now") from exc

    user = session.scalar(select(User).where(User.email == email))
    can_sign_in = (user is not None and user.is_active and user.password_hash is not None
                   and Role(user.role) in STAFF)
    password_ok = verify_password(user.password_hash if can_sign_in else _NO_ACCOUNT_HASH, body.password)
    ok = can_sign_in and password_ok
    if not ok:
        try:
            pipe = client.pipeline()
            pipe.incr(fails)
            pipe.expire(fails, LOGIN_WINDOW)
            pipe.execute()
        except redis.RedisError:
            pass
        audit.record(session, action="session.failed", target_type="user", target_id=None,
                     after={"email_hash": fails[-32:]}, principal=principal,
                     request_id=getattr(request.state, "request_id", None))
        session.commit()
        raise ApiError(401, "invalid_login", "wrong email or password")

    try:
        client.delete(fails)
    except redis.RedisError:
        pass
    new = generate_api_key()
    expires = datetime.now(UTC) + (LONG if body.remember else SHORT)
    key = ApiKey(user_id=user.id, name=SESSION_KEY_NAME, prefix=new.prefix, key_hash=new.key_hash,
                 scopes=sorted(ROLE_SCOPES[Role(user.role)] - EXPLICIT_SCOPES), expires_at=expires)
    session.add(key)
    session.flush()
    audit.record(session, action="session.create", target_type="api_key", target_id=key.id,
                 after={"user_id": user.id, "prefix": new.prefix, "remember": body.remember}, principal=principal,
                 request_id=getattr(request.state, "request_id", None))
    session.commit()
    return SessionOut(key=new.key, expires_at=expires, role=str(user.role))


@router.post("/logout", status_code=204, summary="Sign out (revokes this sign-in key)")
def sign_out(request: Request, principal: Principal = Depends(get_principal),
             session: Session = Depends(get_session)) -> Response:
    if principal.api_key_id is not None:
        key = session.get(ApiKey, principal.api_key_id)
        if key is not None and key.name == SESSION_KEY_NAME and key.revoked_at is None:
            key.revoked_at = datetime.now(UTC)
            audit.record(session, action="session.end", target_type="api_key", target_id=key.id,
                         principal=principal, request_id=getattr(request.state, "request_id", None))
            session.commit()
    return Response(status_code=204)
