"""Planificador de automatizaciones (APScheduler en segundo plano)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from lot_bot.automation.jobs import JOB_DEFINITIONS, JOBS_BY_KEY, JobResult
from lot_bot.core.events import TOPIC_AUTOMATION_RUN, EventBus
from lot_bot.database.engine import Database
from lot_bot.database.models import Automation, AutomationStatus

if TYPE_CHECKING:  # pragma: no cover
    from lot_bot.bootstrap import Application

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AutomationView:
    """Automatizacion tal y como se muestra en pantalla."""

    id: int
    job_key: str
    name: str
    description: str
    enabled: bool
    interval_minutes: int
    writes: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_status: str
    last_result: str | None
    last_error: str | None

    @property
    def status_label(self) -> str:
        return {
            "idle": "En espera",
            "running": "Ejecutándose",
            "ok": "Correcta",
            "failed": "Con errores",
        }.get(self.last_status, self.last_status)

    @property
    def interval_label(self) -> str:
        if self.interval_minutes < 60:
            return f"cada {self.interval_minutes} min"
        if self.interval_minutes < 1440:
            return f"cada {self.interval_minutes // 60} h"
        return f"cada {self.interval_minutes // 1440} día(s)"


class AutomationScheduler:
    """Registra, activa y ejecuta las tareas programadas."""

    def __init__(self, app: Application, database: Database, events: EventBus) -> None:
        self._app = app
        self._db = database
        self._events = events
        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._started = False

    # ------------------------------------------------------------------
    def ensure_definitions(self) -> None:
        """Crea en la base de datos las automatizaciones que falten."""
        with self._db.session_scope() as session:
            existing = {
                key for (key,) in session.execute(select(Automation.job_key)).all()
            }
            for definition in JOB_DEFINITIONS:
                if definition.key in existing:
                    continue
                session.add(
                    Automation(
                        job_key=definition.key,
                        name=definition.name,
                        description=definition.description,
                        enabled=False,
                        interval_minutes=definition.default_interval_minutes,
                        requires_confirmation=definition.writes,
                    )
                )

    def start(self) -> None:
        if self._started:
            return
        self.ensure_definitions()
        self._scheduler.start()
        self._started = True
        for view in self.list_automations():
            if view.enabled:
                self._schedule(view.job_key, view.interval_minutes)
        logger.info("Planificador de automatizaciones iniciado.")

    def shutdown(self) -> None:
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("Planificador detenido.")

    # ------------------------------------------------------------------
    def list_automations(self) -> list[AutomationView]:
        with self._db.session_scope() as session:
            rows = session.scalars(select(Automation).order_by(Automation.name)).all()
            return [self._to_view(row) for row in rows]

    @staticmethod
    def _to_view(row: Automation) -> AutomationView:
        definition = JOBS_BY_KEY.get(row.job_key)
        return AutomationView(
            id=row.id,
            job_key=row.job_key,
            name=row.name,
            description=row.description or (definition.description if definition else ""),
            enabled=row.enabled,
            interval_minutes=row.interval_minutes,
            writes=definition.writes if definition else False,
            last_run_at=row.last_run_at,
            next_run_at=row.next_run_at,
            last_status=row.last_status.value,
            last_result=row.last_result,
            last_error=row.last_error,
        )

    # ------------------------------------------------------------------
    def set_enabled(self, job_key: str, enabled: bool) -> AutomationView | None:
        with self._db.session_scope() as session:
            row = session.scalar(select(Automation).where(Automation.job_key == job_key))
            if row is None:
                return None
            row.enabled = enabled
            row.next_run_at = (
                datetime.now(UTC).replace(tzinfo=None)
                + timedelta(minutes=row.interval_minutes)
                if enabled
                else None
            )
            interval = row.interval_minutes
            view = self._to_view(row)

        if enabled:
            self._schedule(job_key, interval)
        else:
            self._unschedule(job_key)
        logger.info("Automatización '%s' %s.", job_key, "activada" if enabled else "desactivada")
        return view

    def set_interval(self, job_key: str, minutes: int) -> AutomationView | None:
        minutes = max(1, int(minutes))
        with self._db.session_scope() as session:
            row = session.scalar(select(Automation).where(Automation.job_key == job_key))
            if row is None:
                return None
            row.interval_minutes = minutes
            enabled = row.enabled
            view = self._to_view(row)
        if enabled:
            self._schedule(job_key, minutes)
        return view

    def _schedule(self, job_key: str, minutes: int) -> None:
        if not self._started:
            return
        self._unschedule(job_key)
        self._scheduler.add_job(
            self.run_now,
            trigger=IntervalTrigger(minutes=max(1, minutes)),
            args=[job_key],
            id=f"lot-bot:{job_key}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    def _unschedule(self, job_key: str) -> None:
        try:
            self._scheduler.remove_job(f"lot-bot:{job_key}")
        except Exception:  # el trabajo no estaba programado
            pass

    # ------------------------------------------------------------------
    def run_now(self, job_key: str) -> JobResult:
        """Ejecuta una automatizacion inmediatamente."""
        definition = JOBS_BY_KEY.get(job_key)
        if definition is None:
            return JobResult(False, f"La automatización '{job_key}' no existe.")

        options = self._mark_running(job_key)
        logger.info("Ejecutando automatización '%s'.", job_key)
        try:
            result = definition.run(self._app, options)
        except Exception as exc:  # una tarea nunca debe tumbar la aplicacion
            logger.exception("Error en la automatización '%s'", job_key)
            result = JobResult(False, f"Error inesperado: {exc}")
            self._app.audit.record_error(
                f"Automatización: {definition.name}", error=str(exc), actor="automatizacion"
            )
        else:
            self._app.audit.record(
                f"Automatización: {definition.name}",
                actor="automatizacion",
                result="ok" if result.ok else "error",
                detail=result.summary,
                error=None if result.ok else result.summary,
            )

        self._store_result(job_key, result)
        self._events.publish(
            TOPIC_AUTOMATION_RUN,
            {"job_key": job_key, "ok": result.ok, "summary": result.summary},
        )
        return result

    def _mark_running(self, job_key: str) -> dict[str, Any]:
        with self._db.session_scope() as session:
            row = session.scalar(select(Automation).where(Automation.job_key == job_key))
            if row is None:
                return {}
            row.last_status = AutomationStatus.RUNNING
            row.last_run_at = datetime.now(UTC).replace(tzinfo=None)
            return dict(row.options or {})

    def _store_result(self, job_key: str, result: JobResult) -> None:
        with self._db.session_scope() as session:
            row = session.scalar(select(Automation).where(Automation.job_key == job_key))
            if row is None:
                return
            row.last_status = AutomationStatus.OK if result.ok else AutomationStatus.FAILED
            row.last_result = result.summary[:1000]
            row.last_error = None if result.ok else result.summary[:1000]
            row.next_run_at = (
                datetime.now(UTC).replace(tzinfo=None)
                + timedelta(minutes=row.interval_minutes)
                if row.enabled
                else None
            )
