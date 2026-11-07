"""PublicationEngine: publica la cola de anuncios por conexión directa.

No abre ni controla ningún navegador. Para cada anuncio:

    1. comprueba que la cuenta está autenticada (AuthenticationManager)
    2. espera el intervalo mínimo desde la publicación anterior
    3. respeta el máximo diario por cuenta, si lo hay
    4. valida y sube las fotos (ImageManager)
    5. crea el anuncio (WallapopClient), con clave de idempotencia si se admite
    6. guarda el resultado (QueueManager) y lo registra en «Anuncios»

Un anuncio cada vez, en orden. Ningún error borra un anuncio de la cola:

=====================  ======================================================
Error                  Qué hace el motor
=====================  ======================================================
Autenticación (401)    El anuncio espera (sin gastar intento); la cuenta se
                       marca «hay que reconectar»; las demás cuentas siguen.
                       Al reconectar, continúa solo.
Límite (429)           Espera lo que diga Wallapop (o 5 min) y sigue.
Sin conexión           Reintenta con espera creciente.
Respuesta perdida      Comprueba en Wallapop si se creó ANTES de repetir.
Datos rechazados       El anuncio queda en «Error» con el motivo.
Permisos/config.       Se pausa y se explica qué falta.
Verificación           Se pausa: la hace el usuario en Wallapop.
=====================  ======================================================

Si fallan varios anuncios seguidos, el motor se pausa para no insistir.
"""

from __future__ import annotations

import enum
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from lot_bot.integration.auth_manager import AuthenticationManager
from lot_bot.integration.database_manager import DatabaseManager
from lot_bot.integration.image_manager import ImageManager, ImagePreparationError
from lot_bot.integration.queue_manager import ClaimedItem, QueueManager
from lot_bot.integration.wallapop_client import ErrorKind, WallapopClient, classify_error
from lot_bot.publishing.queue import MINIMUM_PUBLISH_INTERVAL_SECONDS, Clock
from lot_bot.wallapop.dto import ItemDraft
from lot_bot.wallapop.errors import AuthenticationError, WallapopError

logger = logging.getLogger(__name__)

SETTINGS_KEY = "motor_publicacion_directa"
DEFAULT_SETTINGS: dict[str, Any] = {
    "interval_seconds": MINIMUM_PUBLISH_INTERVAL_SECONDS,
    #: Máximo de anuncios nuevos por cuenta y día (0 = sin límite propio).
    "max_per_account_per_day": 0,
    #: Al abrir LOT Bot, continuar solo si se cerró con la publicación en marcha.
    "auto_resume": False,
    "state": "stopped",
    "pause_reason": None,
}
MAX_CONSECUTIVE_FAILURES = 5
#: Espera por defecto ante un 429 sin «Retry-After».
DEFAULT_RATE_LIMIT_WAIT_S = 300
#: Comprobaciones de un anuncio interrumpido antes de pedir revisión al usuario.
MAX_VERIFICATION_CHECKS = 5


class EngineState(str, enum.Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


ENGINE_STATE_LABELS = {
    EngineState.STOPPED: "Detenida",
    EngineState.RUNNING: "Publicando",
    EngineState.PAUSED: "En pausa",
}


@dataclass(slots=True)
class EngineStatus:
    state: EngineState
    pause_reason: str | None
    waiting_reason: str | None
    interval_seconds: int
    next_publish_at: datetime | None
    last_publish_at: datetime | None
    current_title: str | None
    counts: dict[str, int]
    provider_label: str
    can_publish: bool

    @property
    def label(self) -> str:
        return ENGINE_STATE_LABELS[self.state]


def _backoff(attempts: int) -> float:
    return float(min(3600, 60 * (2 ** max(0, attempts))))


class PublicationEngine:
    """Hilo de publicación secuencial sobre la cola persistente."""

    def __init__(
        self,
        db: DatabaseManager,
        queue: QueueManager,
        auth: AuthenticationManager,
        client: WallapopClient,
        images: ImageManager,
        *,
        listings: Any = None,
        audit: Any = None,
        clock: Clock | None = None,
        other_last_publish: Callable[[], float | None] | None = None,
    ) -> None:
        self._db = db
        self.queue = queue
        self.auth = auth
        self.client = client
        self.images = images
        self._listings = listings
        self._audit = audit
        self.clock = clock or Clock()
        #: Último intento de la cola de publicación anterior (para que el
        #: intervalo mínimo se respete también entre los dos sistemas).
        self._other_last_publish = other_last_publish
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_attempt: float | None = None
        self._last_published_at: datetime | None = None
        self._cooldown_until: float | None = None
        self._consecutive_failures = 0
        self._current_title: str | None = None
        self.waiting_reason: str | None = None
        self.listeners: list[Callable[[], None]] = []
        auth.ready_listeners.append(self.account_ready)

    # ------------------------------------------------------------------
    # Ajustes y estado persistente
    # ------------------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        values = {**DEFAULT_SETTINGS, **self._db.get_setting(SETTINGS_KEY)}
        try:
            values["interval_seconds"] = max(int(values["interval_seconds"]), self.minimum_interval)
        except (TypeError, ValueError):
            values["interval_seconds"] = self.minimum_interval
        return values

    @property
    def minimum_interval(self) -> int:
        """Mínimo entre publicaciones: el que declare Wallapop o 60 s."""
        declared = self.client.limits.min_seconds_between_publications
        return int(declared) if declared else MINIMUM_PUBLISH_INTERVAL_SECONDS

    def save_settings(self, **changes: Any) -> dict[str, Any]:
        allowed = {"interval_seconds", "max_per_account_per_day", "auto_resume"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Ajustes desconocidos: {', '.join(sorted(unknown))}")
        if "interval_seconds" in changes:
            value = int(changes["interval_seconds"])
            if value < self.minimum_interval:
                raise ValueError(
                    f"El intervalo mínimo entre publicaciones es de {self.minimum_interval} segundos."
                )
            changes["interval_seconds"] = value
        if "max_per_account_per_day" in changes:
            changes["max_per_account_per_day"] = max(0, int(changes["max_per_account_per_day"]))
        if "auto_resume" in changes:
            changes["auto_resume"] = bool(changes["auto_resume"])
        return self._db.update_setting(SETTINGS_KEY, **changes)

    @property
    def interval(self) -> int:
        return int(self.settings()["interval_seconds"])

    @property
    def state(self) -> EngineState:
        try:
            return EngineState(self.settings().get("state") or "stopped")
        except ValueError:
            return EngineState.STOPPED

    @property
    def pause_reason(self) -> str | None:
        return self.settings().get("pause_reason")

    def _set_state(self, state: EngineState, reason: str | None = None) -> None:
        self._db.update_setting(SETTINGS_KEY, state=state.value, pause_reason=reason)
        self._notify()

    def daily_cap(self) -> int:
        """Máximo diario efectivo por cuenta: el más prudente de los dos."""
        own = int(self.settings().get("max_per_account_per_day") or 0)
        declared = int(self.client.limits.max_publications_per_day or 0)
        caps = [c for c in (own, declared) if c > 0]
        return min(caps) if caps else 0

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def start(self) -> None:
        if not self.client.can_publish_directly:
            raise ValueError(
                "La publicación directa no usa el navegador. En «Conectar Wallapop» elige "
                "la conexión por credencial (token) (o «Demostración» para probar)."
            )
        missing = [m for m in self.client.missing_configuration()
                   if not m.startswith("Operación no autorizada")]
        if not self.client.is_demo and "create_item" not in {c.value for c in self.client.capabilities()}:
            raise ValueError(
                "El acceso autorizado no incluye la operación «create_item»: no se puede publicar."
                + (f" Falta: {'; '.join(missing)}" if missing else "")
            )
        self._consecutive_failures = 0
        self._set_state(EngineState.RUNNING)
        self._audit_success("Publicación directa iniciada")
        self._wake.set()

    def resume(self) -> None:
        self.start()

    def pause(self, reason: str = "Pausada por el usuario.") -> None:
        self._set_state(EngineState.PAUSED, reason)
        self._audit_success("Publicación directa en pausa", detail=reason)
        self._wake.set()

    def stop(self) -> None:
        self._set_state(EngineState.STOPPED)
        self._wake.set()

    def account_ready(self, account_ref: str) -> None:
        """Una cuenta vuelve a estar lista: si se pausó por ella, se continúa."""
        self.queue.account_ready(account_ref)
        reason = self.pause_reason or ""
        if self.state is EngineState.PAUSED and "reconectar" in reason.lower():
            try:
                self.resume()
            except ValueError:
                logger.debug("No se puede reanudar todavía", exc_info=True)
        self._wake.set()

    def status(self) -> EngineStatus:
        settings = self.settings()
        state = self.state
        next_at = None
        last = self._last_publish_ts()
        if state is EngineState.RUNNING and last is not None:
            next_at = datetime.fromtimestamp(last + int(settings["interval_seconds"]))
        return EngineStatus(
            state=state,
            pause_reason=settings.get("pause_reason"),
            waiting_reason=self.waiting_reason if state is EngineState.RUNNING else None,
            interval_seconds=int(settings["interval_seconds"]),
            next_publish_at=next_at,
            last_publish_at=self._last_published_at,
            current_title=self._current_title,
            counts=self.queue.counts(),
            provider_label=self.client.provider_label,
            can_publish=self.client.can_publish_directly,
        )

    # ------------------------------------------------------------------
    # Hilo
    # ------------------------------------------------------------------
    def start_worker(self) -> None:
        """Arranca el hilo y recupera lo que quedó a medias."""
        if self._thread and self._thread.is_alive():
            return
        self.queue.recover_interrupted()
        if self.state is EngineState.RUNNING:
            if self.settings().get("auto_resume") and self.client.can_publish_directly:
                logger.info("Publicación directa: se continúa tras el cierre anterior.")
            else:
                self._set_state(
                    EngineState.PAUSED,
                    "LOT Bot se cerró con la publicación en marcha. Los anuncios están a "
                    "salvo: pulsa «Continuar» para seguir.",
                )
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="lotbot-motor-directo", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def _loop(self) -> None:  # pragma: no cover - hilo real
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                logger.exception("Error inesperado en el motor de publicación directa")
                worked = False
            if not worked:
                self._wake.wait(2.0)
                self._wake.clear()

    def run_until_idle(self, max_steps: int = 10_000) -> int:
        """Procesa todo lo que se pueda ahora mismo, sin hilo (pruebas)."""
        steps = 0
        while steps < max_steps and self.run_once():
            steps += 1
        return steps

    # ------------------------------------------------------------------
    # Un paso
    # ------------------------------------------------------------------
    def _check_clock(self) -> None:
        if self.clock.is_real:
            return
        if self.client.is_demo or getattr(self.client.service, "allow_test_clock", False):
            return
        raise RuntimeError(
            "El reloj de pruebas solo se admite en modo DEMO: con Wallapop real se respeta "
            "siempre el intervalo real."
        )

    def _last_publish_ts(self) -> float | None:
        candidates = [self._last_attempt] if self._last_attempt is not None else []
        if self.clock.is_real and self._other_last_publish is not None:
            try:
                other = self._other_last_publish()
            except Exception:
                other = None
            if other is not None:
                candidates.append(other)
        return max(candidates) if candidates else None

    def _next_allowed(self) -> float:
        now = self.clock.now()
        last = self._last_publish_ts()
        allowed = now if last is None else max(now, last + self.interval)
        if self._cooldown_until is not None:
            allowed = max(allowed, self._cooldown_until)
        return allowed

    def _account_usable(self, account_ref: str) -> bool:
        try:
            status = self.auth.status(account_ref)
        except ValueError:
            return False
        return status.is_usable

    def run_once(self) -> bool:
        """Publica (o comprueba) el siguiente anuncio. False si no hay nada que hacer."""
        with self._lock:
            if self.state is not EngineState.RUNNING:
                return False
            self._check_clock()
            item = self.queue.claim_next(self._account_usable)
            if item is None:
                self._explain_idle()
                return False
            self.waiting_reason = None
            if item.status.value == "verifying":
                self._verify(item)
                return True

            # 1. Intervalo mínimo (y espera por límite de Wallapop).
            self._current_title = item.title
            wait = self._next_allowed() - self.clock.now()
            while wait > 0:
                self.clock.sleep(min(wait, 1.0), self._stop)
                if self._stop.is_set() or self.state is not EngineState.RUNNING:
                    self.queue.release(item.id)
                    self._current_title = None
                    return False
                wait = self._next_allowed() - self.clock.now()

            try:
                return self._publish(item)
            finally:
                self._current_title = None

    def _explain_idle(self) -> None:
        counts = self.queue.counts()
        if counts["pending"] + counts["verifying"] == 0:
            self.waiting_reason = "No hay anuncios pendientes. Añade anuncios a la cola."
            return
        blocked = [r for r in self.queue.blocked_accounts() if not self._account_usable(r)]
        if blocked:
            aliases = {s.account_ref: s.alias for s in self.auth.statuses()}
            names = ", ".join(aliases.get(r, r) for r in blocked)
            self.waiting_reason = (
                f"Esperando a que se reconecten estas cuentas: {names}. Sus anuncios "
                "siguen en la cola."
            )
            return
        wake = self.queue.next_wakeup()
        self.waiting_reason = (
            f"Próximo reintento a las {wake + _utc_offset():%H:%M}." if wake else None
        )

    def _publish(self, item: ClaimedItem) -> bool:
        ref = item.account_ref

        # 2. Máximo diario por cuenta.
        cap = self.daily_cap()
        if cap and self.queue.published_today(ref) >= cap:
            tomorrow = (_utcnow() + timedelta(days=1)).replace(
                hour=0, minute=5, second=0, microsecond=0
            )
            self.queue.mark_waiting(
                item.id, "LIMITE_DIARIO",
                f"Esta cuenta ya ha publicado {cap} anuncios hoy (máximo diario). "
                "Seguirá mañana.",
                until=tomorrow,
            )
            return True

        # 3. Autenticación.
        try:
            self.auth.ensure_ready(ref)
        except AuthenticationError as exc:
            # La cuenta deja de estar disponible; la espera evita insistir si
            # algo la siguiera mostrando como conectada.
            self.queue.mark_waiting(item.id, "RECONECTAR", exc.user_message,
                                    until=_utcnow() + timedelta(seconds=60))
            return True

        # 4. Fotos.
        try:
            prepared = self.images.prepare(item.image_paths, self.client.limits.max_images_per_item)
        except ImagePreparationError as exc:
            self.queue.mark_failed(item.id, "FOTOS", str(exc), count_attempt=False)
            self._failure("FOTOS", str(exc), item)
            return True
        try:
            refs = self.images.upload(
                self.client, ref, prepared, dict(item.uploaded_images),
                on_uploaded=lambda h, r: self.queue.record_upload(item.id, h, r),
            )
        except Exception as exc:
            self._handle_error(item, exc, phase="upload")
            return True

        # 5. Publicar.
        draft = ItemDraft(
            title=item.title,
            description=item.description,
            price=float(item.price),
            currency=item.currency,
            category=item.category,
            condition=item.condition,
            attributes={k: v for k, v in item.attributes.items() if not str(k).startswith("_")},
            image_paths=prepared.paths,
            image_urls=refs,
        )
        self._last_attempt = self.clock.now()
        try:
            result = self.client.create_listing(ref, draft, item.idempotency_key)
        except Exception as exc:
            self._handle_error(item, exc, phase="create")
            return True
        if not result.success:
            self.queue.mark_failed(item.id, "RECHAZADO", result.message or "Wallapop no lo aceptó.")
            self._failure("RECHAZADO", result.message, item)
            return True

        url = (result.data or {}).get("url") if isinstance(result.data, dict) else None
        note = "Publicado en Wallapop"
        if prepared.warnings:
            note += " · " + " · ".join(prepared.warnings)
        if item.image_paths and not refs and not self.client.is_demo:
            note += " · El acceso autorizado no incluye subir fotos: se enviaron sin subir."
        self.queue.mark_published(item.id, result.item_id, url, note)
        self._register_listing(item, result.item_id, url, refs or prepared.paths)
        self._consecutive_failures = 0
        self._last_published_at = datetime.now()
        self._audit_success("Anuncio publicado (conexión directa)", account_ref=ref, target=item.title,
                            detail=f"{item.price:.2f} €")
        return True

    # ------------------------------------------------------------------
    def _verify(self, item: ClaimedItem) -> None:
        """¿Llegó a publicarse un anuncio interrumpido? Nunca se repite a ciegas."""
        if not self.client.can_search_published:
            self.queue.mark_needs_review(
                item.id,
                "No se sabe si este anuncio llegó a publicarse y el acceso autorizado no "
                "permite comprobarlo. Míralo en Wallapop: si está, pulsa «Marcar como "
                "publicado»; si no, pulsa «Reintentar».",
            )
            return
        try:
            found = self.client.find_published(item.account_ref, item.title, item.price)
        except Exception as exc:
            message = getattr(exc, "user_message", None) or str(exc)
            if isinstance(exc, AuthenticationError):
                self.auth.mark_rejected(item.account_ref, message)
            checks = self.queue.defer_verification(
                item.id, f"No se ha podido comprobar ahora: {message}", 300
            )
            if checks >= MAX_VERIFICATION_CHECKS:
                self.queue.mark_needs_review(
                    item.id,
                    "No se ha podido comprobar si este anuncio llegó a publicarse. Míralo en "
                    "Wallapop y pulsa «Marcar como publicado» o «Reintentar».",
                )
            return
        if found is not None:
            url = found.raw.get("url") if isinstance(found.raw, dict) else None
            self.queue.mark_published(
                item.id, found.item_id or None, url,
                "Comprobado: ya estaba publicado en Wallapop (no se repite).",
            )
            self._register_listing(item, found.item_id, url, found.image_urls)
            return
        self.queue.mark_retry(
            item.id, "NO_PUBLICADO",
            "Comprobado: no llegó a publicarse. Se vuelve a intentar.", 0, count_attempt=False,
        )

    def _handle_error(self, item: ClaimedItem, exc: BaseException, *, phase: str) -> None:
        kind = classify_error(exc) if isinstance(exc, WallapopError) else ErrorKind.UNKNOWN
        message = getattr(exc, "user_message", None) or str(exc) or type(exc).__name__
        code = type(exc).__name__
        if not isinstance(exc, WallapopError):
            logger.exception("Error inesperado publicando «%s»", item.title, exc_info=exc)
        ref = item.account_ref

        if kind is ErrorKind.AUTH:
            self.auth.mark_rejected(ref, message)
            self.queue.mark_waiting(item.id, "RECONECTAR",
                                    f"{message} El anuncio espera a que reconectes la cuenta.",
                                    until=_utcnow() + timedelta(seconds=60))
            return
        if kind is ErrorKind.CONFIG:
            self.queue.mark_waiting(item.id, "CONFIGURACION", message)
            self.pause(f"{message} Revisa «Conectar Wallapop».")
            return
        if kind is ErrorKind.VERIFICATION:
            self.queue.mark_waiting(item.id, "VERIFICACION", message)
            self.pause(f"{message} Complétala en Wallapop y pulsa «Continuar».")
            return
        if kind is ErrorKind.RATE_LIMIT:
            delay = getattr(exc, "retry_after", None) or DEFAULT_RATE_LIMIT_WAIT_S
            self._cooldown_until = self.clock.now() + float(delay)
            self.queue.mark_waiting(
                item.id, "LIMITE_WALLAPOP",
                f"Wallapop pide esperar {int(delay)} s antes de seguir.",
                until=_utcnow() + timedelta(seconds=float(delay)),
            )
            return
        if kind is ErrorKind.REJECTED:
            self.queue.mark_failed(item.id, code, message)
            self._failure(code, message, item)
            return
        if kind is ErrorKind.NETWORK_UNCERTAIN and phase == "create" and not self.client.supports_idempotency:
            # Pudo llegar a Wallapop: se comprueba antes de repetir.
            self.queue.mark_verifying(
                item.id,
                "Se perdió la respuesta de Wallapop. Se comprobará si se publicó antes de repetir.",
            )
            self._failure(code, message, item)
            return
        # Red sin enviar, servicio no disponible, fallo desconocido: reintentar.
        status = self.queue.mark_retry(item.id, code, message, _backoff(item.attempts))
        self._failure(code, message, item, final=status.value == "failed")

    def _failure(self, code: str, message: str, item: ClaimedItem, final: bool = True) -> None:
        self._consecutive_failures += 1
        if self._audit is not None:
            try:
                self._audit.record_error(
                    "Publicación directa fallida", error=f"{code}: {message}",
                    account_ref=item.account_ref, target=item.title,
                )
            except Exception:
                logger.debug("Auditoría no disponible", exc_info=True)
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self.pause(
                f"Han fallado {self._consecutive_failures} anuncios seguidos (el último: "
                f"{message[:200]}). Revisa los errores y pulsa «Continuar»."
            )

    def _register_listing(self, item: ClaimedItem, remote_id: str | None, url: str | None,
                          images: list[str]) -> None:
        if not remote_id or self._listings is None:
            return
        try:
            self._listings.register_published(
                item.account_ref,
                remote_id,
                item.product_id,
                {
                    "title": item.title,
                    "description": item.description,
                    "price": item.price,
                    "currency": item.currency,
                    "category": item.category,
                    "condition": item.condition,
                    "image_urls": images,
                    "url": url,
                },
                meta={"origen": "motor_directo", "cola_id": item.id},
            )
        except Exception:  # registrar en local nunca debe deshacer una publicación
            logger.warning("No se ha podido registrar el anuncio en local", exc_info=True)

    def _audit_success(self, action: str, **kwargs: Any) -> None:
        if self._audit is None:
            return
        try:
            self._audit.record_success(action, **kwargs)
        except Exception:
            logger.debug("Auditoría no disponible", exc_info=True)

    def _notify(self) -> None:
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:
                logger.debug("Aviso del motor fallido", exc_info=True)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _utc_offset() -> timedelta:
    """Diferencia entre la hora local y UTC (las fechas se guardan en UTC)."""
    return datetime.now() - _utcnow()
