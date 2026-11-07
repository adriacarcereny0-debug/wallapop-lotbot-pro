"""Navegadores abiertos por cuenta, reutilizados entre publicaciones.

Cada cuenta tiene UN hilo propio que abre su perfil persistente y lo mantiene
abierto mientras se usa: publicar 50 anuncios en la cuenta B abre el
navegador de B una sola vez y reutiliza su sesión. Tras un tiempo sin uso se
cierra solo (las cookies quedan guardadas en el perfil).

Playwright exige que todo lo relacionado con un navegador se haga desde el
hilo que lo abrió; por eso cada cuenta tiene su hilo y las operaciones se le
envían como funciones `fn(page)`.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

from lot_bot.wallapop.browser.driver import BrowserLauncher, BrowserPage

logger = logging.getLogger(__name__)

_STOP = object()


class _AccountWorker:
    def __init__(
        self,
        ref: str,
        launcher: BrowserLauncher,
        profile_dir: Path,
        options: dict[str, Any],
        idle_seconds: float,
        on_exit: Callable[[str, _AccountWorker], None],
    ) -> None:
        self.ref = ref
        self._launcher = launcher
        self._profile = profile_dir
        self._options = options
        self._idle = idle_seconds
        self._on_exit = on_exit
        self._jobs: queue.Queue = queue.Queue()
        self.opened = False
        self._thread = threading.Thread(target=self._run, name=f"lotbot-nav-{ref}", daemon=True)
        self._thread.start()

    def submit(self, fn: Callable[[BrowserPage], Any]) -> Future:
        future: Future = Future()
        self._jobs.put((fn, future))
        return future

    def stop(self) -> None:
        self._jobs.put(_STOP)

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def _fail_pending(self, exc: BaseException) -> None:
        while True:
            try:
                item = self._jobs.get_nowait()
            except queue.Empty:
                return
            if item is not _STOP:
                item[1].set_exception(exc)

    def _run(self) -> None:
        """Abre el perfil y atiende operaciones. Si el usuario cierra la
        ventana o la pestaña, se vuelve a abrir el MISMO perfil (con su
        sesión) en la siguiente operación: cerrar la ventana no desconecta."""
        try:
            item = self._jobs.get()
            reopen = 0
            while item is not _STOP:
                reopen += 1
                if reopen > 3:
                    exc = RuntimeError("No se ha podido mantener abierta la ventana del navegador.")
                    item[1].set_exception(exc)
                    self._fail_pending(exc)
                    return
                try:
                    with self._launcher.open(self._profile, **self._options) as page:
                        self.opened = True
                        while item is not _STOP:
                            if not page.is_alive():
                                logger.info("Ventana de %s cerrada; se reabre su perfil.", self.ref)
                                break  # reabrir y ejecutar este mismo trabajo
                            fn, future = item
                            reopen = 0
                            if future.set_running_or_notify_cancel():
                                try:
                                    future.set_result(fn(page))
                                except BaseException as exc:
                                    if not page.is_alive():
                                        # El usuario ha cerrado la ventana: se para
                                        # aquí. No se reabre ni se repite nada.
                                        from lot_bot.wallapop.errors import WindowClosedError

                                        logger.info("Ventana de %s cerrada por el usuario: se detiene.", self.ref)
                                        closed = WindowClosedError(str(exc)[:200])
                                        future.set_exception(closed)
                                        self._fail_pending(closed)
                                        return
                                    future.set_exception(exc)
                            try:
                                item = self._jobs.get(timeout=self._idle)
                            except queue.Empty:
                                logger.info("Navegador de %s cerrado por inactividad.", self.ref)
                                return
                except BaseException as exc:  # no se pudo abrir el navegador
                    if item is not _STOP:
                        future = item[1]
                        target = future.inner if isinstance(future, _Retry) else future
                        if not target.done():
                            target.set_exception(exc)
                    self._fail_pending(exc)
                    return
        finally:
            self._on_exit(self.ref, self)


class _Retry:
    """Envuelve el Future de un trabajo que se repite tras reabrir la ventana."""

    def __init__(self, inner: Future) -> None:
        self.inner = inner

    def set_running_or_notify_cancel(self) -> bool:
        return True

    def set_result(self, value) -> None:
        self.inner.set_result(value)

    def set_exception(self, exc) -> None:
        self.inner.set_exception(exc)

    def done(self) -> bool:
        return self.inner.done()


class BrowserSessionPool:
    #: Tiempo máximo esperando una operación del navegador (publicar incluye
    #: subir fotos y esperar la confirmación de Wallapop).
    DEFAULT_TIMEOUT = 600.0

    def __init__(self, launcher: BrowserLauncher, idle_seconds: float = 900.0) -> None:
        self._launcher = launcher
        self._idle = idle_seconds
        self._workers: dict[str, _AccountWorker] = {}
        self._lock = threading.Lock()
        #: Veces que se ha abierto un navegador por cuenta (diagnóstico/pruebas).
        self.open_count: dict[str, int] = {}

    def _exit(self, ref: str, worker: _AccountWorker) -> None:
        with self._lock:
            if self._workers.get(ref) is worker:
                del self._workers[ref]

    def run(
        self,
        ref: str,
        profile_dir: Path,
        fn: Callable[[BrowserPage], Any],
        *,
        timeout: float | None = None,
        **options: Any,
    ) -> Any:
        """Ejecuta `fn(page)` en el navegador de la cuenta (lo abre si hace falta)."""
        with self._lock:
            worker = self._workers.get(ref)
            if worker is None:
                worker = _AccountWorker(
                    ref, self._launcher, profile_dir, options, self._idle, self._exit
                )
                self._workers[ref] = worker
                self.open_count[ref] = self.open_count.get(ref, 0) + 1
            future = worker.submit(fn)
        try:
            return future.result(timeout if timeout is not None else self.DEFAULT_TIMEOUT)
        except FutureTimeout as exc:
            future.cancel()
            logger.error("El navegador de %s no ha respondido a tiempo; se cierra.", ref)
            threading.Thread(target=self.close, args=(ref, 5.0), daemon=True).start()
            from lot_bot.wallapop.browser.driver import BrowserUnavailable

            raise BrowserUnavailable(
                "El navegador no ha respondido a tiempo. Se ha cerrado; vuelve a intentarlo."
            ) from exc

    def is_open(self, ref: str) -> bool:
        with self._lock:
            return ref in self._workers

    def close(self, ref: str, timeout: float = 10.0) -> None:
        """Cierra el navegador de una cuenta (antes de borrar su perfil o de
        abrirlo para iniciar sesión: Chrome no deja usar un perfil dos veces)."""
        with self._lock:
            worker = self._workers.pop(ref, None)
        if worker is not None:
            worker.stop()
            worker.join(timeout)

    def close_all(self, timeout: float = 10.0) -> None:
        with self._lock:
            refs = list(self._workers)
        for ref in refs:
            self.close(ref, timeout)
