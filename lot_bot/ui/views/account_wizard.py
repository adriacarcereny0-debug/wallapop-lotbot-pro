"""Dialogos para anadir y conectar cuentas de Wallapop.

Sustituyen a la antigua pantalla de «introduce tu API key». Aqui el usuario:

  1. Da un nombre a la cuenta.
  2. Ve QUE mecanismo de acceso esta autorizado en esta instalacion.
  3. Si al mecanismo le falta algun dato tecnico, ve EXACTAMENTE cual y quien
     debe proporcionarlo, en vez de un formulario que no puede rellenar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lot_bot.ui import theme
from lot_bot.ui.widgets.common import spanish_buttons
from lot_bot.wallapop.auth.base import AuthMethod, RequirementSource

if TYPE_CHECKING:  # pragma: no cover
    from lot_bot.bootstrap import Application


SOURCE_LABELS = {
    RequirementSource.WALLAPOP: "lo tiene que facilitar Wallapop",
    RequirementSource.USUARIO: "lo introduces tú",
    RequirementSource.INSTALACION: "se configura en la instalación",
}


class AccessStatusPanel(QFrame):
    """Muestra el estado del mecanismo de acceso y lo que le falta."""

    def __init__(self, method: AuthMethod, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        ready = method.is_ready
        color = theme.SUCCESS if ready else theme.WARNING
        title = QLabel(f"<b style='color:{color}'>{method.describe()}</b>")
        title.setWordWrap(True)
        layout.addWidget(title)

        description = QLabel(method.description)
        description.setWordWrap(True)
        description.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(description)

        missing = method.missing_requirements()
        if not missing:
            state = QLabel("✓ Listo para conectar cuentas.")
            state.setStyleSheet(f"color: {theme.SUCCESS};")
            layout.addWidget(state)
            return

        warning = QLabel(
            "Para poder conectar hacen falta estos datos técnicos. "
            "LOT Bot no los inventa: hay que pedirlos."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(f"color: {theme.WARNING};")
        layout.addWidget(warning)

        for requirement in missing:
            entry = QLabel(
                f"<b>{requirement.label}</b> "
                f"<span style='color:{theme.TEXT_FAINT}'>"
                f"({SOURCE_LABELS.get(requirement.source, requirement.source.value)})</span>"
                f"<br>{requirement.description}"
                f"<br><span style='color:{theme.TEXT_FAINT}'>Se configura en: "
                f"{requirement.where}</span>"
            )
            entry.setWordWrap(True)
            entry.setStyleSheet(
                f"border-left: 2px solid {theme.WARNING}; padding-left: 10px; margin: 2px 0;"
            )
            layout.addWidget(entry)


class AddAccountDialog(QDialog):
    """Alta de una cuenta nueva."""

    def __init__(self, app: Application, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("Añadir cuenta de Wallapop")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        intro = QLabel(
            "Cada cuenta se gestiona por separado: sus anuncios, mensajes e "
            "inventario nunca se mezclan con los de otra."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(intro)

        form = QFormLayout()
        self.alias = QLineEdit()
        self.alias.setPlaceholderText("Por ejemplo: Tienda principal")
        form.addRow("Nombre de la cuenta", self.alias)
        layout.addLayout(form)

        method = app.auth_method
        layout.addWidget(QLabel("<b>Mecanismo de acceso de esta instalación</b>"))
        layout.addWidget(AccessStatusPanel(method))

        if app.demo_mode:
            note = QLabel(
                f"<b style='color:{theme.WARNING}'>MODO DEMO.</b> La cuenta que crees "
                f"funcionará con datos simulados. Nada afecta a Wallapop."
            )
            note.setWordWrap(True)
            layout.addWidget(note)

        buttons = spanish_buttons(QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        ))
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Añadir")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def account_alias(self) -> str:
        return self.alias.text().strip()


class DelegatedCredentialDialog(QDialog):
    """Pide la credencial que Wallapop ha emitido para UNA cuenta.

    No es una API key de aplicación: es el valor que el titular de la cuenta
    ha recibido de Wallapop para esa cuenta concreta.
    """

    def __init__(self, account_alias: str, instructions: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Conectar «{account_alias}»")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        explanation = QLabel(
            "Introduce la credencial que Wallapop ha emitido <b>para esta cuenta</b>. "
            "Se guardará cifrada y no volverá a mostrarse."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        if instructions:
            hint = QLabel(instructions)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            layout.addWidget(hint)

        self.credential = QLineEdit()
        self.credential.setEchoMode(QLineEdit.EchoMode.Password)
        self.credential.setPlaceholderText("Credencial emitida por Wallapop")
        layout.addWidget(self.credential)

        warning = QLabel(
            "No introduzcas aquí tu contraseña de Wallapop. LOT Bot nunca la pide "
            "ni la necesita."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(f"color: {theme.WARNING}; font-size: 12px;")
        layout.addWidget(warning)

        buttons = spanish_buttons(QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        ))
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Conectar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> str:
        return self.credential.text().strip()


class MissingAccessDialog(QDialog):
    """Explica por qué todavía no se puede conectar y qué hay que pedir."""

    def __init__(self, app: Application, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Falta información para conectar con Wallapop")
        self.resize(640, 520)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QLabel(
            f"<b style='color:{theme.WARNING}'>{app.backend.reason}</b>"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setStyleSheet(f"background: {theme.BG};")
        container = QWidget()
        container.setObjectName("Content")
        inner = QVBoxLayout(container)
        inner.setContentsMargins(0, 0, 8, 0)
        inner.setSpacing(10)

        if app.missing_access_data:
            inner.addWidget(QLabel("<b>Qué falta exactamente</b>"))
            for item in app.missing_access_data:
                entry = QLabel(f"• {item}")
                entry.setWordWrap(True)
                inner.addWidget(entry)

        inner.addWidget(QLabel("<b>Mecanismos que LOT Bot sabe manejar</b>"))
        from lot_bot.wallapop.auth import available_methods

        for method in available_methods(
            app.access_profile.auth, redirect_uri=app.settings.wallapop_redirect_uri
        ):
            inner.addWidget(AccessStatusPanel(method))

        inner.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        footer = QLabel(
            "Mientras tanto, LOT Bot sigue funcionando en <b>MODO DEMO</b> con datos "
            "simulados. Nada de lo que hagas afecta a Wallapop."
        )
        footer.setWordWrap(True)
        footer.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(footer)

        buttons = spanish_buttons(QDialogButtonBox(QDialogButtonBox.StandardButton.Close))
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class AuthenticatingDialog(QDialog):
    """Mensaje mientras el usuario completa el inicio de sesión en el navegador."""

    def __init__(self, method: AuthMethod, account_alias: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Autenticando cuenta")
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(f"<b>Conectando «{account_alias}»</b>")
        layout.addWidget(title)

        steps = QLabel(
            "1. Se ha abierto tu navegador en Wallapop.<br>"
            "2. Inicia sesión allí con esa cuenta.<br>"
            "3. Completa los pasos de seguridad que Wallapop te pida.<br>"
            "4. Autoriza el acceso y vuelve aquí.<br><br>"
            "LOT Bot <b>no ve ni guarda tu contraseña</b>."
        )
        steps.setWordWrap(True)
        layout.addWidget(steps)

        row = QHBoxLayout()
        row.addStretch(1)
        buttons = spanish_buttons(QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        layout.addLayout(row)
