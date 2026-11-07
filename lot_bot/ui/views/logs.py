"""Pantalla de logs y errores tecnicos."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QPlainTextEdit

from lot_bot.logs.setup import get_log_file, tail_log
from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import Card, Toolbar, info_box


class LogsView(BaseView):
    title = "Logs y errores"
    subtitle = "Registro técnico. Los tokens y las claves nunca se guardan aquí"

    def build(self) -> None:
        self.add_header_button("Actualizar", self.refresh)
        self.add_header_button("Abrir carpeta", self._show_path)

        toolbar = Toolbar()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filtrar líneas…")
        self.filter.textChanged.connect(self.refresh)
        toolbar.add(self.filter, 1)
        self.errors_only = QCheckBox("Solo errores y avisos")
        self.errors_only.stateChanged.connect(self.refresh)
        toolbar.add(self.errors_only)
        self.autorefresh = QCheckBox("Actualizar solo")
        self.autorefresh.stateChanged.connect(self._toggle_autorefresh)
        toolbar.add(self.autorefresh)
        self.body.addWidget(toolbar)

        card = Card()
        self.path_label = QLabel()
        self.path_label.setStyleSheet(f"color: {theme.TEXT_FAINT}; font-size: 11px;")
        card.add(self.path_label)

        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        font = QFont("Consolas, 'DejaVu Sans Mono', monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.viewer.setFont(font)
        card.add(self.viewer)
        self.body.addWidget(card, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        path = get_log_file()
        self.path_label.setText(f"Fichero: {path}")
        lines = tail_log(600)
        needle = self.filter.text().strip().lower()
        if needle:
            lines = [line for line in lines if needle in line.lower()]
        if self.errors_only.isChecked():
            lines = [line for line in lines if "ERROR" in line or "WARNING" in line or "CRITICAL" in line]
        self.viewer.setPlainText("\n".join(lines))
        self.viewer.verticalScrollBar().setValue(self.viewer.verticalScrollBar().maximum())
        self.header.set_subtitle(f"{len(lines)} línea(s)")

    def _toggle_autorefresh(self) -> None:
        if self.autorefresh.isChecked():
            self._timer.start()
        else:
            self._timer.stop()

    def _show_path(self) -> None:
        info_box(
            self,
            "Ubicación de los registros",
            f"Los registros se guardan en:\n\n{get_log_file().parent}\n\n"
            f"Puedes enviarlos a soporte si necesitas ayuda: no contienen contraseñas "
            f"ni tokens (se eliminan automáticamente).",
        )
