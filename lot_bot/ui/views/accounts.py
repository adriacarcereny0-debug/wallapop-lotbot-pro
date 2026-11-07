"""Pantalla «Cuentas de Wallapop».

Alta, conexion, reautenticacion y baja de cuentas. El mecanismo de conexion es
el que Wallapop haya autorizado; esta pantalla no asume ninguno y, cuando falta
algun dato tecnico, lo dice en lugar de pedir una credencial inexistente.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QInputDialog, QLabel, QPushButton

from lot_bot.ui import theme
from lot_bot.ui.views.account_wizard import (
    AddAccountDialog,
    DelegatedCredentialDialog,
    MissingAccessDialog,
)
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import (
    Card,
    SectionTitle,
    ask_confirmation,
    build_table,
    fill_table,
    info_box,
    selected_row_data,
    show_error,
)
from lot_bot.wallapop.auth.base import AuthKind


class AccountsView(BaseView):
    title = "Cuentas de Wallapop"
    subtitle = "Cada cuenta está aislada: nunca se mezclan anuncios ni mensajes"

    def build(self) -> None:
        self.add_header_button("Añadir cuenta Wallapop", self._add_browser_account, primary=True)
        self.generic_add_button = self.add_header_button("Añadir cuenta", self._add_account)
        self.add_header_button("Actualizar", self.refresh)

        # --- Estado del acceso ---
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.body.addWidget(self.notice)

        self.details_button = QPushButton("Ver qué falta para conectar con Wallapop")
        self.details_button.setObjectName("Ghost")
        self.details_button.clicked.connect(self._show_missing)
        self.body.addWidget(self.details_button)

        # --- Tabla ---
        card = Card()
        card.add(SectionTitle("Cuentas configuradas"))
        self.table = build_table(
            [
                "Cuenta",
                "Referencia",
                "Estado",
                "Mecanismo",
                "Usuario",
                "Última sincronización",
            ]
        )
        self.table.itemSelectionChanged.connect(self._update_buttons)
        card.add(self.table)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.connect_button = QPushButton("Conectar")
        self.connect_button.setObjectName("Primary")
        self.connect_button.clicked.connect(self._connect)
        actions.addWidget(self.connect_button)

        self.reauth_button = QPushButton("Volver a autenticar")
        self.reauth_button.clicked.connect(self._reauthenticate)
        actions.addWidget(self.reauth_button)

        self.disconnect_button = QPushButton("Desconectar")
        self.disconnect_button.clicked.connect(self._disconnect)
        actions.addWidget(self.disconnect_button)

        self.sync_button = QPushButton("Sincronizar")
        self.sync_button.clicked.connect(self._sync)
        actions.addWidget(self.sync_button)

        self.check_button = QPushButton("Comprobar conexión")
        self.check_button.setToolTip("Comprueba en Wallapop que la sesión de la cuenta sigue siendo válida.")
        self.check_button.clicked.connect(self._check_connection)
        actions.addWidget(self.check_button)

        self.browser_button = QPushButton("Abrir cuenta")
        self.browser_button.setToolTip(
            "Abre el navegador de esta cuenta, con su sesión, por ejemplo para completar "
            "una verificación que pida Wallapop."
        )
        self.browser_button.clicked.connect(self._open_browser)
        actions.addWidget(self.browser_button)

        self.rename_button = QPushButton("Renombrar")
        self.rename_button.clicked.connect(self._rename)
        actions.addWidget(self.rename_button)

        actions.addStretch(1)
        self.remove_button = QPushButton("Eliminar cuenta")
        self.remove_button.setObjectName("Danger")
        self.remove_button.clicked.connect(self._remove)
        actions.addWidget(self.remove_button)
        card.body.addLayout(actions)

        self.body.addWidget(card, 1)
        self._update_buttons()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        backend = self.app.backend
        method = self.app.auth_method

        if backend.demo:
            self.notice.setText(
                f"<b style='color:{theme.WARNING}'>MODO DEMO</b> — {backend.reason} "
                f"Las cuentas que ves son simuladas y nada afecta a Wallapop."
            )
            self.details_button.setVisible(True)
        elif self.app.browser_auth is not None:
            self.notice.setText(
                f"<b style='color:{theme.SUCCESS}'>WALLAPOP (NAVEGADOR)</b> — las cuentas "
                f"conectadas aquí son cuentas autorizadas para el uso personal del titular de "
                f"LOT Bot. No es una integración oficial de Wallapop. Tú inicias sesión en el "
                f"navegador; LOT Bot no ve ni guarda tu contraseña y cada cuenta tiene su "
                f"propio perfil de navegador aislado."
            )
            self.details_button.setVisible(False)
        else:
            self.notice.setText(
                f"<b style='color:{theme.SUCCESS}'>WALLAPOP REAL</b> — acceso mediante "
                f"«{method.describe()}». LOT Bot no guarda tu contraseña: solo la "
                f"credencial de acceso, cifrada."
            )
            self.details_button.setVisible(bool(backend.missing))

        accounts = self.app.accounts.list_accounts()
        self._accounts = accounts
        demo_mode = self.app.demo_mode

        fill_table(
            self.table,
            [
                [
                    a.alias,
                    a.internal_ref,
                    self._status_text(a, demo_mode),
                    a.auth_method_label,
                    a.wallapop_login or "—",
                    a.last_sync_at.strftime("%d/%m/%Y %H:%M") if a.last_sync_at else "nunca",
                ]
                for a in accounts
            ],
            row_data=[a.internal_ref for a in accounts],
            colorizer=lambda row, col, value: (
                self._status_color(accounts[row], demo_mode)
                if col == 2 and row < len(accounts)
                else None
            ),
        )
        connected = sum(1 for a in accounts if self._really_connected(a, demo_mode))
        self.header.set_subtitle(
            f"{len(accounts)} cuenta(s) · {connected} conectada(s) · "
            f"cada una con sus propios anuncios, mensajes y automatizaciones"
        )
        self._update_buttons()

    # ------------------------------------------------------------------
    @staticmethod
    def _really_connected(account, demo_mode: bool) -> bool:
        """En modo real, una cuenta de demostración NO está conectada.

        Es la regla de no mostrar jamás algo DEMO como si fuera Wallapop real.
        """
        if account.is_demo and not demo_mode:
            return False
        return account.is_connected

    @staticmethod
    def _status_text(account, demo_mode: bool) -> str:
        if account.is_demo and not demo_mode:
            return "Solo demostración"
        return account.status_label

    @staticmethod
    def _status_color(account, demo_mode: bool) -> str:
        if account.is_demo and not demo_mode:
            return theme.WARNING
        return theme.STATUS_COLORS.get(account.status.value, theme.TEXT_MUTED)

    # ------------------------------------------------------------------
    def _selected(self):
        refs = selected_row_data(self.table)
        if not refs:
            return None
        return next((a for a in self._accounts if a.internal_ref == refs[0]), None)

    def _update_buttons(self) -> None:
        account = self._selected()
        has = account is not None
        for button in (
            self.disconnect_button,
            self.sync_button,
            self.rename_button,
            self.remove_button,
        ):
            button.setEnabled(has)

        browser_mode = self.app.browser_auth is not None
        browser_account = has and browser_mode and not account.is_demo
        self.browser_button.setVisible(browser_mode)
        self.check_button.setVisible(browser_mode)
        self.sync_button.setVisible(not browser_mode)
        self.reauth_button.setVisible(not browser_mode)
        if getattr(self, "generic_add_button", None) is not None:
            self.generic_add_button.setVisible(not browser_mode)
        self.connect_button.setText("Reconectar" if browser_mode else "Conectar")
        self.browser_button.setEnabled(
            browser_account and account.auth_method == AuthKind.BROWSER_SESSION.value
        )
        self.check_button.setEnabled(
            browser_account and account.auth_method == AuthKind.BROWSER_SESSION.value
        )
        if browser_mode:
            self.connect_button.setEnabled(browser_account)
            return
        method_ready = self.app.auth_method.is_ready
        conectada = bool(account) and self._really_connected(account, self.app.demo_mode)
        self.connect_button.setEnabled(has and method_ready and not conectada)
        self.reauth_button.setEnabled(has and method_ready and conectada)

        if not method_ready:
            tip = (
                "El mecanismo de acceso autorizado todavía no tiene todos los datos "
                "técnicos. Pulsa «Ver qué falta para conectar con Wallapop»."
            )
            self.connect_button.setToolTip(tip)
            self.reauth_button.setToolTip(tip)
        else:
            self.connect_button.setToolTip("")
            self.reauth_button.setToolTip("")

    # ------------------------------------------------------------------
    def _add_account(self) -> None:
        dialog = AddAccountDialog(self.app, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        alias = dialog.account_alias()
        if not alias:
            show_error(self, "La cuenta necesita un nombre.")
            return
        try:
            account = self.app.accounts.add_account(alias, is_demo=self.app.demo_mode)
        except ValueError as exc:
            show_error(self, str(exc))
            return
        self.app.audit.record_success("Alta de cuenta", target=account.alias)
        self.refresh()

        if self.app.demo_mode:
            info_box(
                self,
                "Cuenta añadida",
                f"«{account.alias}» creada en modo demostración.\n\n"
                f"Funciona con datos simulados: no afecta a Wallapop.",
            )
        elif self.app.auth_method.is_ready:
            self._connect_account(account)
        else:
            self._show_missing()

    # ------------------------------------------------------------------
    def _connect(self) -> None:
        account = self._selected()
        if account is not None:
            self._connect_account(account)

    def _reauthenticate(self) -> None:
        account = self._selected()
        if account is None:
            return
        if not ask_confirmation(
            self,
            "Volver a autenticar",
            f"Se sustituirá la sesión guardada de «{account.alias}» por una nueva.\n\n"
            f"Tendrás que iniciar sesión otra vez en Wallapop.",
        ):
            return
        self._connect_account(account, reauth=True)

    def _connect_account(self, account, reauth: bool = False) -> None:
        """Lanza el mecanismo de acceso que corresponda."""
        method = self.app.auth_method
        if self.app.browser_auth is not None and not account.is_demo:
            self._browser_login(account, new=False)
            return

        if method.kind is AuthKind.DEMO and not self.app.demo_mode:
            self._show_missing()
            return

        context: dict = {}
        if method.kind is AuthKind.DELEGATED_CREDENTIAL:
            dialog = DelegatedCredentialDialog(
                account.alias,
                instructions=self.app.access_profile.auth.delegated.instructions,
                parent=self,
            )
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            context["credential"] = dialog.value()
        elif method.interactive:
            info_box(
                self,
                "Autenticación en Wallapop",
                "Se abrirá tu navegador en Wallapop.\n\n"
                "Inicia sesión con esta cuenta, completa los pasos de seguridad que "
                "Wallapop te pida y autoriza el acceso.\n\n"
                "LOT Bot no ve ni guarda tu contraseña.",
            )

        self.connect_button.setEnabled(False)
        self.connect_button.setText("Autenticando…")

        def work():
            if reauth:
                return self.app.accounts.reauthenticate(account.internal_ref, **context)
            return self.app.accounts.connect(account.internal_ref, **context)

        def success(outcome) -> None:
            if outcome.success:
                self.app.audit.record_success(
                    "Conexión de cuenta",
                    account_ref=account.internal_ref,
                    target=account.alias,
                    detail=method.describe(),
                )
                info_box(self, "Cuenta conectada", outcome.message)
            else:
                self.app.audit.record_error(
                    "Conexión de cuenta",
                    error=outcome.message,
                    account_ref=account.internal_ref,
                    target=account.alias,
                )
                if outcome.blocked_by_missing_data:
                    self._show_missing()
                else:
                    show_error(self, outcome.message)

        def done(_=None) -> None:
            self.connect_button.setText("Conectar")
            self.refresh()

        self.run_task(work, on_success=success, on_done=done)

    # ------------------------------------------------------------------
    # Integración por navegador
    # ------------------------------------------------------------------
    def _add_browser_account(self) -> None:
        if self.app.browser_auth is None:
            # Se puede activar aquí mismo, sin pasar por Configuración.
            if not ask_confirmation(
                self,
                "Activar la integración por navegador",
                "Ahora mismo LOT Bot está en modo demostración.\n\n"
                "¿Quieres activar la integración con Wallapop mediante navegador para "
                "conectar tu cuenta? (Se puede volver al modo demostración desde "
                "Configuración → Wallapop.)",
            ):
                return
            try:
                self.app.set_integration_mode("navegador")
            except Exception as exc:
                show_error(self, "No se ha podido activar la integración.", str(exc))
                return
            if self.app.browser_auth is None:
                show_error(
                    self,
                    "La integración por navegador no está disponible.",
                    self.app.backend.describe_missing() or self.app.backend.reason,
                )
                return
            window = self.window()
            if hasattr(window, "_update_status"):
                window._update_status()
            self.refresh()
        alias, accepted = QInputDialog.getText(
            self, "Añadir cuenta Wallapop", "Nombre para reconocer esta cuenta en LOT Bot:"
        )
        if not accepted or not alias.strip():
            return
        if not ask_confirmation(
            self,
            "Iniciar sesión en Wallapop",
            "Se abrirá una ventana del navegador con Wallapop.\n\n"
            "1. Inicia sesión tú mismo con la cuenta que quieras conectar.\n"
            "2. Si Wallapop te pide una verificación, complétala en esa ventana.\n"
            "3. Pulsa «Ya he iniciado sesión»: LOT Bot comprobará que la sesión es válida.\n\n"
            "LOT Bot no ve ni guarda tu contraseña. Esta conexión es para tu uso "
            "personal autorizado; no es una integración oficial de Wallapop.",
        ):
            return
        try:
            account = self.app.accounts.add_account(alias.strip(), is_demo=False)
        except ValueError as exc:
            show_error(self, str(exc))
            return
        self.app.audit.record_success("Alta de cuenta", target=account.alias, detail="navegador")
        self.refresh()
        self._browser_login(account, new=True)

    def _browser_login(self, account, *, new: bool) -> None:
        """Abre el navegador (que queda abierto) y solo conecta la cuenta si
        LOT Bot comprueba de verdad que hay sesión iniciada."""
        from lot_bot.ui.views.browser_login import BrowserLoginDialog

        method = self.app.browser_auth
        session = method.start_login(account.internal_ref)
        dialog = BrowserLoginDialog(session, account.alias, self)
        accepted = dialog.exec() == dialog.DialogCode.Accepted
        check = session.check if session.verified else None
        # Se cierra el navegador de inicio de sesión: las cookies quedan en el
        # perfil de la cuenta y la cola lo volverá a abrir cuando publique.
        self.run_task(session.close)

        if not accepted or check is None:
            self.app.audit.record_cancelled(
                "Conexión de cuenta por navegador", target=account.alias, detail=session.message
            )
            if new:
                self.app.accounts.remove_account(account.internal_ref)
            self.refresh()
            return
        shown = f"«{check.login}»" if check.login else "la cuenta con la que has iniciado sesión"
        if not ask_confirmation(
            self,
            "Confirmar cuenta",
            f"LOT Bot ha comprobado que hay una sesión válida de {shown}.\n\n"
            f"¿Conectarla como «{account.alias}»?\n\n"
            f"Quedará en un perfil de navegador exclusivo de esta cuenta, en tu "
            f"ordenador. Puedes borrarla con «Desconectar».",
        ):
            if new:
                self.app.accounts.remove_account(account.internal_ref)
            self.app.audit.record_cancelled("Conexión de cuenta por navegador", target=account.alias)
            self.refresh()
            return
        outcome = self.app.accounts.connect(account.internal_ref, method=method, session_check=check)
        if outcome.success:
            self.app.audit.record_success(
                "Conexión de cuenta",
                account_ref=account.internal_ref,
                target=account.alias,
                detail="Sesión comprobada en Wallapop",
            )
            info_box(self, "Cuenta conectada", f"«{account.alias}» está conectada.")
        else:
            show_error(self, outcome.message)
        self.refresh()

    def _open_browser(self) -> None:
        account = self._selected()
        method = self.app.browser_auth
        if account is None or method is None:
            return
        info_box(
            self,
            "Abrir cuenta",
            f"Se abrirá el navegador de «{account.alias}» con su sesión. Úsalo para lo que "
            f"necesites (por ejemplo, completar una verificación de Wallapop) y ciérralo "
            f"al terminar: mientras esté abierta, LOT Bot no puede publicar en esta cuenta "
            f"(Chrome no deja usar el mismo perfil dos veces).",
        )
        # No ocupa ningún hilo de la interfaz: el navegador queda abierto por su cuenta.
        method.open_for_user(account.internal_ref)

    def _check_connection(self) -> None:
        account = self._selected()
        method = self.app.browser_auth
        if account is None or method is None:
            return
        self.check_button.setEnabled(False)
        self.check_button.setText("Comprobando…")

        def success(check) -> None:
            if check.state in ("desconocido", "error"):
                # No se sabe con seguridad: no se cambia el estado de la cuenta.
                from lot_bot.wallapop.browser.auth import UNCONFIRMED

                show_error(self, UNCONFIRMED, check.message)
                return
            self.app.accounts.mark_session_checked(account.internal_ref, check.ok, check.message)
            self.app.audit.record(
                "Comprobación de sesión",
                result="ok" if check.ok else "error",
                account_ref=account.internal_ref,
                target=account.alias,
                detail=check.message,
            )
            if check.ok:
                info_box(self, "Sesión válida", f"«{account.alias}» tiene la sesión iniciada.")
            else:
                show_error(self, f"«{account.alias}» no tiene una sesión válida.", check.message)

        def done(*_) -> None:
            self.check_button.setText("Comprobar conexión")
            self.refresh()

        self.run_task(method.check, account.internal_ref, on_success=success, on_done=done)

    # ------------------------------------------------------------------
    def _disconnect(self) -> None:
        account = self._selected()
        if account is None:
            return
        if not ask_confirmation(
            self,
            "Desconectar cuenta",
            f"Se borrará la credencial guardada de «{account.alias}» (y, si se conectó "
            f"por navegador, su sesión y cookies guardadas).\n\n"
            f"Los anuncios seguirán en Wallapop; solo se corta el acceso de LOT Bot.",
        ):
            return
        self.app.accounts.disconnect(account.internal_ref)
        self.app.audit.record_success(
            "Desconexión de cuenta", account_ref=account.internal_ref, target=account.alias
        )
        self.refresh()

    def _sync(self) -> None:
        account = self._selected()
        if account is None:
            return
        self.sync_button.setEnabled(False)
        self.sync_button.setText("Sincronizando…")

        def work():
            result = self.app.listings.sync_account(account.internal_ref)
            if self.app.messages.messaging_available:
                self.app.messages.sync_account(account.internal_ref)
            return result

        def success(result):
            info_box(
                self,
                "Sincronización terminada",
                f"Nuevos: {result.get('nuevos', 0)} · "
                f"Actualizados: {result.get('actualizados', 0)} · "
                f"Retirados: {result.get('retirados', 0)}",
            )

        def done(_=None):
            self.sync_button.setText("Sincronizar")
            self.refresh()

        self.run_task(work, on_success=success, on_done=done)

    def _rename(self) -> None:
        account = self._selected()
        if account is None:
            return
        alias, accepted = QInputDialog.getText(
            self, "Renombrar cuenta", "Nuevo nombre:", text=account.alias
        )
        if accepted and alias.strip():
            self.app.accounts.rename_account(account.internal_ref, alias.strip())
            self.refresh()

    def _remove(self) -> None:
        account = self._selected()
        if account is None:
            return
        if not ask_confirmation(
            self,
            "Eliminar cuenta",
            f"Se eliminará «{account.alias}» de LOT Bot junto con sus anuncios y "
            f"mensajes guardados en local.\n\n"
            f"Los anuncios publicados en Wallapop NO se tocan.",
            destructive=True,
        ):
            return
        self.app.accounts.remove_account(account.internal_ref)
        self.app.audit.record_success("Baja de cuenta", target=account.alias)
        self.refresh()

    def _show_missing(self) -> None:
        MissingAccessDialog(self.app, self).exec()
