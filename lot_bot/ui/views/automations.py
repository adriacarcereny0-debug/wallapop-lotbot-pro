"""Pantalla de automatizaciones."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QTextEdit

from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import (
    Card,
    SectionTitle,
    build_table,
    fill_table,
    info_box,
    selected_row_data,
)


class AutomationsView(BaseView):
    title = "Automatizaciones"
    subtitle = "Tareas programadas. Ninguna publica ni modifica nada sin tu autorización"

    def build(self) -> None:
        self.add_header_button("Actualizar", self.refresh)

        notice = QLabel(
            "Todas las automatizaciones incluidas son de solo lectura: revisan, sincronizan "
            "y preparan trabajo, pero no publican ni modifican anuncios por su cuenta."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.body.addWidget(notice)

        card = Card()
        self.table = build_table(
            ["Tarea", "Activa", "Frecuencia", "Última ejecución", "Próxima", "Estado", "Resultado"]
        )
        self.table.itemSelectionChanged.connect(self._on_selection)
        card.add(self.table)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.enable_button = QPushButton("Activar / desactivar")
        self.enable_button.clicked.connect(self._toggle)
        actions.addWidget(self.enable_button)

        actions.addWidget(QLabel("Frecuencia (min):"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 10080)
        self.interval_spin.setValue(60)
        actions.addWidget(self.interval_spin)
        self.interval_button = QPushButton("Guardar frecuencia")
        self.interval_button.clicked.connect(self._save_interval)
        actions.addWidget(self.interval_button)

        actions.addStretch(1)
        self.run_button = QPushButton("Ejecutar ahora")
        self.run_button.setObjectName("Primary")
        self.run_button.clicked.connect(self._run_now)
        actions.addWidget(self.run_button)
        card.body.addLayout(actions)
        self.body.addWidget(card, 2)

        detail_card = Card()
        detail_card.add(SectionTitle("Detalle de la última ejecución"))
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFixedHeight(150)
        detail_card.add(self.detail)
        self.body.addWidget(detail_card)

        self._on_selection()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        automations = self.app.automations.list_automations()
        self._automations = automations
        fill_table(
            self.table,
            [
                [
                    a.name,
                    "Sí" if a.enabled else "No",
                    a.interval_label,
                    a.last_run_at.strftime("%d/%m %H:%M") if a.last_run_at else "nunca",
                    a.next_run_at.strftime("%d/%m %H:%M") if a.next_run_at else "—",
                    a.status_label,
                    (a.last_result or "—")[:80],
                ]
                for a in automations
            ],
            row_data=[a.job_key for a in automations],
            colorizer=lambda row, col, value: (
                theme.STATUS_COLORS.get(automations[row].last_status)
                if col == 5 and row < len(automations)
                else (theme.SUCCESS if col == 1 and value == "Sí" else None)
            ),
        )
        active = sum(1 for a in automations if a.enabled)
        self.header.set_subtitle(
            f"{active} de {len(automations)} automatizaciones activas · "
            f"{'planificador en marcha' if self.app.automations._started else 'planificador detenido'}"
        )
        self._on_selection()

    # ------------------------------------------------------------------
    def _selected_key(self) -> str | None:
        keys = selected_row_data(self.table)
        return str(keys[0]) if keys else None

    def _on_selection(self) -> None:
        key = self._selected_key()
        enabled = key is not None
        for button in (self.enable_button, self.interval_button, self.run_button):
            button.setEnabled(enabled)
        if key is None:
            self.detail.clear()
            return
        automation = next((a for a in self._automations if a.job_key == key), None)
        if automation is None:
            return
        self.interval_spin.setValue(automation.interval_minutes)
        lines = [
            automation.description,
            "",
            f"Estado: {automation.status_label}",
            f"Último resultado: {automation.last_result or '—'}",
        ]
        if automation.last_error:
            lines.append(f"Último error: {automation.last_error}")
        self.detail.setPlainText("\n".join(lines))

    def _toggle(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        automation = next((a for a in self._automations if a.job_key == key), None)
        if automation is None:
            return
        self.app.automations.set_enabled(key, not automation.enabled)
        self.app.audit.record_success(
            "Cambio de automatización",
            target=automation.name,
            detail="activada" if not automation.enabled else "desactivada",
        )
        self.refresh()

    def _save_interval(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        self.app.automations.set_interval(key, self.interval_spin.value())
        self.refresh()

    def _run_now(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        self.run_button.setEnabled(False)
        self.run_button.setText("Ejecutando…")

        def success(result):
            detail = "\n".join(
                f"{k}: {v}" for k, v in (result.details or {}).items() if v
            )
            info_box(
                self,
                "Automatización ejecutada",
                f"{result.summary}\n\n{detail}"[:2000],
            )

        def done(_=None):
            self.run_button.setEnabled(True)
            self.run_button.setText("Ejecutar ahora")
            self.refresh()

        self.run_task(lambda: self.app.automations.run_now(key), on_success=success, on_done=done)
