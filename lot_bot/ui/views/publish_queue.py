"""Pantalla «Publicación automática»: progreso de la cola en tiempo real."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton

from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import (
    Card,
    SectionTitle,
    ask_confirmation,
    build_table,
    fill_table,
    selected_row_data,
    show_error,
)

STATUS_LABELS = {
    "pending": "○ Pendiente",
    "generating": "⏳ Generando imagen",
    "waiting": "⏳ En espera",
    "publishing": "⏳ Publicando",
    "published": "✓ Publicado",
    "unconfirmed": "? Resultado no confirmado",
    "failed": "✗ Fallido",
    "cancelled": "— Cancelado",
}
JOB_LABELS = {
    "pending": "Preparada",
    "running": "En marcha",
    "paused": "En pausa",
    "completed": "Terminada",
    "cancelled": "Cancelada",
}
STATUS_COLORS = {
    "unconfirmed": theme.WARNING,
    "published": theme.SUCCESS,
    "failed": theme.DANGER,
    "cancelled": theme.TEXT_MUTED,
    "waiting": theme.WARNING,
    "generating": theme.WARNING,
    "publishing": theme.WARNING,
}


class PublishQueueView(BaseView):
    title = "Publicación automática"
    subtitle = "Los anuncios se publican de uno en uno, con un intervalo mínimo entre ellos"

    def build(self) -> None:
        self.add_header_button("Actualizar", self.refresh)
        self._job_id: int | None = None

        card = Card()
        self.summary_title = SectionTitle("No hay ninguna cola")
        card.add(self.summary_title)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        card.add(self.progress_bar)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        card.add(self.summary)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Iniciar")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self._start)
        controls.addWidget(self.start_button)
        self.pause_button = QPushButton("Pausar")
        self.pause_button.clicked.connect(self._pause)
        controls.addWidget(self.pause_button)
        self.resume_button = QPushButton("Continuar")
        self.resume_button.clicked.connect(self._resume)
        controls.addWidget(self.resume_button)
        self.cancel_button = QPushButton("Cancelar cola")
        self.cancel_button.setObjectName("Danger")
        self.cancel_button.clicked.connect(self._cancel)
        controls.addWidget(self.cancel_button)
        controls.addStretch(1)
        card.body.addLayout(controls)
        self.body.addWidget(card)

        tasks = Card()
        tasks.add(SectionTitle("Anuncios de la cola"))
        self.table = build_table(
            ["#", "Cuenta", "Anuncio", "Estado", "Inicio", "Publicado", "Resultado / error"]
        )
        tasks.add(self.table)
        row = QHBoxLayout()
        self.retry_button = QPushButton("Reintentar anuncio fallido")
        self.retry_button.clicked.connect(self._retry)
        row.addWidget(self.retry_button)
        row.addStretch(1)
        tasks.body.addLayout(row)
        self.body.addWidget(tasks, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # ------------------------------------------------------------------
    def _tick(self) -> None:
        if self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        queue = self.app.publish_queue
        progress = queue.progress(None)
        if progress is None:
            self._job_id = None
            self.summary_title.setText("No hay ninguna cola")
            self.summary.setText(
                "Pide en el asistente, por ejemplo, «Publica 10 canapés». Verás aquí el "
                f"progreso. Intervalo mínimo actual: {queue.interval} s."
            )
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            fill_table(self.table, [])
            self._buttons(None)
            return
        self._job_id = progress.job_id
        demo = " · MODO DEMO (simulado)" if progress.is_demo else ""
        self.summary_title.setText(
            f"{progress.name} — {JOB_LABELS.get(progress.status, progress.status)}{demo}"
        )
        self.progress_bar.setRange(0, max(progress.total, 1))
        self.progress_bar.setValue(progress.published)
        self.progress_bar.setFormat(f"{progress.published}/{progress.total}")
        lines = [
            f"{progress.total} anuncios preparados · intervalo mínimo {progress.interval_seconds} s",
            f"Cuenta: {progress.current_account or '—'}",
            "Última publicación: "
            + (f"{progress.last_publish_at:%H:%M:%S}" if progress.last_publish_at else "—"),
            "Próxima publicación permitida: "
            + (f"{progress.next_allowed_at:%H:%M:%S}" if progress.next_allowed_at else "—"),
            f"✓ {progress.published} publicados · ⏳ {progress.in_progress} en espera · "
            f"○ {progress.pending} pendientes · ✗ {progress.failed} fallidos",
        ]
        if progress.status == "paused" and progress.pause_reason:
            lines.append(f"<b style='color:{theme.WARNING}'>En pausa:</b> {progress.pause_reason}")
        self.summary.setText("<br>".join(lines))
        tasks = progress.tasks
        fill_table(
            self.table,
            [
                [
                    t["posicion"],
                    t["cuenta"],
                    t["anuncio"],
                    STATUS_LABELS.get(t["estado"], t["estado"]),
                    f"{t['inicio']:%H:%M:%S}" if t["inicio"] else "",
                    f"{t['publicado']:%H:%M:%S}" if t["publicado"] else "",
                    t["error"] or t["url"] or t["id_wallapop"] or "",
                ]
                for t in tasks
            ],
            row_data=[t["id"] for t in tasks],
            colorizer=lambda row, col, _v: (
                STATUS_COLORS.get(tasks[row]["estado"]) if col == 3 and row < len(tasks) else None
            ),
        )
        self._buttons(progress)

    def _buttons(self, progress) -> None:
        status = progress.status if progress else None
        self.start_button.setEnabled(status == "pending")
        self.pause_button.setEnabled(status == "running")
        self.resume_button.setEnabled(status == "paused")
        self.cancel_button.setEnabled(status in ("pending", "running", "paused"))
        self.retry_button.setEnabled(bool(progress and progress.failed))

    # ------------------------------------------------------------------
    def _safe(self, action) -> None:
        try:
            action()
        except (ValueError, RuntimeError) as exc:
            show_error(self, str(exc))
        self.refresh()

    def _start(self) -> None:
        if self._job_id:
            self._safe(lambda: self.app.publish_queue.start(self._job_id))

    def _pause(self) -> None:
        if self._job_id:
            self._safe(lambda: self.app.publish_queue.pause(self._job_id))

    def _resume(self) -> None:
        if self._job_id:
            self._safe(lambda: self.app.publish_queue.resume(self._job_id))

    def _cancel(self) -> None:
        if self._job_id and ask_confirmation(
            self,
            "Cancelar cola",
            "No se publicará nada más de esta cola. Lo ya publicado no se toca.",
            destructive=True,
        ):
            self._safe(lambda: self.app.publish_queue.cancel(self._job_id))

    def _retry(self) -> None:
        selected = selected_row_data(self.table)
        progress = self.app.publish_queue.progress(self._job_id)
        failed = [t["id"] for t in progress.tasks if t["estado"] == "failed"] if progress else []
        target = next((t for t in selected if t in failed), failed[0] if failed else None)
        if target is not None:
            self._safe(lambda: self.app.publish_queue.retry_task(target))
