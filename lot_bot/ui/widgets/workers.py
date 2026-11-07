"""Ejecucion de tareas largas fuera del hilo de la interfaz.

Sincronizar con Wallapop o consultar la IA puede tardar segundos. Si se hiciera
en el hilo grafico, la ventana se quedaria congelada. Estas clases ejecutan el
trabajo en segundo plano y devuelven el resultado a la interfaz.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

logger = logging.getLogger(__name__)


class _Signals(QObject):
    finished = Signal(object)
    failed = Signal(str, str)
    done = Signal()


class _Task(QRunnable):
    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.signals = _Signals()
        self._function = function
        self._args = args
        self._kwargs = kwargs

    @Slot()
    def run(self) -> None:  # pragma: no cover - se ejecuta en otro hilo
        try:
            result = self._function(*self._args, **self._kwargs)
        except Exception as exc:
            logger.exception("Error en tarea en segundo plano")
            self.signals.failed.emit(_friendly_message(exc), traceback.format_exc())
        else:
            self.signals.finished.emit(result)
        finally:
            self.signals.done.emit()


class TaskRunner:
    """Lanza funciones en segundo plano y entrega el resultado a la interfaz."""

    def __init__(self, max_threads: int = 4) -> None:
        # Grupo de hilos PROPIO: así el asistente tiene los suyos y una tarea
        # larga de otra pantalla (p. ej. el navegador) nunca lo deja esperando.
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max_threads)
        #: Señales de las tareas en curso. Se guardan aquí hasta que la
        #: interfaz ha recibido «terminado»: si Python las destruyera antes (al
        #: acabar la tarea en su hilo), Qt descartaría los avisos pendientes y
        #: la interfaz se quedaría esperando para siempre («Pensando…»).
        self._alive: set[_Signals] = set()

    def run(
        self,
        function: Callable[..., Any],
        *args: Any,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        on_done: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> None:
        """Ejecuta `function(*args, **kwargs)` en segundo plano.

        Los tres callbacks son solo por nombre para que nunca puedan
        confundirse con los argumentos de la funcion.
        """
        task = _Task(function, *args, **kwargs)
        if on_success is not None:
            task.signals.finished.connect(on_success)
        if on_error is not None:
            task.signals.failed.connect(on_error)
        if on_done is not None:
            task.signals.done.connect(on_done)
        signals = task.signals
        self._alive.add(signals)
        signals.done.connect(lambda: self._alive.discard(signals))
        self._pool.start(task)

    def wait(self, timeout_ms: int = 5000) -> bool:
        return self._pool.waitForDone(timeout_ms)


def _friendly_message(exc: Exception) -> str:
    """Traduce la excepcion a un mensaje entendible por el usuario."""
    from lot_bot.wallapop.errors import WallapopError

    if isinstance(exc, WallapopError):
        return exc.user_message
    if isinstance(exc, PermissionError):
        return str(exc)
    if isinstance(exc, ValueError):
        return str(exc)
    return f"Se ha producido un error inesperado ({type(exc).__name__}). Consulta la pantalla de Logs."
