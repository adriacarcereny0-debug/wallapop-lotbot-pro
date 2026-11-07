"""Asistente IA: el chat principal de LOT Bot.

Flujo: el usuario escribe una orden -> el agente la interpreta -> si la accion
modifica informacion, aparece el panel [Cancelar] [Confirmar] y nada se ejecuta
hasta que el usuario decide.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lot_bot.ai.agent import AgentResponse
from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import Card, ConfirmationPanel

EXAMPLES = [
    "Prepara el anuncio de canapé",
    "Publica el anuncio de canapé",
    "Empieza a subir anuncios cada 60 segundos",
    "¿Cómo va la cola?",
    "Estadísticas",
    "¿Qué anuncios tienen más visualizaciones?",
]


class ChatBubble(QFrame):
    """Un mensaje del chat."""

    STYLES = {
        "usuario": (theme.ACCENT_SOFT, theme.TEXT, "Tú"),
        "asistente": (theme.CARD, theme.TEXT, "LOT Bot"),
        "herramienta": (theme.BG_ELEVATED, theme.TEXT_MUTED, "Acción"),
        "sistema": (theme.BG_ELEVATED, theme.WARNING, "Pendiente de confirmación"),
        "error": ("#2a1418", theme.DANGER, "Error"),
    }

    def __init__(self, role: str, text: str, tool_name: str | None = None) -> None:
        super().__init__()
        background, color, label = self.STYLES.get(role, self.STYLES["asistente"])
        if tool_name:
            label = f"Acción · {tool_name}"

        self.setStyleSheet(
            f"QFrame {{ background: {background}; border: 1px solid {theme.BORDER};"
            f"border-radius: 12px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(4)

        head = QLabel(f"{label} · {datetime.now():%H:%M}")
        head.setStyleSheet(f"color: {theme.TEXT_FAINT}; font-size: 11px; border: none;")
        layout.addWidget(head)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(f"color: {color}; border: none;")
        layout.addWidget(body)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)


class AssistantView(BaseView):
    title = "Asistente IA"
    subtitle = "¿Qué quieres hacer?"

    def build(self) -> None:
        self.add_header_button("Nueva conversación", self._reset)

        # --- Aviso del proveedor de IA ---
        self.provider_label = QLabel()
        self.provider_label.setWordWrap(True)
        self.provider_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        self.body.addWidget(self.provider_label)

        # --- Conversación ---
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("Content")
        self.chat_layout = QVBoxLayout(container)
        self.chat_layout.setContentsMargins(0, 0, 8, 0)
        self.chat_layout.setSpacing(10)
        self.chat_layout.addStretch(1)
        self.scroll.setWidget(container)
        # Bajar solo al final cuando llega un mensaje nuevo: se espera a que
        # la zona del chat crezca (el tamaño nuevo llega después de añadirlo).
        self._follow = True
        self.scroll.verticalScrollBar().rangeChanged.connect(self._on_range_changed)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_user_scroll)
        self.body.addWidget(self.scroll, 1)

        # --- Panel de confirmación ---
        self.confirmation = ConfirmationPanel()
        self.confirmation.confirmed.connect(self._on_confirmed)
        self.confirmation.cancelled.connect(self._on_cancelled)
        self.body.addWidget(self.confirmation)

        # --- Ejemplos ---
        self.examples_row = QHBoxLayout()
        self.examples_row.setSpacing(6)
        for example in EXAMPLES[:4]:
            button = QPushButton(example)
            button.setObjectName("Ghost")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, text=example: self._use_example(text))
            self.examples_row.addWidget(button)
        self.examples_row.addStretch(1)
        self.body.addLayout(self.examples_row)

        # --- Entrada ---
        input_card = Card(spacing=8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QTextEdit()
        self.input.setPlaceholderText(
            "Escribe una orden. Por ejemplo: «Empieza a subir anuncios cada 60 segundos»"
        )
        self.input.setFixedHeight(76)
        row.addWidget(self.input, 1)

        buttons = QVBoxLayout()
        self.send_button = QPushButton("Enviar")
        self.send_button.setObjectName("Primary")
        self.send_button.clicked.connect(self._send)
        buttons.addWidget(self.send_button)
        buttons.addStretch(1)
        row.addLayout(buttons)
        input_card.body.addLayout(row)
        self.body.addWidget(input_card)

        self._greet()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        provider = self.app.agent.provider
        mode = "MODO DEMO — los cambios no afectan a Wallapop." if self.app.demo_mode else ""
        extra = (
            ""
            if provider.natural_language
            else " Escribe órdenes concretas; para lenguaje libre configura una clave de IA en Ajustes."
        )
        self.provider_label.setText(f"Asistente: {provider.describe()}.{extra} {mode}".strip())

    # ------------------------------------------------------------------
    def _greet(self) -> None:
        self._add_bubble(
            "asistente",
            "Hola. Dime qué quieres hacer con tus cuentas de Wallapop.\n"
            "Antes de publicar, modificar o eliminar cualquier cosa te pediré confirmación.",
        )

    def _use_example(self, text: str) -> None:
        self.input.setPlainText(text)
        self.input.setFocus()

    def _reset(self) -> None:
        self.app.agent.reset()
        self.confirmation.clear()
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._greet()

    # ------------------------------------------------------------------
    def _add_bubble(self, role: str, text: str, tool_name: str | None = None) -> None:
        bubble = ChatBubble(role, text, tool_name)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        from PySide6.QtCore import QTimer

        self._follow = True
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _on_user_scroll(self, value: int) -> None:
        # Si subes para leer algo, no se te baja de golpe hasta el próximo mensaje.
        self._follow = value >= self.scroll.verticalScrollBar().maximum() - 30

    def _on_range_changed(self, _minimum: int, maximum: int) -> None:
        if getattr(self, "_follow", False):
            self.scroll.verticalScrollBar().setValue(maximum)

    # ------------------------------------------------------------------
    def _send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self._add_bubble("usuario", text)
        self._set_busy(True)
        self.run_task(
            lambda: self.app.agent.ask(text),
            on_success=self._on_response,
            on_done=lambda: self._set_busy(False),
        )

    #: Si una respuesta tarda más que esto, se libera el botón y se avisa (la
    #: tarea sigue en segundo plano y su resultado aparecerá al terminar).
    WATCHDOG_MS = 90_000

    def _set_busy(self, busy: bool) -> None:
        self.send_button.setEnabled(not busy)
        self.send_button.setText("Pensando…" if busy else "Enviar")
        self.input.setEnabled(not busy)
        if not hasattr(self, "_watchdog"):
            from PySide6.QtCore import QTimer

            self._watchdog = QTimer(self)
            self._watchdog.setSingleShot(True)
            self._watchdog.timeout.connect(self._on_watchdog)
        if busy:
            self._watchdog.start(self.WATCHDOG_MS)
        else:
            self._watchdog.stop()

    def _on_watchdog(self) -> None:
        if self.send_button.isEnabled():
            return
        self._set_busy(False)
        self._add_bubble(
            "asistente",
            "Esto está tardando más de lo normal. Sigue en segundo plano y te mostraré el "
            "resultado cuando termine; mientras tanto puedes seguir escribiendo. Si ves que no "
            "llega, mira «Logs y errores».",
        )

    def _on_response(self, response: AgentResponse) -> None:
        for message in response.messages:
            if message.role == "sistema":
                continue  # lo muestra el panel de confirmación
            self._add_bubble(message.role, message.text, message.tool_name)

        if response.needs_confirmation and response.pending is not None:
            request = response.pending.request
            self.confirmation.show_request(
                request.token, request.title, request.lines, request.destructive
            )
        else:
            self.confirmation.clear()
        self._notify_refresh()

    # ------------------------------------------------------------------
    def _on_confirmed(self, token: str) -> None:
        self._add_bubble("usuario", "Confirmado.")
        self._set_busy(True)
        self.run_task(
            lambda: self.app.agent.confirm(token),
            on_success=self._on_response,
            on_done=lambda: self._set_busy(False),
        )

    def _on_cancelled(self, token: str) -> None:
        self._add_bubble("usuario", "Cancelado.")
        self.run_task(lambda: self.app.agent.cancel(token), on_success=self._on_response)

    def _notify_refresh(self) -> None:
        """Avisa al resto de pantallas de que los datos pueden haber cambiado."""
        from lot_bot.core.events import TOPIC_CATALOG_CHANGED, TOPIC_LISTINGS_CHANGED

        self.app.events.publish(TOPIC_LISTINGS_CHANGED, {})
        self.app.events.publish(TOPIC_CATALOG_CHANGED, {})
