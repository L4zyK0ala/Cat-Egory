"""Request dependencies: database session, Redis, caller identity (principal), scopes and rate limits."""
from __future__ import annotations

import ipaddress
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import redis
from fastapi import Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.config import Settings, get_settings
from database.models import ApiKey, Role, User
from database.session import get_session

from .errors import ApiError
from .security import DEVELOPER_ACCOUNT_EMAIL, ROLE_SCOPES, fingerprint, key_matches, parse_api_key

log = logging.getLogger("category.auth")
bearer = HTTPBearer(auto_error=False, description="API key: `cat_<prefix>_<secret>`")
_LAST_USED_RESOLUTION = timedelta(minutes=5)


@lru_cache
def _redis_client(url: str) -> redis.Redis:
    return redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)


def get_redis() -> redis.Redis:
    return _redis_client(get_settings().redis_url)


@dataclass(slots=True)
class Principal:
    client_ip: str | None
    fingerprint: str | None
    user_id: int | None = None
    role: Role | None = None
    api_key_id: int | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    rate_limit: int | None = None
    daily_quota: int | None = None  # self-service developer keys only

    @property
    def authenticated(self) -> bool:
        return self.api_key_id is not None


def client_ip(request: Request, settings: Settings) -> str | None:
    """The address to rate-limit: the connection's, or the visitor's as stated by a trusted proxy (the Cloudflare
    tunnel). The header is ignored from anyone else, otherwise any client could pick its own rate-limit identity.
    IPv6 is reduced to its /64, the unit a single customer usually controls."""
    peer = request.client.host if request.client else None
    try:
        trusted = peer is not None and any(ipaddress.ip_address(peer) in net for net in settings.trusted_proxies)
        if not trusted:
            return peer
        real = ipaddress.ip_address(request.headers.get("cf-connecting-ip", "").strip())
    except ValueError:
        return peer
    return str(ipaddress.ip_network(f"{real}/64", strict=False).network_address) if real.version == 6 else str(real)


def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: Session = Depends(get_session),
    client: redis.Redis = Depends(get_redis),
) -> Principal:
    settings = get_settings()
    ip = client_ip(request, settings)
    secret = settings.fingerprint_secret.get_secret_value() if settings.fingerprint_secret else None
    principal = Principal(client_ip=ip, fingerprint=fingerprint(secret, ip))
    if credentials is None:
        return principal

    # A wrong key is refused before the rate limiter runs, so wrong keys are counted here: without it, every guess
    # would be a free database query. Redis down = not counted (the same fail-open choice as the rate limit).
    now_s = int(time.time())
    failures = f"rl:authfail:{principal.fingerprint or ip or 'unknown'}:{now_s - now_s % 60}"
    try:
        blocked = int(client.get(failures) or 0) >= settings.auth_failure_limit_per_minute
    except redis.RedisError:
        blocked = False
    if blocked:
        raise ApiError(429, "rate_limited", "too many failed API key attempts",
                       headers={"Retry-After": str(60 - now_s % 60)})

    def refused() -> ApiError:
        try:
            pipe = client.pipeline()
            pipe.incr(failures)
            pipe.expire(failures, 65)
            pipe.execute()
        except redis.RedisError:
            pass
        return ApiError(401, "invalid_api_key", "invalid, expired or revoked API key",
                        headers={"WWW-Authenticate": "Bearer"})

    prefix = parse_api_key(credentials.credentials)
    if prefix is None:
        raise refused()
    row = session.execute(select(ApiKey, User).join(User, User.id == ApiKey.user_id)
                          .where(ApiKey.prefix == prefix)).first()
    now = datetime.now(UTC)
    if row is None:
        raise refused()
    key, user = row
    if (not key_matches(credentials.credentials, key.key_hash) or key.revoked_at is not None
            or (key.expires_at is not None and key.expires_at <= now) or not user.is_active):
        raise refused()

    if key.last_used_at is None or now - key.last_used_at > _LAST_USED_RESOLUTION:
        key.last_used_at = now
        session.commit()
    principal.user_id, principal.role, principal.api_key_id = user.id, Role(user.role), key.id
    # Effective scopes: what the key was given AND what the user's current role allows.
    principal.scopes = frozenset(key.scopes) & ROLE_SCOPES[Role(user.role)]
    principal.rate_limit = key.rate_limit_per_minute
    if user.email == DEVELOPER_ACCOUNT_EMAIL:
        principal.daily_quota = settings.developer_key_daily_quota
    request.state.principal = principal
    return principal


def require(scope: str) -> Callable[..., Principal]:
    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.authenticated:
            raise ApiError(401, "authentication_required", "an API key is required",
                           headers={"WWW-Authenticate": "Bearer"})
        if scope not in principal.scopes:
            raise ApiError(403, "insufficient_scope", f"this API key lacks the '{scope}' scope")
        return principal

    return dependency


def daily_usage(client: redis.Redis, key_id: int | None, *, count: bool = False) -> tuple[int, int]:
    """Requests made with a developer key today (UTC), counting this one if ``count``; seconds until reset."""
    now = int(time.time())
    day, until_midnight = now // 86400, 86400 - now % 86400
    name = f"quota:{key_id}:{day}"
    if not count:
        return int(client.get(name) or 0), until_midnight
    pipe = client.pipeline()
    pipe.incr(name)
    pipe.expire(name, 2 * 86400)
    return int(pipe.execute()[0]), until_midnight


def consume_quota(client: redis.Redis, principal: Principal, extra: int) -> None:
    """A request that does the work of many (bulk lookup) uses that many from a developer key's daily quota."""
    if principal.daily_quota is None or extra <= 0:
        return
    try:
        now = int(time.time())
        name = f"quota:{principal.api_key_id}:{now // 86400}"
        pipe = client.pipeline()
        pipe.incrby(name, extra)
        pipe.expire(name, 2 * 86400)
        used = int(pipe.execute()[0])
    except redis.RedisError:
        return
    if used > principal.daily_quota:
        raise ApiError(429, "daily_quota_exceeded", "this key used its daily quota; it resets at 00:00 UTC",
                       headers={"Retry-After": str(86400 - now % 86400)})


class RateLimiter:
    """Fixed one-minute windows in Redis (INCR + EXPIRE). Simple, O(1), shared by all API replicas."""

    def __init__(self, client: redis.Redis, window_seconds: int = 60) -> None:
        self.client, self.window = client, window_seconds

    def hit(self, bucket: str, identity: str, limit: int) -> tuple[bool, int, int]:
        """Return (allowed, remaining, seconds_until_reset)."""
        now = int(time.time())
        window_start = now - now % self.window
        key = f"rl:{bucket}:{identity}:{window_start}"
        pipe = self.client.pipeline()
        pipe.incr(key)
        pipe.expire(key, self.window + 5)
        count = int(pipe.execute()[0])
        return count <= limit, max(0, limit - count), window_start + self.window - now


def rate_limit(bucket: str) -> Callable[..., None]:
    """``bucket`` is ``read`` or ``write``; API keys get their own (higher) limit."""

    def dependency(response: Response, principal: Principal = Depends(get_principal),
                   client: redis.Redis = Depends(get_redis)) -> None:
        settings = get_settings()
        if principal.authenticated:
            identity = f"key:{principal.api_key_id}"
            limit = principal.rate_limit or settings.api_key_rate_limit_per_minute
        else:
            identity = f"ip:{principal.fingerprint or principal.client_ip or 'unknown'}"
            limit = (settings.public_rate_limit_per_minute if bucket == "read"
                     else settings.anonymous_write_limit_per_minute)
        try:
            allowed, remaining, reset = RateLimiter(client).hit(bucket, identity, limit)
        except redis.RedisError as exc:
            log.warning("rate limiter unavailable", extra={"error": type(exc).__name__})
            if settings.rate_limit_fail_open:
                return
            raise ApiError(503, "unavailable", "rate limiter unavailable") from exc
        headers = {"RateLimit-Limit": str(limit), "RateLimit-Remaining": str(remaining), "RateLimit-Reset": str(reset)}
        if not allowed:
            raise ApiError(429, "rate_limited", "too many requests", headers=headers | {"Retry-After": str(reset)})
        if principal.daily_quota is not None:
            try:
                used, until_midnight = daily_usage(client, principal.api_key_id, count=True)
            except redis.RedisError:
                used, until_midnight = 0, 0  # same fail-open choice as the per-minute limit
            headers |= {"X-Quota-Limit": str(principal.daily_quota),
                        "X-Quota-Remaining": str(max(0, principal.daily_quota - used))}
            if used > principal.daily_quota:
                raise ApiError(429, "daily_quota_exceeded", "this key used its daily quota; it resets at 00:00 UTC",
                               headers=headers | {"Retry-After": str(until_midnight)})
        response.headers.update(headers)

    return dependency
