"""Audit log helper. Call it inside the same transaction as the change it records."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from database.models import AuditLog


def record(session: Session, *, action: str, target_type: str, target_id: str | int | None = None,
           before: dict[str, Any] | None = None, after: dict[str, Any] | None = None, principal=None,
           request_id: str | None = None) -> None:
    session.add(AuditLog(
        action=action,
        target_type=target_type,
        target_id=None if target_id is None else str(target_id),
        before=before,
        after=after,
        actor_user_id=getattr(principal, "user_id", None),
        actor_api_key_id=getattr(principal, "api_key_id", None),
        actor_fingerprint=getattr(principal, "fingerprint", None),
        request_id=request_id,
    ))
