"""Pantalla «Conectar Wallapop»: conexión directa de las cuentas, sin navegador.

Pensada para clientes sin conocimientos técnicos: elegir el tipo de
conexión, elegir la cuenta y seguir los pasos numerados. Cada botón explica
qué hace y todo lo que pasa queda en el historial.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import (
    Badge,
    Card,
    SectionTitle,
    StatCard,
    ask_confirmation,
    build_table,
    fill_table,
    info_box,
    show_error,
)

#: Único tipo de conexión de esta pantalla: credencial (token) de integración.
TOKEN_MODE = "perfil"

AUTH_BADGE = {
    "connected": "connected",
    "expiring": "expired",
    "needs_reconnect": "expired",
    "not_connected": "disconnected",
    "error": "error",
    "demo": "ready",
}


def local_time(value: datetime | None) -> str:
    """Las fechas se guardan en UTC: se muestran en la hora del equipo."""
    if value is None:
        return "—"
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    return f"{value + offset:%d/%m/%Y %H:%M}"


_local = local_time


def scroll_body(view) -> QVBoxLayout:
    """Pone el contenido en una zona desplazable: nada se comprime aunque la
    ventana sea baja. Devuelve el layout donde añadir las tarjetas."""
    from PySide6.QtWidgets import QFrame, QScrollArea, QWidget

    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    inner = QWidget()
    inner.setObjectName("Content")
    layout = QVBoxLayout(inner)
    layout.setContentsMargins(0, 0, 6, 0)
    layout.setSpacing(14)
    area.setWidget(inner)
    view.body.addWidget(area, 1)
    return layout


class ConnectWallapopView(BaseView):
    title = "Conectar Wallapop"
    subtitle = "Conecta tus cuentas sin abrir ni controlar ningún navegador"

    def build(self) -> None:
        self.add_header_button("Actualizar", self.refresh)
        body = scroll_body(self)

        # --- 1. Tipo de conexión -------------------------------------------
        mode_card = Card()
        mode_card.add(SectionTitle("1 · Tipo de conexión: credencial (token) de Wallapop"))
        row = QHBoxLayout()
        self.mode_button = QPushButton("Activar conexión por credencial")
        self.mode_button.setObjectName("Primary")
        self.mode_button.setToolTip(
            "Publica directamente con la credencial (token) que Wallapop emite para "
            "cada cuenta, sin navegador."
        )
        self.mode_button.clicked.connect(self._apply_mode)
        row.addWidget(self.mode_button)
        row.addStretch(1)
        mode_card.body.addLayout(row)
        self.mode_info = QLabel()
        self.mode_info.setWordWrap(False)
        mode_card.add(self.mode_info)
        body.addWidget(mode_card)

        # --- 2. Cuenta -------------------------------------------------------
        account_card = Card()
        account_card.add(SectionTitle("2 · Cuenta"))
        row = QHBoxLayout()
        self.account_combo = QComboBox()
        self.account_combo.currentIndexChanged.connect(lambda _i: self._show_status())
        row.addWidget(self.account_combo, 1)
        add_button = QPushButton("Añadir cuenta")
        add_button.clicked.connect(self._add_account)
        row.addWidget(add_button)
        account_card.body.addLayout(row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        self.fields: dict[str, QLabel] = {}
        names = [
            ("account", "Estado de la cuenta"),
            ("auth", "Estado de autenticación"),
            ("last_connection", "Última conexión"),
            ("last_check", "Última comprobación"),
            ("expires", "La credencial caduca"),
            ("login", "Usuario de Wallapop"),
        ]
        for index, (key, label) in enumerate(names):
            caption = QLabel(label)
            caption.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            grid.addWidget(caption, index // 2, (index % 2) * 2)
            value = QLabel("—")
            grid.addWidget(value, index // 2, (index % 2) * 2 + 1)
            self.fields[key] = value
        self.auth_badge = Badge("—")
        self.auth_badge.setMinimumHeight(22)
        grid.addWidget(self.auth_badge, 0, 4)
        grid.setColumnStretch(5, 1)
        account_card.body.addLayout(grid)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        account_card.add(self.detail)
        body.addWidget(account_card)

        # --- 3. Pasos --------------------------------------------------------
        steps_card = Card()
        self.steps_title = SectionTitle("3 · Conectar")
        steps_card.add(self.steps_title)
        self.steps = QLabel()
        self.steps.setWordWrap(False)
        steps_card.add(self.steps)

        row = QHBoxLayout()
        self.start_button = QPushButton("Iniciar conexión")
        self.start_button.setObjectName("Primary")
        self.start_button.setToolTip("Empieza el procedimiento de conexión autorizado por Wallapop.")
        self.start_button.clicked.connect(self._start)
        row.addWidget(self.start_button)
        self.credential = QLineEdit()
        self.credential.setEchoMode(QLineEdit.EchoMode.Password)
        self.credential.setPlaceholderText("Credencial de integración (pégala aquí)")
        row.addWidget(self.credential, 1)
        self.show_credential = QCheckBox("Mostrar")
        self.show_credential.toggled.connect(
            lambda on: self.credential.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        row.addWidget(self.show_credential)
        self.validate_button = QPushButton("Validar conexión")
        self.validate_button.setObjectName("Primary")
        self.validate_button.clicked.connect(self._validate)
        row.addWidget(self.validate_button)
        steps_card.body.addLayout(row)

        row = QHBoxLayout()
        self.check_button = QPushButton("Comprobar estado")
        self.check_button.setToolTip("Comprueba si la autenticación sigue activa.")
        self.check_button.clicked.connect(self._check)
        row.addWidget(self.check_button)
        self.renew_button = QPushButton("Renovar")
        self.renew_button.setToolTip("Sustituye la credencial por la nueva que te dé Wallapop.")
        self.renew_button.clicked.connect(self._renew)
        row.addWidget(self.renew_button)
        self.reconnect_button = QPushButton("Reconectar")
        self.reconnect_button.setToolTip("Vuelve a hacer la conexión (por ejemplo, con una credencial nueva).")
        self.reconnect_button.clicked.connect(self._reconnect)
        row.addWidget(self.reconnect_button)
        self.disconnect_button = QPushButton("Desconectar")
        self.disconnect_button.setObjectName("Danger")
        self.disconnect_button.clicked.connect(self._disconnect)
        row.addWidget(self.disconnect_button)
        row.addStretch(1)
        steps_card.body.addLayout(row)
        body.addWidget(steps_card)

        # --- Resumen de la cola ---------------------------------------------
        stats = QHBoxLayout()
        self.stat_pending = StatCard("Anuncios pendientes")
        self.stat_published = StatCard("Anuncios publicados")
        self.stat_errors = StatCard("Errores")
        for card in (self.stat_pending, self.stat_published, self.stat_errors):
            stats.addWidget(card)
        body.addLayout(stats)

        # --- Historial -------------------------------------------------------
        history = Card()
        history.add(SectionTitle("Historial de conexión"))
        self.history_table = build_table(["Fecha", "Cuenta", "Evento", "Resultado", "Detalle"])
        self.history_table.setMinimumHeight(240)
        history.add(self.history_table)
        body.addWidget(history)

    # ------------------------------------------------------------------
    @property
    def _auth(self):
        return self.app.auth_manager

    def _ref(self) -> str | None:
        return self.account_combo.currentData()

    def refresh(self) -> None:
        backend = self.app.backend
        chosen = self.app.settings.wallapop_integration or self.app.integration_mode
        active = chosen == TOKEN_MODE
        self.mode_button.setVisible(not active)
        client = self.app.wallapop_client
        lines = [f"<b>Activo:</b> {client.provider_label}"]
        if backend.reason:
            lines.append(backend.reason)
        missing = [m for m in backend.missing if not m.startswith("Operación no")]
        if missing:
            lines.append(
                f"<span style='color:{theme.WARNING}'><b>Falta para conectar de verdad:</b></span><br>"
                + "<br>".join(f"• {m}" for m in missing)
            )
        self.mode_info.setText("<br>".join(lines))

        selected = self._ref()
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        for account in self.app.accounts.list_accounts():
            self.account_combo.addItem(account.alias, account.internal_ref)
        if selected:
            found = self.account_combo.findData(selected)
            if found >= 0:
                self.account_combo.setCurrentIndex(found)
        self.account_combo.blockSignals(False)
        self._show_status()

    def _show_status(self) -> None:
        procedure = self._auth.procedure()
        self.steps_title.setText(f"3 · Conectar — {procedure.title}")
        steps = "<br>".join(f"<b>{n}.</b> {s}" for n, s in enumerate(procedure.steps, start=1))
        if procedure.documentation_url:
            steps += (
                f"<br><a style='color:{theme.ACCENT}' href='{procedure.documentation_url}'>"
                "Ver la documentación de Wallapop</a>"
            )
            self.steps.setOpenExternalLinks(True)
        self.steps.setText(steps or "—")
        self.credential.setVisible(procedure.needs_credential)
        self.show_credential.setVisible(procedure.needs_credential)
        self.validate_button.setText("Validar conexión")

        ref = self._ref()
        for button in (self.start_button, self.validate_button, self.check_button,
                       self.renew_button, self.reconnect_button, self.disconnect_button):
            button.setEnabled(ref is not None)
        self.start_button.setEnabled(ref is not None and procedure.ready)
        self.renew_button.setEnabled(ref is not None and procedure.needs_credential)

        if ref is None:
            for label in self.fields.values():
                label.setText("—")
            self.detail.setText("Añade una cuenta para empezar.")
        else:
            try:
                status = self._auth.status(ref)
            except ValueError:
                status = None
            if status is not None:
                self.fields["account"].setText(status.account_status)
                self.fields["auth"].setText(status.auth_label)
                self.auth_badge.setText(status.auth_label)
                self.auth_badge.apply_status(AUTH_BADGE.get(status.auth_state.value, "idle"))
                self.fields["last_connection"].setText(_local(status.last_connected_at))
                check = _local(status.last_check_at)
                if status.last_check_ok is not None:
                    check += " ✓" if status.last_check_ok else " ✗"
                self.fields["last_check"].setText(check)
                self.fields["expires"].setText(
                    _local(status.expires_at) if status.expires_at else "Sin caducidad conocida"
                )
                self.fields["login"].setText(status.login or "—")
                hint = status.detail
                if status.needs_reconnect:
                    hint = (hint + " " if hint else "") + "Pulsa «Reconectar»."
                self.detail.setText(
                    f"<span style='color:{theme.WARNING}'>{hint}</span>" if hint else ""
                )
                self.validate_button.setEnabled(procedure.needs_credential or status.is_usable)

        counts = self.app.queue_manager.counts()
        self.stat_pending.update_value(counts["pendientes"])
        self.stat_published.update_value(counts["published"])
        self.stat_errors.update_value(counts["errores"])

        rows = self._auth.history(limit=200)
        fill_table(
            self.history_table,
            [[_local(r["fecha"]), r["cuenta"], r["evento"], "✓" if r["ok"] else "✗", r["detalle"]]
             for r in rows],
            colorizer=lambda row, col, _v: (
                (theme.SUCCESS if rows[row]["ok"] else theme.DANGER) if col == 3 else None
            ),
        )

    # ------------------------------------------------------------------
    def _notify_window(self) -> None:
        window = self.window()
        update = getattr(window, "_update_status", None)
        if callable(update):
            update()

    def _apply_mode(self) -> None:
        try:
            self.app.set_integration_mode(TOKEN_MODE)
        except Exception as exc:  # perfil con errores, etc.
            show_error(self, getattr(exc, "user_message", None) or str(exc))
        self.refresh()
        self._notify_window()

    def _add_account(self) -> None:
        alias, ok = QInputDialog.getText(
            self, "Añadir cuenta", "Nombre para reconocer la cuenta (por ejemplo, «Tienda Madrid»):"
        )
        if not ok:
            return
        try:
            info = self._auth.add_account(alias)
        except ValueError as exc:
            show_error(self, str(exc))
            return
        self.refresh()
        index = self.account_combo.findData(info.internal_ref)
        if index >= 0:
            self.account_combo.setCurrentIndex(index)

    def _run(self, function, *args) -> None:
        """Las acciones que hablan con Wallapop se hacen en segundo plano."""
        for button in (self.start_button, self.validate_button, self.check_button,
                       self.renew_button, self.reconnect_button, self.disconnect_button):
            button.setEnabled(False)
        self.run_task(function, *args, on_success=self._on_result, on_done=self._done)

    def _done(self) -> None:
        self.refresh()
        self._notify_window()

    def _on_result(self, result) -> None:
        if result.open_url.startswith("https://"):
            QDesktopServices.openUrl(QUrl(result.open_url))
        if result.awaiting_credential:
            self.credential.setFocus()
        if result.ok:
            info_box(self, "Conectar Wallapop", result.message)
        else:
            show_error(self, result.message)

    def _start(self) -> None:
        if self._ref():
            self._run(self._auth.start_connection, self._ref())

    def _validate(self) -> None:
        ref = self._ref()
        if not ref:
            return
        procedure = self._auth.procedure()
        if procedure.needs_credential and self.credential.text().strip():
            value = self.credential.text()
            self.credential.clear()  # no se queda en pantalla
            self._run(self._auth.submit_credential, ref, value)
        elif procedure.needs_credential and not self._auth.status(ref).is_usable:
            show_error(self, "Pega primero la credencial de integración.")
        else:
            self._run(self._auth.validate, ref)

    def _check(self) -> None:
        if self._ref():
            self._run(self._auth.check, self._ref())

    def _renew(self) -> None:
        """Renovar = sustituir la credencial por la nueva que dé Wallapop."""
        ref = self._ref()
        if not ref:
            return
        value = self.credential.text()
        if not value.strip():
            self.credential.setFocus()
            show_error(
                self,
                "Pega la nueva credencial (token) en «Credencial de integración» y "
                "vuelve a pulsar «Renovar».",
            )
            return
        self.credential.clear()
        self._run(self._auth.submit_credential, ref, value)

    def _reconnect(self) -> None:
        if self._ref():
            self._run(self._auth.reconnect, self._ref())

    def _disconnect(self) -> None:
        ref = self._ref()
        if ref and ask_confirmation(
            self,
            "Desconectar cuenta",
            "Se borrará la credencial de este equipo. Los anuncios pendientes de esta "
            "cuenta se conservan en la cola.",
            destructive=True,
        ):
            self._run(self._auth.disconnect, ref)


__all__ = ["ConnectWallapopView", "local_time"]
