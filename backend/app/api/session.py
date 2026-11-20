"""Admin sign-in with email and password (admin instance only).

A successful sign-in creates a short-lived API key ("web sign-in": 12 hours, or 7 days with ``remember``) with
the user's role scopes, so the admin UI never needs a long-lived ``cat_`` key pasted in. Everything else keeps
working with Bearer keys as before. Sign-out revokes the key.

Brute force: the ``write`` rate limit per client, plus at most ``LOGIN_FAILURES`` wrong passwords or codes per
email in ``LOGIN_WINDOW`` seconds (Redis). Wrong email and wrong password give the same answer.

Second factor (backend/app/mfa.py): an account with TOTP enrolled (``python -m backend.cli mfa-enroll``) also needs a
6-digit code or one of its backup codes; without one the answer is ``otp_required``. With
``CATEGORY_ADMIN_MFA_REQUIRED=true`` an account without TOTP cannot sign in with a password at all. API keys are
separate credentials and are not affected.
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

from common.config import get_settings
from database.models import ApiKey, Role, User
from database.session import get_session

from .. import audit, mfa
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
    otp: str | None = Field(default=None, max_length=32,
                            description="6-digit code from the authenticator app, or a backup code (abcd-ef01-2345)")


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

    user = session.scalar(select(User).where(User.email == email).with_for_update())
    can_sign_in = (user is not None and user.is_active and user.password_hash is not None
                   and Role(user.role) in STAFF)
    password_ok = verify_password(user.password_hash if can_sign_in else _NO_ACCOUNT_HASH, body.password)
    ok = can_sign_in and password_ok

    def failed(code: str, message: str, reason: str) -> ApiError:
        try:
            pipe = client.pipeline()
            pipe.incr(fails)
            pipe.expire(fails, LOGIN_WINDOW)
            pipe.execute()
        except redis.RedisError:
            pass
        audit.record(session, action="session.failed", target_type="user", target_id=None,
                     after={"email_hash": fails[-32:], "reason": reason}, principal=principal,
                     request_id=getattr(request.state, "request_id", None))
        session.commit()
        return ApiError(401, code, message)

    if not ok:
        raise failed("invalid_login", "wrong email or password", "password")
    settings = get_settings()
    if user.totp_secret:
        if not (body.otp or "").strip():  # the password was right; ask for the code (not counted as a failure)
            raise ApiError(401, "otp_required", "enter the code from your authenticator app, or a backup code")
        if not second_factor(session, user, body.otp, app_secret(), request, principal):
            raise failed("invalid_otp", "wrong or already used code", "otp")
    elif settings.admin_mfa_required:
        raise ApiError(403, "mfa_enrollment_required", "this account has no second factor yet; an administrator "
                                                       "enrols it with: python -m backend.cli mfa-enroll --email ...")

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


def app_secret() -> str:
    s = get_settings().fingerprint_secret
    return s.get_secret_value() if s else ""


def second_factor(session: Session, user: User, code: str, secret_key: str, request: Request,
                  principal: Principal) -> bool:
    """A TOTP code (each time step once) or a backup code (each code once)."""
    try:
        step = mfa.matching_step(mfa.unseal(user.totp_secret, secret_key), code, last_step=user.totp_last_step)
    except mfa.MfaError:
        step = None
    if step is not None:
        user.totp_last_step = step
        return True
    remaining = mfa.use_backup_code(code, list(user.totp_backup_codes or []))
    if remaining is None:
        return False
    user.totp_backup_codes = remaining
    audit.record(session, action="session.backup_code_used", target_type="user", target_id=user.id,
                 after={"backup_codes_left": len(remaining)}, principal=principal,
                 request_id=getattr(request.state, "request_id", None))
    return True


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
