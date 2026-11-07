"""Pantalla de mensajes: bandeja de entrada y respuestas asistidas."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import (
    Card,
    SectionTitle,
    Toolbar,
    ask_confirmation,
    info_box,
    show_error,
)


class MessagesView(BaseView):
    title = "Mensajes"
    subtitle = "Conversaciones con compradores"

    def build(self) -> None:
        self.add_header_button("Sincronizar", self._sync, primary=True)
        self.add_header_button("Actualizar", self.refresh)

        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.body.addWidget(self.notice)

        toolbar = Toolbar()
        self.account_filter = QComboBox()
        self.account_filter.currentIndexChanged.connect(self.refresh)
        toolbar.add(self.account_filter)
        self.unread_only = QPushButton("Solo sin leer")
        self.unread_only.setCheckable(True)
        self.unread_only.clicked.connect(self.refresh)
        toolbar.add(self.unread_only)
        toolbar.add_stretch()
        self.body.addWidget(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = Card()
        left.add(SectionTitle("Conversaciones"))
        self.conversation_list = QListWidget()
        self.conversation_list.currentItemChanged.connect(self._on_conversation_selected)
        left.add(self.conversation_list)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        thread_card = Card()
        self.thread_title = SectionTitle("Selecciona una conversación")
        thread_card.add(self.thread_title)
        self.thread = QTextEdit()
        self.thread.setReadOnly(True)
        thread_card.add(self.thread)
        right_layout.addWidget(thread_card, 2)

        reply_card = Card()
        reply_card.add(SectionTitle("Respuesta"))
        self.facts_label = QLabel()
        self.facts_label.setWordWrap(True)
        self.facts_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        reply_card.add(self.facts_label)

        self.reply = QTextEdit()
        self.reply.setPlaceholderText(
            "Escribe la respuesta o pulsa «Generar respuesta» para proponerla con los datos reales del producto."
        )
        self.reply.setFixedHeight(120)
        reply_card.add(self.reply)

        buttons = QHBoxLayout()
        self.generate_button = QPushButton("Generar respuesta")
        self.generate_button.clicked.connect(self._generate)
        buttons.addWidget(self.generate_button)
        buttons.addStretch(1)
        self.send_button = QPushButton("Enviar")
        self.send_button.setObjectName("Primary")
        self.send_button.clicked.connect(self._send)
        buttons.addWidget(self.send_button)
        reply_card.body.addLayout(buttons)
        right_layout.addWidget(reply_card, 1)

        splitter.addWidget(right)
        splitter.setSizes([320, 700])
        self.body.addWidget(splitter, 1)
        self._set_enabled(False)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        available = self.app.messages.messaging_available
        if not available:
            self.notice.setText(
                f"<b style='color:{theme.WARNING}'>NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS</b> — "
                f"la integración autorizada no incluye acceso a los mensajes de Wallapop. "
                f"Esta pantalla estará disponible cuando se conceda ese permiso."
            )
        elif self.app.demo_mode:
            self.notice.setText(
                f"<b style='color:{theme.WARNING}'>MODO DEMO</b> — conversaciones simuladas. "
                f"Nada se envía a compradores reales."
            )
        else:
            self.notice.setText("")

        current = self.account_filter.currentData()
        self.account_filter.blockSignals(True)
        self.account_filter.clear()
        self.account_filter.addItem("Todas las cuentas", None)
        for account in self.app.accounts.list_accounts():
            self.account_filter.addItem(account.alias, account.internal_ref)
        index = self.account_filter.findData(current)
        if index >= 0:
            self.account_filter.setCurrentIndex(index)
        self.account_filter.blockSignals(False)

        conversations = self.app.messages.list_conversations(
            account_ref=self.account_filter.currentData(),
            only_unread=self.unread_only.isChecked(),
        )
        self._conversations = conversations
        self.conversation_list.clear()
        for conversation in conversations:
            marker = "● " if conversation.unread else "   "
            item = QListWidgetItem(
                f"{marker}{conversation.buyer_name or 'Comprador'}\n"
                f"      {conversation.account_alias} · {conversation.listing_title or '—'}"
            )
            item.setData(Qt.ItemDataRole.UserRole, conversation.id)
            self.conversation_list.addItem(item)
        self.header.set_subtitle(
            f"{len(conversations)} conversación(es) · {self.app.messages.unread_count()} sin leer"
        )
        self.send_button.setEnabled(self.app.messages.sending_available)

    # ------------------------------------------------------------------
    def _current_id(self) -> int | None:
        item = self.conversation_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _set_enabled(self, enabled: bool) -> None:
        self.generate_button.setEnabled(enabled)
        self.reply.setEnabled(enabled)
        self.send_button.setEnabled(enabled and self.app.messages.sending_available)

    def _on_conversation_selected(self) -> None:
        conversation_id = self._current_id()
        if conversation_id is None:
            self._set_enabled(False)
            return
        view = self.app.messages.get_conversation(int(conversation_id))
        if view is None:
            return
        self.thread_title.setText(
            f"{view.buyer_name or 'Comprador'} · {view.account_alias} · {view.listing_title or '—'}"
        )
        lines = []
        for message in view.messages:
            who = "Comprador" if message.is_from_buyer else "Nosotros"
            stamp = message.sent_at.strftime("%d/%m %H:%M") if message.sent_at else ""
            tag = " (borrador)" if message.is_draft else ""
            lines.append(f"[{stamp}] {who}{tag}:\n{message.body}\n")
        self.thread.setPlainText("\n".join(lines))

        draft = next((m for m in view.messages if m.is_draft), None)
        self.reply.setPlainText(draft.body if draft else "")
        self._show_facts(view)
        self._set_enabled(True)
        self.app.messages.mark_read(int(conversation_id))

    def _show_facts(self, view) -> None:
        from lot_bot.ai.tools.base import ToolContext
        from lot_bot.ai.tools.message_tools import _sales_context

        context = _sales_context(ToolContext(app=self.app), view)
        known = context.known_facts()
        unknown = context.unknown_fields()
        text = "Datos comprobados: " + (
            ", ".join(f"{k}={v}" for k, v in list(known.items())[:8]) or "ninguno"
        )
        if unknown:
            text += f"  |  Sin datos: {', '.join(unknown)} (el asistente responderá «No dispongo de esa información»)"
        self.facts_label.setText(text)

    # ------------------------------------------------------------------
    def _sync(self) -> None:
        if not self.app.messages.messaging_available:
            show_error(
                self,
                "La integración autorizada no incluye acceso a los mensajes de Wallapop.",
            )
            return
        refs = [a.internal_ref for a in self.app.accounts.list_accounts() if a.is_connected]
        self.run_task(
            lambda: self.app.messages.sync_all(refs),
            on_success=lambda results: None,
            on_done=self.refresh,
        )

    def _generate(self) -> None:
        conversation_id = self._current_id()
        if conversation_id is None:
            return
        view = self.app.messages.get_conversation(int(conversation_id))
        last = view.last_buyer_message() if view else None
        if last is None:
            show_error(self, "No hay ningún mensaje del comprador al que responder.")
            return

        from lot_bot.ai.tools.base import ToolContext
        from lot_bot.ai.tools.message_tools import _prepare_message_response

        def work():
            return _prepare_message_response(
                ToolContext(app=self.app, actor="usuario"),
                {"conversacion": int(conversation_id)},
            )

        def success(result):
            if not result.ok:
                show_error(self, result.summary)
                return
            self.reply.setPlainText(result.data.get("respuesta", ""))
            missing = result.data.get("datos_que_faltan") or []
            if missing:
                self.facts_label.setText(
                    self.facts_label.text()
                    + f"  |  ATENCIÓN: faltan datos ({', '.join(missing)}); revisa la respuesta."
                )

        self.run_task(work, on_success=success)

    def _send(self) -> None:
        conversation_id = self._current_id()
        body = self.reply.toPlainText().strip()
        if conversation_id is None or not body:
            return
        view = self.app.messages.get_conversation(int(conversation_id))
        if not ask_confirmation(
            self,
            "Enviar mensaje",
            f"Cuenta: {view.account_alias}\nComprador: {view.buyer_name or '—'}\n\n{body}",
        ):
            return

        def success(result):
            if result.get("correcto"):
                info_box(self, "Mensaje enviado", result.get("mensaje", ""))
            else:
                show_error(self, result.get("mensaje", "No se ha podido enviar."))

        self.run_task(
            lambda: self.app.messages.send(
                int(conversation_id), body, confirmed=True, actor="usuario"
            ),
            on_success=success,
            on_done=self.refresh,
        )
