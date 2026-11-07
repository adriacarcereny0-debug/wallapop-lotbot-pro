"""Cola de publicación automática con intervalo mínimo entre anuncios.

«Publica 10 canapés» crea una cola:

    anuncio 1 → (imagen) → publicar → esperar ≥ intervalo → anuncio 2 → ...

REGLAS
------
* El intervalo mínimo (`minimum_publish_interval_seconds`) es de 60 segundos
  y NO se admiten valores inferiores. Es un mínimo, no un objetivo: si generar
  la imagen tarda más, se espera más.
* El intervalo se cuenta entre CUALQUIER par de publicaciones (también entre
  cuentas distintas) y también entre intentos fallidos.
* Si una publicación falla: se registra el error, el anuncio queda marcado,
  y solo se reintenta de forma automática una vez más, respetando el
  intervalo (el doble). Si Wallapop pide una verificación, la sesión ha
  caducado o limita las peticiones, la cola se PAUSA y espera al usuario:
  nunca se intenta saltar un bloqueo.
* Dos fallos seguidos pausan la cola.

PRUEBAS
-------
El reloj se puede sustituir (`clock=`) para que las pruebas no esperen 60
segundos de verdad. Solo se admite con el servicio simulado (DEMO): con
Wallapop real se usa siempre el reloj real.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from lot_bot.core.audit import AuditService
from lot_bot.database.engine import Database
from lot_bot.database.models import (
    PublishJob,
    PublishJobStatus,
    PublishTask,
    PublishTaskStatus,
    Setting,
)

logger = logging.getLogger(__name__)

MINIMUM_PUBLISH_INTERVAL_SECONDS = 60
SETTINGS_KEY = "publicacion"
#: Fotos ya publicadas (para no repetirlas nunca, en ninguna cuenta).
USED_PHOTOS_KEY = "fotos_usadas"
NO_PHOTOS_MESSAGE = (
    "Añade fotos nuevas: ya se han publicado todas las fotos marcadas del Anuncio "
    "principal y no se repite ninguna. Añádelas en «Anuncio principal» → «Fotografías…» "
    "(márcalas con «Usar / no usar al publicar») y pulsa «Reanudar»."
)
DEFAULT_PUBLISH_SETTINGS: dict[str, Any] = {
    "minimum_publish_interval_seconds": MINIMUM_PUBLISH_INTERVAL_SECONDS,
    "generate_images": True,
    # Mis fotos: False = todas las marcadas en cada anuncio; True = una
    # distinta en cada anuncio, rotando entre las marcadas.
    "rotate_photos": False,
    "automatic_retries": 0,  # «una vez» es una vez: sin reintentos automáticos
    # Máximo de anuncios por cuenta (0 = sin límite). Al llegar, se eliminan
    # los MÁS ANTIGUOS para dejar sitio al nuevo.
    "max_listings_per_account": 0,
}

#: Solo lo que tiene que hacer una PERSONA pausa la cola y pide «Continuar»
#: (verificación, iniciar sesión, cerrar Chrome, claves...). Nunca se salta.
PAUSING_ERRORS = {
    "VerificationRequiredError",
    "AuthenticationError",
    "AuthorizationError",
    "RateLimitError",
    "NotAvailableWithCurrentAccessError",
    "ConfigurationError",
    "NO_API_KEY",
    "INVALID_KEY",
    "NO_CREDITS",
    "ProfileInUseError",
    "BrowserUnavailable",
    "PublishCancelledError",
}
#: Fallos de UN anuncio (un paso del formulario, un dato, sin confirmación):
#: ese anuncio queda marcado y la cola SIGUE sola con el siguiente.
SKIPPING_ERRORS = {
    "FormMismatchError",
    "ImageUploadError",
    "BrowserStepError",
    "RESULTADO_NO_CONFIRMADO",
    # Se cerró la ventana de una cuenta: ese anuncio no se repite, pero la
    # cadena sigue (para el siguiente se abre otra ventana).
    "WindowClosedError",
}
#: Errores que no se arreglan reintentando.
NON_RETRYABLE = {
    "CALIDAD_INSUFICIENTE",
    "DUPLICATE",
    "RESULTADO_NO_CONFIRMADO",
    "WindowClosedError",
    "PublishCancelledError",
}
UNCONFIRMED_CODE = "RESULTADO_NO_CONFIRMADO"
RELOGIN_CODE = "INICIAR_SESION"
#: Máximo que se espera a que el usuario complete una verificación y pulse
#: «Continuar» antes de dejar la cola en pausa.
USER_GATE_TIMEOUT_S = 1800.0
#: Seguridad: si fallan tantos anuncios SEGUIDOS, algo ha cambiado en la web
#: y no tiene sentido seguir gastando intentos: se pausa y se avisa.
MAX_CONSECUTIVE_FAILURES = 3


def validate_interval(seconds: Any) -> int:
    try:
        value = int(seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("El intervalo debe ser un número de segundos.") from exc
    if value < MINIMUM_PUBLISH_INTERVAL_SECONDS:
        raise ValueError(
            f"El intervalo mínimo entre publicaciones no puede ser inferior a "
            f"{MINIMUM_PUBLISH_INTERVAL_SECONDS} segundos."
        )
    return value


# ---------------------------------------------------------------------------
# Reloj
# ---------------------------------------------------------------------------
class Clock:
    """Reloj real. `sleep` se interrumpe si se pide parar."""

    is_real = True

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float, interrupt: threading.Event) -> None:
        interrupt.wait(max(0.0, seconds))


class FakeClock(Clock):
    """Reloj simulado para pruebas: dormir solo avanza el tiempo."""

    is_real = False

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t = start
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float, interrupt: threading.Event) -> None:
        seconds = max(0.0, seconds)
        self.slept.append(seconds)
        self.t += seconds


# ---------------------------------------------------------------------------
@dataclass(slots=True)
class QueueProgress:
    job_id: int
    name: str
    status: str
    total: int
    published: int
    failed: int
    pending: int
    in_progress: int
    cancelled: int
    interval_seconds: int
    current_account: str | None
    last_publish_at: datetime | None
    next_allowed_at: datetime | None
    pause_reason: str | None
    is_demo: bool
    tasks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def done(self) -> int:
        return self.published + self.failed + self.cancelled

    def bar(self, width: int = 10) -> str:
        filled = int(round(width * self.published / self.total)) if self.total else 0
        return "█" * filled + "░" * (width - filled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cola": self.job_id,
            "nombre": self.name,
            "estado": self.status,
            "total": self.total,
            "publicados": self.published,
            "fallidos": self.failed,
            "pendientes": self.pending,
            "en_curso": self.in_progress,
            "cancelados": self.cancelled,
            "intervalo_segundos": self.interval_seconds,
            "cuenta_actual": self.current_account,
            "ultima_publicacion": self.last_publish_at.strftime("%H:%M:%S")
            if self.last_publish_at
            else None,
            "proxima_permitida": self.next_allowed_at.strftime("%H:%M:%S")
            if self.next_allowed_at
            else None,
            "motivo_pausa": self.pause_reason,
            "demo": self.is_demo,
        }


class PublishQueue:
    """Ejecuta las colas de publicación, de una en una y en segundo plano."""

    def __init__(
        self,
        database: Database,
        audit: AuditService,
        app: Any,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._db = database
        self._audit = audit
        self._app = app
        self.clock = clock or Clock()
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_attempt: float | None = None
        self._consecutive_failures = 0
        #: Avisos para la interfaz (se llaman desde el hilo de la cola).
        self.listeners: list[Callable[[int], None]] = []

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        with self._db.session_scope() as session:
            row = session.get(Setting, SETTINGS_KEY)
            values = {**DEFAULT_PUBLISH_SETTINGS, **((row.value or {}) if row else {})}
        # Un valor inferior guardado a mano en la base de datos no se respeta.
        try:
            values["minimum_publish_interval_seconds"] = validate_interval(
                values["minimum_publish_interval_seconds"]
            )
        except ValueError:
            values["minimum_publish_interval_seconds"] = MINIMUM_PUBLISH_INTERVAL_SECONDS
        return values

    def save_settings(self, **changes: Any) -> dict[str, Any]:
        if "minimum_publish_interval_seconds" in changes:
            changes["minimum_publish_interval_seconds"] = validate_interval(
                changes["minimum_publish_interval_seconds"]
            )
        if "automatic_retries" in changes:
            changes["automatic_retries"] = max(0, min(3, int(changes["automatic_retries"])))
        merged = {**self.settings(), **changes}
        with self._db.session_scope() as session:
            row = session.get(Setting, SETTINGS_KEY)
            if row is None:
                session.add(Setting(key=SETTINGS_KEY, value=merged))
            else:
                row.value = merged
        self._audit.record_success(
            "Configuración de la cola de publicación",
            detail=f"Intervalo mínimo: {merged['minimum_publish_interval_seconds']} s",
            actor="usuario",
        )
        return merged

    @property
    def interval(self) -> int:
        return self.settings()["minimum_publish_interval_seconds"]

    def _check_clock(self) -> None:
        """La aceleración de pruebas nunca puede llegar a Wallapop real."""
        if not self.clock.is_real and not getattr(self._app.wallapop, "is_mock", False):
            raise RuntimeError(
                "El reloj de pruebas solo se admite en modo DEMO: con Wallapop real se "
                "respeta siempre el intervalo real."
            )

    # ------------------------------------------------------------------
    # Crear colas
    # ------------------------------------------------------------------
    def enqueue_master(
        self,
        key: str | None,
        account_refs: list[str],
        copies: int | None,
        overrides: dict[str, Any] | None = None,
        *,
        generate_images: bool | None = None,
        actor: str = "usuario",
        start: bool = True,
    ) -> int:
        """Crea una cola con copias del anuncio principal. Devuelve su id."""
        master = self._app.master_ads.get(key)
        if master is None:
            raise ValueError("No existe el anuncio principal.")
        if not self._app.demo_mode:
            accounts = {a.internal_ref: a for a in self._app.accounts.list_accounts()}
            demo_only = [
                accounts[r].alias for r in account_refs if r in accounts and accounts[r].is_demo
            ]
            if demo_only:
                raise ValueError(
                    "Estas cuentas son solo de demostración y no pueden publicar en Wallapop: "
                    + ", ".join(demo_only)
                )
        refs = self._app.master_ads.distribute(account_refs, copies)
        if not refs:
            raise ValueError("No hay cuentas en las que publicar.")
        settings = self.settings()
        if generate_images is None:
            generate_images = bool(settings["generate_images"])
        title = self._app.master_ads.render(master, overrides)["title"]
        with self._db.session_scope() as session:
            job = PublishJob(
                name=f"{len(refs)} × {master.name}",
                status=PublishJobStatus.PENDING,
                interval_seconds=settings["minimum_publish_interval_seconds"],
                generate_images=generate_images,
                is_demo=bool(self._app.demo_mode),
                actor=actor,
            )
            session.add(job)
            session.flush()
            for position, ref in enumerate(refs, start=1):
                session.add(
                    PublishTask(
                        job_id=job.id,
                        position=position,
                        account_ref=ref,
                        title=title[:200],
                        payload={"master_key": master.key, "overrides": dict(overrides or {})},
                    )
                )
            job_id = job.id
        self._audit.record_success(
            "Cola de publicación creada",
            target=master.name,
            detail=f"{len(refs)} anuncio(s), intervalo mínimo "
            f"{settings['minimum_publish_interval_seconds']} s"
            + (", una imagen generada por anuncio" if generate_images else ""),
            actor=actor,
        )
        if start:
            self.start(job_id)
        return job_id

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def _set_job(self, job_id: int, status: PublishJobStatus, reason: str | None = None) -> None:
        with self._db.session_scope() as session:
            job = session.get(PublishJob, job_id)
            if job is None:
                raise ValueError(f"No existe la cola {job_id}.")
            job.status = status
            job.pause_reason = reason
            if status is PublishJobStatus.RUNNING and job.started_at is None:
                job.started_at = datetime.now()
            if status in (PublishJobStatus.COMPLETED, PublishJobStatus.CANCELLED):
                job.finished_at = datetime.now()
        self._notify(job_id)

    def start(self, job_id: int) -> None:
        self._set_job(job_id, PublishJobStatus.RUNNING)
        self._audit.record_success("Cola de publicación iniciada", detail=f"Cola {job_id}")
        self._wake.set()

    def pause(self, job_id: int, reason: str = "Pausada por el usuario.") -> None:
        self._set_job(job_id, PublishJobStatus.PAUSED, reason)
        self._audit.record_success("Cola de publicación pausada", detail=f"Cola {job_id}: {reason}")

    def resume(self, job_id: int) -> None:
        self._consecutive_failures = 0
        self._set_job(job_id, PublishJobStatus.RUNNING)
        self._audit.record_success("Cola de publicación reanudada", detail=f"Cola {job_id}")
        self._wake.set()

    def cancel(self, job_id: int) -> int:
        """Cancela lo que queda por publicar. Lo publicado no se toca."""
        with self._db.session_scope() as session:
            tasks = session.scalars(
                select(PublishTask).where(
                    PublishTask.job_id == job_id,
                    PublishTask.status.in_(
                        [
                            PublishTaskStatus.PENDING,
                            PublishTaskStatus.WAITING,
                            PublishTaskStatus.GENERATING,
                        ]
                    ),
                )
            ).all()
            for task in tasks:
                task.status = PublishTaskStatus.CANCELLED
            count = len(tasks)
        self._set_job(job_id, PublishJobStatus.CANCELLED, "Cancelada por el usuario.")
        self._audit.record_cancelled(
            "Cola de publicación cancelada", detail=f"Cola {job_id}: {count} sin publicar"
        )
        return count

    def retry_task(self, task_id: int) -> None:
        """Vuelve a poner en la cola un anuncio fallido."""
        with self._db.session_scope() as session:
            task = session.get(PublishTask, task_id)
            if task is None:
                raise ValueError(f"No existe el anuncio {task_id} de la cola.")
            if task.status is not PublishTaskStatus.FAILED:
                raise ValueError("Solo se pueden reintentar anuncios fallidos.")
            task.status = PublishTaskStatus.PENDING
            task.error = None
            task.error_code = None
            task.attempts = 0
            job_id = task.job_id
        self._consecutive_failures = 0
        self._audit.record_success("Reintento de publicación", detail=f"Anuncio {task_id}")
        self.resume(job_id)

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------
    def jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._db.session_scope() as session:
            rows = session.scalars(
                select(PublishJob).order_by(PublishJob.id.desc()).limit(limit)
            ).all()
            return [{"id": j.id, "nombre": j.name, "estado": j.status.value} for j in rows]

    def published_listing_ids(self, job_id: int) -> list[int]:
        """Anuncios locales creados por esta cola (para «este anuncio»)."""
        from lot_bot.database.models import Account, Listing

        with self._db.session_scope() as session:
            pairs = session.execute(
                select(PublishTask.account_ref, PublishTask.remote_id).where(
                    PublishTask.job_id == job_id,
                    PublishTask.status == PublishTaskStatus.PUBLISHED,
                    PublishTask.remote_id.is_not(None),
                )
            ).all()
            ids: list[int] = []
            for ref, remote_id in pairs:
                listing_id = session.scalar(
                    select(Listing.id)
                    .join(Account, Listing.account_id == Account.id)
                    .where(Account.internal_ref == ref, Listing.wallapop_item_id == remote_id)
                )
                if listing_id is not None:
                    ids.append(listing_id)
            return ids

    def latest_job_id(self) -> int | None:
        with self._db.session_scope() as session:
            return session.scalar(select(func.max(PublishJob.id)))

    def _last_publish_ts(self) -> float | None:
        """Último intento de publicación (en memoria o en la base de datos)."""
        if self._last_attempt is not None:
            return self._last_attempt
        with self._db.session_scope() as session:
            last = session.scalar(select(func.max(PublishTask.started_at)))
        if last is None:
            return None
        # Solo cuenta para el reloj real: las fechas guardadas son reales.
        return last.timestamp() if self.clock.is_real else None

    def next_allowed_ts(self, job_interval: int | None = None, attempts: int = 0) -> float:
        interval = max(job_interval or self.interval, MINIMUM_PUBLISH_INTERVAL_SECONDS)
        if attempts > 0:
            interval *= 2 ** min(attempts, 3)
        last = self._last_publish_ts()
        return self.clock.now() if last is None else max(self.clock.now(), last + interval)

    def progress(self, job_id: int | None = None) -> QueueProgress | None:
        job_id = job_id or self.latest_job_id()
        if job_id is None:
            return None
        aliases = {a.internal_ref: a.alias for a in self._app.accounts.list_accounts()}
        with self._db.session_scope() as session:
            job = session.get(PublishJob, job_id)
            if job is None:
                return None
            tasks = list(job.tasks)
            counts = {status: 0 for status in PublishTaskStatus}
            for task in tasks:
                counts[task.status] += 1
            current = next(
                (
                    t
                    for t in tasks
                    if t.status
                    in (
                        PublishTaskStatus.GENERATING,
                        PublishTaskStatus.WAITING,
                        PublishTaskStatus.PUBLISHING,
                    )
                ),
                None,
            ) or next((t for t in tasks if t.status is PublishTaskStatus.PENDING), None)
            counts_unconfirmed = counts[PublishTaskStatus.UNCONFIRMED]
            published_times = [t.published_at for t in tasks if t.published_at]
            last_attempt = self._last_publish_ts()
            next_allowed = None
            if job.status is PublishJobStatus.RUNNING and last_attempt is not None:
                next_allowed = datetime.fromtimestamp(last_attempt + job.interval_seconds)
            return QueueProgress(
                job_id=job.id,
                name=job.name,
                status=job.status.value,
                total=len(tasks),
                published=counts[PublishTaskStatus.PUBLISHED],
                failed=counts[PublishTaskStatus.FAILED] + counts_unconfirmed,
                pending=counts[PublishTaskStatus.PENDING],
                in_progress=counts[PublishTaskStatus.GENERATING]
                + counts[PublishTaskStatus.WAITING]
                + counts[PublishTaskStatus.PUBLISHING],
                cancelled=counts[PublishTaskStatus.CANCELLED],
                interval_seconds=job.interval_seconds,
                current_account=aliases.get(current.account_ref, current.account_ref)
                if current
                else None,
                last_publish_at=max(published_times) if published_times else None,
                next_allowed_at=next_allowed,
                pause_reason=job.pause_reason,
                is_demo=job.is_demo,
                tasks=[
                    {
                        "id": t.id,
                        "posicion": t.position,
                        "cuenta": aliases.get(t.account_ref, t.account_ref),
                        "cuenta_ref": t.account_ref,
                        "anuncio": t.title,
                        "estado": t.status.value,
                        "intentos": t.attempts,
                        "inicio": t.started_at,
                        "publicado": t.published_at,
                        "id_wallapop": t.remote_id,
                        "url": t.remote_url,
                        "imagen": t.image_path,
                        "error": t.error,
                        "codigo_error": t.error_code,
                    }
                    for t in tasks
                ],
            )

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------
    def start_worker(self) -> None:
        """Arranca el hilo que procesa las colas (en la aplicación real)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        # Una cola que estaba «en marcha» al cerrar el programa se deja en
        # pausa: el usuario decide si sigue.
        with self._db.session_scope() as session:
            for job in session.scalars(
                select(PublishJob).where(PublishJob.status == PublishJobStatus.RUNNING)
            ).all():
                job.status = PublishJobStatus.PAUSED
                job.pause_reason = "LOT Bot se cerró con la cola en marcha. Pulsa «Reanudar»."
            for task in session.scalars(
                select(PublishTask).where(
                    PublishTask.status.in_(
                        [
                            PublishTaskStatus.GENERATING,
                            PublishTaskStatus.WAITING,
                            PublishTaskStatus.PUBLISHING,
                        ]
                    )
                )
            ).all():
                task.status = PublishTaskStatus.PENDING
        self._thread = threading.Thread(target=self._loop, name="lotbot-cola", daemon=True)
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
                logger.exception("Error inesperado en la cola de publicación")
                worked = False
            if not worked:
                self._wake.wait(2.0)
                self._wake.clear()

    def _active_job(self) -> int | None:
        with self._db.session_scope() as session:
            return session.scalar(
                select(PublishJob.id)
                .where(PublishJob.status == PublishJobStatus.RUNNING)
                .order_by(PublishJob.id)
                .limit(1)
            )

    def _job_status(self, job_id: int) -> PublishJobStatus:
        with self._db.session_scope() as session:
            return session.get(PublishJob, job_id).status

    def run_until_idle(self, max_steps: int = 1000) -> int:
        """Procesa todo lo pendiente sin hilo (pruebas). Devuelve pasos."""
        steps = 0
        while steps < max_steps and self.run_once():
            steps += 1
        return steps

    def run_once(self) -> bool:
        """Procesa el siguiente anuncio de la cola activa. False si no hay nada."""
        with self._lock:
            job_id = self._active_job()
            if job_id is None:
                return False
            self._check_clock()
            with self._db.session_scope() as session:
                job = session.get(PublishJob, job_id)
                pending = session.scalars(
                    select(PublishTask)
                    .where(
                        PublishTask.job_id == job_id,
                        PublishTask.status == PublishTaskStatus.PENDING,
                    )
                    .order_by(PublishTask.position)
                ).all()
                # Las cuentas no conectadas (p. ej. desconectadas por el usuario) esperan.
                task = next((t for t in pending if self._account_usable(t.account_ref)), None)
                blocked = sorted({t.account_ref for t in pending}) if pending and task is None else []
                if blocked:
                    job_done = False
                elif task is None:
                    job_done = True
                else:
                    job_done = False
                    task_id, ref, attempts = task.id, task.account_ref, task.attempts
                    payload = dict(task.payload or {})
                    image_path = task.image_path
                    generate = job.generate_images
                    interval = job.interval_seconds
                    actor = job.actor
                    position = task.position
            if blocked:
                aliases = {a.internal_ref: a.alias for a in self._app.accounts.list_accounts()}
                names = ", ".join(aliases.get(r, r) for r in blocked)
                self.pause(
                    job_id,
                    f"Estas cuentas no están conectadas en LOT Bot: {names}. Conéctalas en "
                    "Cuentas; al comprobarse la sesión la cola continúa sola.",
                )
                return True
            if job_done:
                self._set_job(job_id, PublishJobStatus.COMPLETED)
                self._audit.record_success(
                    "Cola de publicación terminada", detail=self.progress(job_id).name
                )
                return True

            # 1. Imagen única para este anuncio.
            if generate and not image_path:
                self._set_task(task_id, status=PublishTaskStatus.GENERATING)
                image_path = self._generate_image(task_id, job_id, position, ref, payload)
                if image_path is None:
                    return True

            # 2. Esperar el intervalo mínimo (desde cualquier publicación anterior).
            #    Mientras tanto se hace sitio (si la cuenta está al máximo).
            self._set_task(task_id, status=PublishTaskStatus.WAITING)
            service = getattr(self._app, "wallapop", None)
            if service is not None and hasattr(service, "user_gate"):
                service.user_gate = self._user_gate
            self._current_job = job_id
            self._make_room(job_id, ref)
            wait = self.next_allowed_ts(interval, attempts) - self.clock.now()
            while wait > 0:
                self.clock.sleep(min(wait, 1.0), self._stop)
                if self._stop.is_set():
                    self._set_task(task_id, status=PublishTaskStatus.PENDING)
                    return False
                if self._job_status(job_id) is not PublishJobStatus.RUNNING:
                    status = self._job_status(job_id)
                    if status is PublishJobStatus.PAUSED:
                        self._set_task(task_id, status=PublishTaskStatus.PENDING)
                    return True
                wait = self.next_allowed_ts(interval, attempts) - self.clock.now()

            # 3. Publicar.
            self._set_task(
                task_id,
                status=PublishTaskStatus.PUBLISHING,
                started_at=datetime.fromtimestamp(self.clock.now()),
                attempts=attempts + 1,
            )
            self._last_attempt = self.clock.now()
            self._current_job = job_id
            service = getattr(self._app, "wallapop", None)
            if service is not None and hasattr(service, "user_gate"):
                service.user_gate = self._user_gate
            only_images = None
            photo_key = None
            if not image_path and self.settings().get("rotate_photos"):
                photo = self._next_rotating_photo(payload.get("master_key"))
                if photo is None:
                    # No quedan fotos sin publicar: se para y se avisa.
                    self._set_task(task_id, status=PublishTaskStatus.PENDING, attempts=attempts)
                    self.pause(job_id, NO_PHOTOS_MESSAGE)
                    return True
                only_images = [photo["path"]]
                photo_key = self.photo_key(photo)
            try:
                outcome = self._app.master_ads.publish_single(
                    payload.get("master_key"),
                    ref,
                    payload.get("overrides") or {},
                    extra_images=[image_path] if image_path else None,
                    only_images=only_images,
                    meta={"imagen": payload.get("imagen") or {"origen": "plantilla"}},
                    confirmed=True,
                    actor=actor,
                )
            except Exception as exc:  # errores de configuración, capacidad...
                code = type(exc).__name__
                message = getattr(exc, "user_message", None) or str(exc)
                self._fail(task_id, job_id, attempts + 1, code, message)
                return True

            if outcome.success:
                self._consecutive_failures = 0
                if photo_key:
                    self.mark_photo_used(photo_key, ref)
                self._set_task(
                    task_id,
                    status=PublishTaskStatus.PUBLISHED,
                    published_at=datetime.fromtimestamp(self.clock.now()),
                    remote_id=outcome.item_id,
                    remote_url=outcome.url,
                    error=None,
                    error_code=None,
                )
            elif outcome.error_code == UNCONFIRMED_CODE and self._appears_in_catalog(task_id, ref):
                # Wallapop no lo confirmó en pantalla, pero está en «Tus productos».
                self._consecutive_failures = 0
                self._set_task(
                    task_id,
                    status=PublishTaskStatus.PUBLISHED,
                    published_at=datetime.fromtimestamp(self.clock.now()),
                    error=None,
                    error_code=None,
                )
                if photo_key:
                    self.mark_photo_used(photo_key, ref)
            else:
                self._fail(
                    task_id, job_id, attempts + 1, outcome.error_code or "ERROR", outcome.message
                )
            return True

    #: Espera entre borrados para hacer sitio (no son publicaciones).
    ROOM_DELETE_WAIT_S = 8.0

    def _make_room(self, job_id: int, account_ref: str) -> None:
        """Hace sitio si hace falta. NUNCA para la cola: si algo falla (p. ej.
        no se puede leer «Tus productos»), no se borra nada y se publica igual."""
        try:
            self._make_room_unsafe(job_id, account_ref)
        except Exception as exc:
            logger.warning("Límite de anuncios de %s: no se ha podido comprobar (%s). "
                           "No se borra nada.", account_ref, exc)

    def _make_room_unsafe(self, job_id: int, account_ref: str) -> None:
        """Si la cuenta ha llegado al máximo de anuncios, elimina los MÁS
        ANTIGUOS (los que hagan falta) antes de publicar el nuevo. Nunca toca
        nada si el límite está desactivado (0)."""
        limit = int(self.settings().get("max_listings_per_account") or 0)
        listings = getattr(self._app, "listings", None)
        publishing = getattr(self._app, "publishing", None)
        if limit <= 0 or listings is None or publishing is None:
            return
        from lot_bot.publishing.listings import ListingFilter

        # La primera vez en esta cola se lee la lista real de la cuenta (solo
        # se mira la lista «Tus productos»; no se entra en ningún anuncio).
        synced = self.__dict__.setdefault("_room_synced", set())
        if (job_id, account_ref) not in synced:
            synced.add((job_id, account_ref))
            try:
                listings.sync_account(account_ref)
            except Exception as exc:
                # Sin la lista real no se sabe cuántos hay: no se borra a ciegas.
                synced.discard((job_id, account_ref))
                logger.warning("No se ha podido leer la lista de %s: %s", account_ref, exc)
                return
        active = [
            v for v in listings.search(ListingFilter(account_ref=account_ref, limit=5000))
            if v.status == "active"
        ]
        excess = len(active) - limit + 1
        if excess <= 0:
            return
        # Más antiguos primero: fecha de publicación (sin fecha = más antiguo).
        # Sin fecha (leídos de «Tus productos», que va de más nuevo a más
        # antiguo): cuanto más abajo en la lista (id mayor), más antiguo.
        oldest = sorted(
            active,
            key=lambda v: (
                v.published_at is not None,
                v.published_at or datetime.min,
                -v.id if v.published_at is None else v.id,
            ),
        )[:excess]
        logger.info(
            "%s tiene %d anuncios (máximo %d): se eliminan %d antiguos.",
            account_ref, len(active), limit, len(oldest),
        )
        outcomes = publishing.delete_listings(
            [v.id for v in oldest],
            confirmed=True,
            actor="LOT Bot (límite de anuncios)",
            interval_seconds=self.ROOM_DELETE_WAIT_S,
            sleep=lambda s: self.clock.sleep(s, self._stop),
        )
        failed = [o for o in outcomes if not o.success]
        self._audit.record_success(
            "Sitio hecho para publicar",
            account_ref=account_ref,
            detail=f"Eliminados {len(outcomes) - len(failed)} anuncio(s) antiguos (máximo {limit}).",
        )
        if failed:
            logger.warning("No se han podido eliminar %d antiguos: %s", len(failed), failed[0].message)

    def _appears_in_catalog(self, task_id: int, account_ref: str) -> bool:
        """¿El anuncio sin confirmar está en la lista «Tus productos»? Se
        compara cuántos hay con ese título en Wallapop y cuántos conoce LOT
        Bot: si Wallapop tiene uno más, se publicó (y se registra)."""
        from lot_bot.publishing.listings import ListingFilter, _norm_text
        from lot_bot.wallapop.capabilities import Capability

        service = getattr(self._app, "wallapop", None)
        listings = getattr(self._app, "listings", None)
        if service is None or listings is None:
            return False
        try:
            if Capability.LIST_ITEMS not in service.capabilities():
                return False
            with self._db.session_scope() as session:
                title = session.get(PublishTask, task_id).title or ""
            wanted = _norm_text(title)
            if not wanted:
                return False
            remote = sum(
                1 for item in service.list_items(account_ref, limit=500)
                if wanted in _norm_text(item.raw.get("texto") or item.title)
            )
            local = sum(
                1 for v in listings.search(ListingFilter(account_ref=account_ref, limit=5000))
                if v.status != "removed" and _norm_text(v.title) == wanted
            )
            if remote <= local:
                return False
            listings.sync_account(account_ref)  # queda en «Anuncios» con su dirección
            return True
        except Exception as exc:  # comprobar nunca rompe la cola
            logger.warning("No se ha podido comprobar en «Tus productos»: %s", exc)
            return False

    # ------------------------------------------------------------------
    def _generate_image(
        self, task_id: int, job_id: int, position: int, ref: str, payload: dict[str, Any]
    ) -> str | None:
        from lot_bot.images.generation import GenerationError, spec_from_master

        master = self._app.master_ads.get(payload.get("master_key"))
        overrides = payload.get("overrides") or {}
        spec = spec_from_master(master, size=overrides.get("medida"))
        try:
            result = self._app.image_generation.generate_unique(
                spec,
                variation=job_id * 17 + position,
                subject=f"Cola {job_id} · anuncio {position} · {master.name}",
                account_ref=ref,
                task_id=task_id,
                preferred_rooms=self._preferred_rooms(),
            )
        except GenerationError as exc:
            self._fail(task_id, job_id, 1, exc.code, exc.user_message, allow_retry=exc.code != "DUPLICATE")
            return None
        except Exception as exc:
            self._fail(task_id, job_id, 1, type(exc).__name__, str(exc))
            return None
        # Se guarda qué escena se usó: servirá para analizar qué funciona mejor.
        payload["imagen"] = {
            "id": result.image_id,
            "escena": dict(result.scene),
            "proveedor": result.provider,
            "operacion": result.operation,
            "demo": result.is_demo,
        }
        self._set_task(task_id, image_path=str(result.path), payload=dict(payload))
        self._audit.record_success(
            "Imagen generada para anuncio",
            account_ref=ref,
            target=master.name,
            detail=f"{result.provider}; intento {result.attempts}; {result.path.name}",
        )
        return str(result.path)

    # ------------------------------------------------------------------
    # Fotos ya publicadas: NUNCA se repiten (en ninguna cuenta)
    # ------------------------------------------------------------------
    @staticmethod
    def photo_key(image: dict[str, Any]) -> str:
        """Identifica la foto por su contenido (si se conoce) o por su ruta."""
        return str(image.get("content_hash") or image.get("path") or "")

    def used_photos(self) -> dict[str, dict[str, Any]]:
        """{foto: {"cuentas": [...], "fecha": ...}} de las fotos ya publicadas."""
        with self._db.session_scope() as session:
            row = session.get(Setting, USED_PHOTOS_KEY)
            return dict((row.value or {}) if row else {})

    def _save_used(self, used: dict[str, dict[str, Any]]) -> None:
        with self._db.session_scope() as session:
            row = session.get(Setting, USED_PHOTOS_KEY)
            if row is None:
                session.add(Setting(key=USED_PHOTOS_KEY, value=used))
            else:
                row.value = used

    def mark_photo_used(self, key: str, account_ref: str) -> None:
        used = self.used_photos()
        entry = dict(used.get(key) or {"cuentas": []})
        entry["cuentas"] = sorted({*entry.get("cuentas", []), account_ref})
        entry["fecha"] = datetime.fromtimestamp(self.clock.now()).isoformat(timespec="seconds")
        used[key] = entry
        self._save_used(used)

    def reset_used_photos(self) -> None:
        """Permite volver a usar todas las fotos (lo decide el usuario)."""
        self._save_used({})
        self._audit.record_success("Fotos publicadas reiniciadas", actor="usuario")

    def unused_photos(self, master_key: str | None) -> list[dict[str, Any]]:
        """Fotos marcadas del anuncio principal que aún no se han publicado."""
        master = self._app.master_ads.get(master_key)
        if master is None:
            return []
        used = self.used_photos()
        return [
            i for i in self._app.master_ads.effective_images(master)
            if i.get("source") != "demo" and self.photo_key(i) not in used
        ]

    def _next_rotating_photo(self, master_key: str | None) -> dict[str, Any] | None:
        """La primera foto marcada que todavía no se ha publicado en NINGUNA
        cuenta (en el orden de «Fotografías…»)."""
        unused = self.unused_photos(master_key)
        return unused[0] if unused else None

    def _preferred_rooms(self) -> list[str]:
        """Habitaciones que mejor han funcionado (si hay datos suficientes)."""
        optimizer = getattr(self._app, "optimizer", None)
        if optimizer is None:
            return []
        try:
            return optimizer.preferred_rooms()
        except Exception:  # la optimización nunca debe parar la cola
            logger.debug("Optimizador no disponible", exc_info=True)
            return []

    def _set_task(self, task_id: int, **fields: Any) -> None:
        with self._db.session_scope() as session:
            task = session.get(PublishTask, task_id)
            for key, value in fields.items():
                setattr(task, key, value)
            job_id = task.job_id
        self._notify(job_id)

    def _fail(
        self,
        task_id: int,
        job_id: int,
        attempts: int,
        code: str,
        message: str,
        *,
        allow_retry: bool = True,
    ) -> None:
        if code == "AuthenticationError":
            self._session_expired(task_id, job_id, attempts, message)
            return
        settings = self.settings()
        pausing = code in PAUSING_ERRORS
        retry = (
            allow_retry
            and not pausing
            and code not in NON_RETRYABLE
            and attempts <= int(settings["automatic_retries"])
        )
        with self._db.session_scope() as session:
            task = session.get(PublishTask, task_id)
            task.attempts = attempts
            task.error = message[:1000]
            task.error_code = code[:80]
            if code == UNCONFIRMED_CODE:
                task.status = PublishTaskStatus.UNCONFIRMED
            else:
                task.status = PublishTaskStatus.PENDING if retry else PublishTaskStatus.FAILED
            ref, title = task.account_ref, task.title
        self._audit.record_error(
            "Publicación en cola fallida" + (" (se reintentará)" if retry else ""),
            error=f"{code}: {message}",
            account_ref=ref,
            target=title,
        )
        self._consecutive_failures += 1
        if pausing:
            self.pause(job_id, message)
        elif self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self.pause(
                job_id,
                f"Han fallado {self._consecutive_failures} anuncios seguidos (el último: "
                f"{message[:200]}). Puede que Wallapop haya cambiado algo: revisa el error "
                "y pulsa «Continuar».",
            )
        elif code in SKIPPING_ERRORS:
            logger.info("Anuncio no publicado (%s); la cola sigue con el siguiente.", code)
        self._notify(job_id)

    def _user_gate(self, account_ref: str, message: str) -> bool:
        """Deja la cola en pausa con `message` y espera a «Continuar».

        La llama el navegador cuando Wallapop pide una verificación (o la
        ventana de la cuenta sigue abierta). El navegador se queda abierto y,
        al pulsar «Continuar» (= Reanudar), la publicación sigue desde el
        mismo paso. True = continuar; False = cancelada o tiempo agotado.
        """
        job_id = getattr(self, "_current_job", None)
        if job_id is None:
            return False
        aliases = {a.internal_ref: a.alias for a in self._app.accounts.list_accounts()}
        self.pause(job_id, f"{aliases.get(account_ref, account_ref)}: {message}")
        deadline = time.monotonic() + USER_GATE_TIMEOUT_S
        while time.monotonic() < deadline and not self._stop.is_set():
            status = self._job_status(job_id)
            if status is PublishJobStatus.RUNNING:
                return True
            if status in (PublishJobStatus.CANCELLED, PublishJobStatus.COMPLETED):
                return False
            self._stop.wait(0.3)
        return False

    def account_reconnected(self, account_ref: str) -> list[int]:
        """Tras «Reconectar» una cuenta cuya sesión caducó, las colas que
        esperaban por ella continúan solas. Devuelve las colas reanudadas."""
        resumed: list[int] = []
        with self._db.session_scope() as session:
            jobs = session.scalars(
                select(PublishJob).where(PublishJob.status == PublishJobStatus.PAUSED)
            ).all()
            candidates = []
            for job in jobs:
                waiting = session.scalars(
                    select(PublishTask).where(
                        PublishTask.job_id == job.id,
                        PublishTask.account_ref == account_ref,
                        PublishTask.status == PublishTaskStatus.PENDING,
                        PublishTask.error_code == RELOGIN_CODE,
                    )
                ).first()
                if waiting is not None:
                    candidates.append(job.id)
        for job_id in candidates:
            self.resume(job_id)
            resumed.append(job_id)
        return resumed

    def _account_usable(self, ref: str) -> bool:
        from lot_bot.database.models import AccountStatus

        info = self._app.accounts.get_account(ref)
        return info is not None and info.status == AccountStatus.CONNECTED

    def _session_expired(self, task_id: int, job_id: int, attempts: int, message: str) -> None:
        """Wallapop pide volver a iniciar sesión en una cuenta.

        La cuenta NO se marca como caducada (en LOT Bot las cuentas solo dejan
        de funcionar si el usuario las elimina). El anuncio vuelve a la cola y
        la cola se pausa hasta que el usuario entre y pulse «Continuar» (o
        «Reconectar», que la reanuda sola)."""
        with self._db.session_scope() as session:
            task = session.get(PublishTask, task_id)
            task.attempts = max(0, attempts - 1)  # no cuenta como intento fallido
            task.error = message[:1000]
            task.error_code = RELOGIN_CODE
            task.status = PublishTaskStatus.PENDING
            ref, title = task.account_ref, task.title
        self._audit.record_error(
            "Wallapop pide iniciar sesión de nuevo (la cuenta sigue conectada)",
            error=message,
            account_ref=ref,
            target=title,
        )
        aliases = {a.internal_ref: a.alias for a in self._app.accounts.list_accounts()}
        if "Continuar" not in message:
            message += " Inicia sesión en la ventana de la cuenta y pulsa «Continuar»."
        self.pause(job_id, f"{aliases.get(ref, ref)}: {message}")

    def _notify(self, job_id: int) -> None:
        for listener in list(self.listeners):
            try:
                listener(job_id)
            except Exception:  # la interfaz nunca debe tumbar la cola
                logger.debug("Aviso de la cola fallido", exc_info=True)
