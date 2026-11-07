"""Clase base de las pantallas de LOT Bot."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from lot_bot.ui.widgets.common import PageHeader, show_error
from lot_bot.ui.widgets.workers import TaskRunner

if TYPE_CHECKING:  # pragma: no cover
    from lot_bot.bootstrap import Application

logger = logging.getLogger(__name__)


class BaseView(QWidget):
    """Pantalla con cabecera, cuerpo y ejecucion en segundo plano."""

    title: str = ""
    subtitle: str = ""

    def __init__(self, app: Application, runner: TaskRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.runner = runner
        self.setObjectName("Content")

        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(28, 24, 28, 24)
        self.root.setSpacing(16)

        self.header = PageHeader(self.title, self.subtitle)
        self.root.addWidget(self.header)

        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        self.root.addLayout(self.body, 1)

        self.build()

    # ------------------------------------------------------------------
    def build(self) -> None:
        """Construye el contenido de la pantalla. Lo implementa cada vista."""

    def refresh(self) -> None:
        """Vuelve a cargar los datos. Se llama al mostrar la pantalla."""

    # ------------------------------------------------------------------
    def add_header_button(self, text: str, slot, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        if primary:
            button.setObjectName("Primary")
        button.clicked.connect(slot)
        return self.header.add_action(button)

    def run_task(self, function, *args, on_success=None, on_done=None, **kwargs) -> None:
        """Ejecuta algo en segundo plano mostrando los errores al usuario."""
        self.runner.run(
            function,
            *args,
            on_success=on_success,
            on_error=self._on_task_error,
            on_done=on_done,
            **kwargs,
        )

    def _on_task_error(self, message: str, detail: str) -> None:
        logger.warning("Error en tarea de la interfaz: %s", message)
        show_error(self, message, detail)
