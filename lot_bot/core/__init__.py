"""Servicios transversales: auditoria y eventos."""

from lot_bot.core.audit import AuditEntry, AuditService
from lot_bot.core.events import EventBus, get_event_bus

__all__ = ["AuditService", "AuditEntry", "EventBus", "get_event_bus"]
