"""Historial de acciones.

Cada accion relevante deja constancia de: que se hizo, quien la pidio, sobre
que cuenta, cuando, con que resultado y, si fallo, por que.

Los secretos nunca llegan aqui: el texto pasa por el filtro de redaccion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select

from lot_bot.database.engine import Database
from lot_bot.database.models import Account, ActionResult, AuditLog
from lot_bot.logs.redaction import redact

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AuditEntry:
    """Linea del historial, lista para mostrar en pantalla."""

    id: int
    timestamp: datetime
    actor: str
    action: str
    account_ref: str | None
    target: str | None
    result: str
    detail: str | None
    error: str | None

    def format_line(self) -> str:
        stamp = self.timestamp.strftime("%d/%m/%Y %H:%M")
        parts = [stamp]
        if self.account_ref:
            parts.append(self.account_ref)
        parts.append(self.action)
        if self.target:
            parts.append(f"Producto: {self.target}")
        parts.append(f"Resultado: {self.result.upper()}")
        return "\n".join(parts)


class AuditService:
    """Escribe y consulta el historial de acciones."""

    def __init__(self, database: Database, demo_mode: bool = False) -> None:
        self._db = database
        #: En DEMO, cada entrada se marca con «[DEMO]» para que nadie la
        #: confunda con una operación real en Wallapop.
        self.demo_mode = demo_mode

    # ------------------------------------------------------------------
    def record(
        self,
        action: str,
        *,
        actor: str = "usuario",
        account_ref: str | None = None,
        target: str | None = None,
        result: ActionResult | str = ActionResult.OK,
        detail: str | None = None,
        error: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        if isinstance(result, str):
            result = ActionResult(result)
        if self.demo_mode and not action.startswith("[DEMO]"):
            action = f"[DEMO] {action}"
        with self._db.session_scope() as session:
            account_id = None
            if account_ref:
                account_id = session.scalar(
                    select(Account.id).where(Account.internal_ref == account_ref)
                )
            entry = AuditLog(
                actor=actor,
                action=action[:120],
                account_id=account_id,
                account_ref=account_ref,
                target=(target or "")[:250] or None,
                result=result,
                detail=redact(detail) if detail else None,
                error=redact(error) if error else None,
                payload=_safe_payload(payload or {}),
            )
            session.add(entry)
            session.flush()
            return entry.id

    def record_success(self, action: str, **kwargs: Any) -> int:
        return self.record(action, result=ActionResult.OK, **kwargs)

    def record_error(self, action: str, error: str, **kwargs: Any) -> int:
        kwargs.pop("result", None)
        return self.record(action, result=ActionResult.ERROR, error=error, **kwargs)

    def record_cancelled(self, action: str, **kwargs: Any) -> int:
        return self.record(action, result=ActionResult.CANCELLED, **kwargs)

    # ------------------------------------------------------------------
    def recent(
        self,
        limit: int = 200,
        account_ref: str | None = None,
        only_errors: bool = False,
        since: datetime | None = None,
    ) -> list[AuditEntry]:
        with self._db.session_scope() as session:
            stmt = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
            if account_ref:
                stmt = stmt.where(AuditLog.account_ref == account_ref)
            if only_errors:
                stmt = stmt.where(AuditLog.result == ActionResult.ERROR)
            if since:
                stmt = stmt.where(AuditLog.timestamp >= since)
            return [
                AuditEntry(
                    id=row.id,
                    timestamp=row.timestamp,
                    actor=row.actor,
                    action=row.action,
                    account_ref=row.account_ref,
                    target=row.target,
                    result=row.result.value,
                    detail=row.detail,
                    error=row.error,
                )
                for row in session.scalars(stmt).all()
            ]

    def stats(self, days: int = 7) -> dict[str, int]:
        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
        with self._db.session_scope() as session:
            total = session.scalar(
                select(func.count(AuditLog.id)).where(AuditLog.timestamp >= since)
            ) or 0
            errors = session.scalar(
                select(func.count(AuditLog.id))
                .where(AuditLog.timestamp >= since)
                .where(AuditLog.result == ActionResult.ERROR)
            ) or 0
            return {"total": total, "errores": errors, "correctas": total - errors, "dias": days}

    def purge_older_than(self, days: int = 180) -> int:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
        with self._db.session_scope() as session:
            result = session.execute(delete(AuditLog).where(AuditLog.timestamp < cutoff))
            return result.rowcount or 0


_SENSITIVE_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
    "secret",
    "password",
    "api_key",
    "authorization",
}


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Elimina cualquier clave sensible antes de guardar el historial."""
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if any(marker in str(key).lower() for marker in _SENSITIVE_KEYS):
            clean[key] = "***REDACTED***"
        elif isinstance(value, dict):
            clean[key] = _safe_payload(value)
        elif isinstance(value, str):
            clean[key] = redact(value)[:1000]
        elif isinstance(value, (list, tuple)):
            clean[key] = [
                _safe_payload(v) if isinstance(v, dict) else (redact(v)[:500] if isinstance(v, str) else v)
                for v in value[:50]
            ]
        else:
            clean[key] = value
    return clean
