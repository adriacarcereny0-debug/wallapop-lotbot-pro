"""Bus de eventos interno.

Permite que los servicios avisen a la interfaz (progreso, errores, fin de una
automatizacion) sin depender de Qt ni conocer las ventanas.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

Listener = Callable[[dict[str, Any]], None]


class EventBus:
    """Publicacion/suscripcion sencilla y segura entre hilos."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, topic: str, listener: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners[topic].append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners.get(topic, []):
                    self._listeners[topic].remove(listener)

        return unsubscribe

    def publish(self, topic: str, payload: dict[str, Any] | None = None) -> None:
        with self._lock:
            listeners = list(self._listeners.get(topic, []))
        data = payload or {}
        for listener in listeners:
            try:
                listener(data)
            except Exception:  # pragma: no cover - un oyente no debe romper el emisor
                logger.exception("Error en un suscriptor del evento '%s'", topic)

    def clear(self) -> None:
        with self._lock:
            self._listeners.clear()


# Temas de eventos usados por la aplicacion.
TOPIC_ACCOUNTS_CHANGED = "accounts.changed"
TOPIC_CATALOG_CHANGED = "catalog.changed"
TOPIC_LISTINGS_CHANGED = "listings.changed"
TOPIC_MESSAGES_CHANGED = "messages.changed"
TOPIC_AUTOMATION_RUN = "automation.run"
TOPIC_AUDIT_CHANGED = "audit.changed"
TOPIC_ERROR = "error"

_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
