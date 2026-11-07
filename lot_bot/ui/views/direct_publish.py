"""Pantalla «Publicación masiva»: cola persistente del motor directo.

Añadir anuncios (uno a uno o desde un CSV), iniciar/pausar la publicación,
ver qué se ha publicado, qué ha fallado y por qué, y reintentar.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from lot_bot.database.models import AdQueueStatus
from lot_bot.integration import AdInput, EngineState
from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.views.connect_wallapop import local_time, scroll_body
from lot_bot.ui.widgets.common import (
    Card,
    SectionTitle,
    StatCard,
    ask_confirmation,
    build_table,
    fill_table,
    info_box,
    selected_row_data,
    show_error,
    spanish_buttons,
)

CONDITIONS = ["", "Nuevo", "Como nuevo", "En buen estado", "En condiciones aceptables", "Lo ha dado todo"]
FILTERS = [
    ("Todos", None),
    ("Pendientes", AdQueueStatus.PENDING),
    ("Publicados", AdQueueStatus.PUBLISHED),
    ("Con error", AdQueueStatus.FAILED),
    ("Para revisar", AdQueueStatus.NEEDS_REVIEW),
    ("Comprobando", AdQueueStatus.VERIFYING),
    ("Cancelados", AdQueueStatus.CANCELLED),
]
STATUS_COLORS = {
    "published": theme.SUCCESS,
    "failed": theme.DANGER,
    "needs_review": theme.WARNING,
    "verifying": theme.WARNING,
    "processing": theme.INFO,
    "cancelled": theme.TEXT_MUTED,
}
EVENT_LABELS = {
    "queued": "Añadido a la cola",
    "processing": "Publicando",
    "published": "Publicado",
    "retry": "Reintento programado",
    "waiting": "En espera",
    "verifying": "Comprobando",
    "verify_deferred": "Comprobación aplazada",
    "needs_review": "Para revisar",
    "failed": "Error",
    "cancelled": "Cancelado",
    "requeued": "Vuelto a la cola",
    "interrupted": "Interrumpido",
}


class AdDialog(QDialog):
    """Formulario sencillo para añadir un anuncio a la cola."""

    def __init__(self, accounts, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Añadir anuncio a la cola")
        self.setMinimumWidth(560)
        self.photos: list[str] = []
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.account = QComboBox()
        for account in accounts:
            self.account.addItem(account.alias, account.internal_ref)
        form.addRow("Cuenta", self.account)
        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(200)
        form.addRow("Título", self.title_edit)
        self.description = QPlainTextEdit()
        self.description.setMinimumHeight(110)
        form.addRow("Descripción", self.description)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 1_000_000)
        self.price.setDecimals(2)
        self.price.setSuffix(" €")
        form.addRow("Precio", self.price)
        self.category = QLineEdit()
        self.category.setPlaceholderText("Por ejemplo: Hogar y jardín")
        form.addRow("Categoría", self.category)
        self.condition = QComboBox()
        self.condition.addItems(CONDITIONS)
        form.addRow("Estado", self.condition)
        photos_row = QHBoxLayout()
        self.photos_label = QLabel("Sin fotos")
        photos_row.addWidget(self.photos_label, 1)
        choose = QPushButton("Elegir fotos…")
        choose.clicked.connect(self._choose)
        photos_row.addWidget(choose)
        form.addRow("Fotos", photos_row)
        layout.addLayout(form)
        buttons = spanish_buttons(
            QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Elige las fotos del anuncio", "", "Imágenes (*.jpg *.jpeg *.png *.webp)"
        )
        if paths:
            self.photos = paths
            self.photos_label.setText(f"{len(paths)} foto(s): " + ", ".join(Path(p).name for p in paths[:3])
                                      + ("…" if len(paths) > 3 else ""))

    def ad(self) -> AdInput:
        return AdInput(
            account_ref=self.account.currentData() or "",
            title=self.title_edit.text(),
            description=self.description.toPlainText(),
            price=self.price.value(),
            category=self.category.text().strip() or None,
            condition=self.condition.currentText() or None,
            image_paths=list(self.photos),
        )


class DirectPublishView(BaseView):
    title = "Publicación masiva"
    subtitle = "Cola de anuncios publicada por conexión directa, de uno en uno y sin navegador"

    def build(self) -> None:
        self.add_header_button("Añadir anuncio…", self._add_ad, primary=True)
        self.add_header_button("Importar CSV…", self._import_csv)
        self.add_header_button("Desde Productos", self._from_catalog)
        self.add_header_button("Actualizar", self.refresh)
        body = scroll_body(self)

        stats = QHBoxLayout()
        self.stat_pending = StatCard("Pendientes")
        self.stat_processing = StatCard("Publicando ahora")
        self.stat_published = StatCard("Publicados")
        self.stat_errors = StatCard("Errores")
        self.stat_review = StatCard("Para revisar")
        for card in (self.stat_pending, self.stat_processing, self.stat_published,
                     self.stat_errors, self.stat_review):
            stats.addWidget(card)
        body.addLayout(stats)

        control = Card()
        self.state_title = SectionTitle("Publicación")
        control.add(self.state_title)
        self.state_info = QLabel()
        # Sin ajuste de línea: con él, Qt calcula mal la altura del texto
        # enriquecido dentro de la zona desplazable y lo recorta.
        self.state_info.setWordWrap(False)
        control.add(self.state_info)
        row = QHBoxLayout()
        self.start_button = QPushButton("Iniciar publicación")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self._start)
        row.addWidget(self.start_button)
        self.pause_button = QPushButton("Pausar")
        self.pause_button.clicked.connect(self._pause)
        row.addWidget(self.pause_button)
        self.reconnect_button = QPushButton("Reconectar cuentas…")
        self.reconnect_button.setToolTip("Abre «Conectar Wallapop».")
        self.reconnect_button.clicked.connect(self._go_connect)
        row.addWidget(self.reconnect_button)
        row.addStretch(1)
        control.body.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Segundos entre anuncios"))
        self.interval = QSpinBox()
        self.interval.setRange(60, 86_400)
        self.interval.setMinimumSize(120, 36)
        row.addWidget(self.interval)
        row.addWidget(QLabel("Máx. por cuenta y día"))
        self.daily = QSpinBox()
        self.daily.setRange(0, 10_000)
        self.daily.setSpecialValueText("Sin límite")
        self.daily.setMinimumSize(140, 36)
        row.addWidget(self.daily)
        self.auto_resume = QCheckBox("Continuar sola al abrir LOT Bot")
        row.addWidget(self.auto_resume)
        save = QPushButton("Guardar ajustes")
        save.clicked.connect(self._save_settings)
        row.addWidget(save)
        row.addStretch(1)
        control.body.addLayout(row)
        body.addWidget(control)

        queue = Card()
        head = QHBoxLayout()
        head.addWidget(SectionTitle("Anuncios de la cola"))
        head.addStretch(1)
        self.filter = QComboBox()
        for label, value in FILTERS:
            self.filter.addItem(label, value)
        self.filter.currentIndexChanged.connect(lambda _i: self.refresh())
        head.addWidget(self.filter)
        queue.body.addLayout(head)
        self.table = build_table(
            ["#", "Cuenta", "Título", "Precio", "Estado", "Intentos", "Resultado / motivo"]
        )
        self.table.setMinimumHeight(320)
        queue.add(self.table)
        row = QHBoxLayout()
        for text, slot in (
            ("Reintentar seleccionados", self._retry_selected),
            ("Reintentar todos los errores", self._retry_all),
            ("Marcar como publicado", self._mark_published),
            ("Cancelar seleccionados", self._cancel_selected),
            ("Quitar de la cola", self._remove_selected),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        queue.body.addLayout(row)
        body.addWidget(queue)

        history = Card()
        history.add(SectionTitle("Historial de publicación"))
        self.history_table = build_table(["Fecha", "Anuncio", "Evento", "Detalle"])
        self.history_table.setMinimumHeight(220)
        history.add(self.history_table)
        body.addWidget(history)

        self._settings_loaded = False
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # ------------------------------------------------------------------
    def _tick(self) -> None:
        if self.isVisible():
            self.refresh()

    @property
    def _engine(self):
        return self.app.publication_engine

    def refresh(self) -> None:
        engine = self._engine
        status = engine.status()
        counts = status.counts
        self.stat_pending.update_value(counts["pending"] + counts["verifying"])
        self.stat_processing.update_value(counts["processing"])
        self.stat_published.update_value(counts["published"])
        self.stat_errors.update_value(counts["failed"])
        self.stat_review.update_value(counts["needs_review"])

        self.state_title.setText(f"Publicación — {status.label}")
        lines = [f"<b>Conexión:</b> {status.provider_label}",
                 f"Intervalo entre anuncios: {status.interval_seconds} s"]
        if status.current_title:
            lines.append(f"Ahora: «{status.current_title}»")
        if status.last_publish_at:
            lines.append(f"Última publicación: {status.last_publish_at:%H:%M:%S}")
        if status.next_publish_at and status.state is EngineState.RUNNING:
            lines.append(f"Próxima publicación permitida: {status.next_publish_at:%H:%M:%S}")
        if not status.can_publish:
            lines.append(
                f"<span style='color:{theme.WARNING}'>La publicación masiva usa conexión directa. "
                "Elige el tipo de conexión en «Conectar Wallapop».</span>"
            )
        if status.pause_reason and status.state is EngineState.PAUSED:
            lines.append(f"<b style='color:{theme.WARNING}'>En pausa:</b> {status.pause_reason}")
        if status.waiting_reason:
            lines.append(f"<span style='color:{theme.TEXT_MUTED}'>{status.waiting_reason}</span>")
        self.state_info.setText("<br>".join(lines))
        running = status.state is EngineState.RUNNING
        self.start_button.setText("Continuar" if status.state is EngineState.PAUSED else "Iniciar publicación")
        self.start_button.setEnabled(not running and status.can_publish)
        self.pause_button.setEnabled(running)

        if not self._settings_loaded:
            settings = engine.settings()
            self.interval.setMinimum(engine.minimum_interval)
            self.interval.setValue(int(settings["interval_seconds"]))
            self.daily.setValue(int(settings["max_per_account_per_day"] or 0))
            self.auto_resume.setChecked(bool(settings["auto_resume"]))
            self._settings_loaded = True

        aliases = {a.internal_ref: a.alias for a in self.app.accounts.list_accounts()}
        items = self.app.queue_manager.items(status=self.filter.currentData(), limit=1000)
        fill_table(
            self.table,
            [
                [
                    i["id"],
                    aliases.get(i["cuenta_ref"], i["cuenta_ref"]),
                    i["titulo"],
                    f"{i['precio']:.2f} €",
                    i["estado_texto"],
                    i["intentos"],
                    i["error"] if i["estado"] != "published" else (i["url"] or i["id_wallapop"] or ""),
                ]
                for i in items
            ],
            row_data=[i["id"] for i in items],
            colorizer=lambda row, col, _v: (
                STATUS_COLORS.get(items[row]["estado"]) if col == 4 and row < len(items) else None
            ),
        )
        events = self.app.queue_manager.history(limit=200)
        fill_table(
            self.history_table,
            [[local_time(e["fecha"]), e["anuncio"], EVENT_LABELS.get(e["evento"], e["evento"]),
              e["detalle"]] for e in events],
        )

    # ------------------------------------------------------------------
    def _safe(self, action, success: str | None = None) -> None:
        try:
            result = action()
        except (ValueError, RuntimeError) as exc:
            show_error(self, str(exc))
        else:
            if success:
                info_box(self, "Publicación masiva", success.format(result=result))
        self.refresh()

    def _start(self) -> None:
        self._safe(self._engine.start)

    def _pause(self) -> None:
        self._safe(self._engine.pause)

    def _go_connect(self) -> None:
        window = self.window()
        if hasattr(window, "go_to_view"):
            window.go_to_view("Conectar Wallapop")

    def _save_settings(self) -> None:
        self._safe(
            lambda: self._engine.save_settings(
                interval_seconds=self.interval.value(),
                max_per_account_per_day=self.daily.value(),
                auto_resume=self.auto_resume.isChecked(),
            ),
            "Ajustes guardados.",
        )

    def _add_ad(self) -> None:
        accounts = self.app.accounts.list_accounts()
        if not accounts:
            show_error(self, "Añade primero una cuenta en «Conectar Wallapop».")
            return
        dialog = AdDialog(accounts, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = self.app.queue_manager.add(dialog.ad())
        if result.added:
            info_box(self, "Publicación masiva", result.summary)
        else:
            show_error(self, "No se ha añadido el anuncio.",
                       "\n".join(result.invalid + result.duplicates))
        self.refresh()

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar anuncios desde CSV", "", "CSV (*.csv *.txt)"
        )
        if not path:
            return
        accounts = self.app.accounts.list_accounts()
        default = accounts[0].internal_ref if len(accounts) == 1 else None
        try:
            result = self.app.queue_manager.import_csv(path, default_account=default)
        except (OSError, UnicodeDecodeError) as exc:
            show_error(self, f"No se ha podido leer el fichero: {exc}")
            return
        detail = "\n".join(result.invalid + result.duplicates)
        if result.added:
            info_box(self, "Importación terminada", result.summary + ("\n\n" + detail[:1500] if detail else ""))
        else:
            show_error(self, "No se ha importado ningún anuncio. " + result.summary, detail)
        self.refresh()

    def _from_catalog(self) -> None:
        from lot_bot.integration import enqueue_products

        if not ask_confirmation(
            self, "Añadir productos del catálogo",
            "Se añadirán a la cola todos los productos de «Productos» en sus cuentas "
            "asignadas, con el título y la descripción de su plantilla. Los duplicados "
            "se omiten.",
        ):
            return
        try:
            result = enqueue_products(self.app)
        except ValueError as exc:
            show_error(self, str(exc))
            return
        detail = "\n".join(result.invalid + result.duplicates)
        info_box(self, "Productos añadidos", result.summary + ("\n\n" + detail[:1500] if detail else ""))
        self.refresh()

    def _selected(self) -> list[int]:
        return [int(v) for v in selected_row_data(self.table)]

    def _retry_selected(self) -> None:
        ids = self._selected()
        if ids:
            self._safe(lambda: self.app.queue_manager.retry(ids), "{result} anuncio(s) vuelven a la cola.")

    def _retry_all(self) -> None:
        self._safe(self.app.queue_manager.retry_all_failed, "{result} anuncio(s) vuelven a la cola.")

    def _mark_published(self) -> None:
        ids = self._selected()
        if ids and ask_confirmation(
            self, "Marcar como publicado",
            "Úsalo solo si has comprobado en Wallapop que el anuncio SÍ está publicado.",
        ):
            def action():
                for item_id in ids:
                    self.app.queue_manager.mark_reviewed_as_published(item_id)
            self._safe(action)

    def _cancel_selected(self) -> None:
        ids = self._selected()
        if ids:
            self._safe(lambda: self.app.queue_manager.cancel(ids), "{result} anuncio(s) cancelados.")

    def _remove_selected(self) -> None:
        ids = self._selected()
        if ids and ask_confirmation(
            self, "Quitar de la cola",
            "Se quitan de la cola los anuncios cancelados o con error seleccionados.",
            destructive=True,
        ):
            self._safe(lambda: self.app.queue_manager.remove(ids), "{result} anuncio(s) quitados.")
