"""AuthenticationManager: conexión de cuentas sin controlar un navegador.

Es lo que hay detrás de la pantalla «Conectar Wallapop». Ofrece, para cada
cuenta:

    iniciar conexión  ->  introducir credencial  ->  validar
    comprobar estado  ->  renovar  ->  reconectar  ->  desconectar

y guarda un historial de todo ello.

QUÉ HACE Y QUÉ NO HACE
----------------------
* Usa el mecanismo que Wallapop haya autorizado y que esté declarado en el
  perfil: credencial de integración provisional (transferencia manual
  documentada) declarada en el perfil de acceso. Es el único mecanismo de
  la conexión directa.
* La credencial se guarda cifrada (lo hace `AccountManager`) y nunca se
  muestra, ni se escribe en los logs, ni llega a la IA.
* NO extrae cookies ni credenciales del navegador, NO usa tokens internos,
  NO automatiza formularios de acceso y NO intenta saltarse CAPTCHA ni
  verificaciones. Si Wallapop exige un paso, lo hace el usuario.

El estado de cada conexión (conectada, hay que reconectar, error…) se guarda
en `wallapop_connections` y cada acción en `wallapop_connection_events`.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from lot_bot.database.models import AccountStatus, ConnectionEvent, WallapopConnection
from lot_bot.integration.database_manager import DatabaseManager
from lot_bot.integration.wallapop_client import ErrorKind, WallapopClient, classify_error
from lot_bot.wallapop.account_manager import AccountInfo, AccountManager
from lot_bot.wallapop.auth.demo import DemoAuthMethod
from lot_bot.wallapop.errors import AuthenticationError, WallapopError

logger = logging.getLogger(__name__)

#: Una credencial que caduca antes de esto se muestra como «caduca pronto».
EXPIRING_SOON = timedelta(hours=24)


class AuthState(str, enum.Enum):
    CONNECTED = "connected"
    EXPIRING = "expiring"
    NEEDS_RECONNECT = "needs_reconnect"
    NOT_CONNECTED = "not_connected"
    ERROR = "error"
    DEMO = "demo"


AUTH_STATE_LABELS = {
    AuthState.CONNECTED: "Conectada y activa",
    AuthState.EXPIRING: "Activa · caduca pronto",
    AuthState.NEEDS_RECONNECT: "Hay que reconectar",
    AuthState.NOT_CONNECTED: "Sin conectar",
    AuthState.ERROR: "Error de conexión",
    AuthState.DEMO: "Demostración (simulada)",
}

EVENT_LABELS = {
    "connect_started": "Inicio de conexión",
    "connected": "Cuenta conectada",
    "validated": "Conexión validada",
    "validation_failed": "Validación fallida",
    "check_ok": "Comprobación correcta",
    "check_failed": "Comprobación fallida",
    "renewed": "Credencial renovada",
    "renew_unavailable": "Renovación no disponible",
    "rejected": "Wallapop rechazó la credencial",
    "disconnected": "Cuenta desconectada",
    "error": "Error",
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(slots=True)
class ConnectionStatus:
    """Todo lo que la pantalla muestra de una cuenta. Sin secretos."""

    account_ref: str
    alias: str
    account_status: str
    auth_state: AuthState
    provider: str
    provider_label: str
    method_label: str
    is_demo: bool
    login: str | None = None
    last_connected_at: datetime | None = None
    last_validated_at: datetime | None = None
    last_check_at: datetime | None = None
    last_check_ok: bool | None = None
    expires_at: datetime | None = None
    detail: str = ""

    @property
    def auth_label(self) -> str:
        return AUTH_STATE_LABELS[self.auth_state]

    @property
    def is_usable(self) -> bool:
        return self.auth_state in (AuthState.CONNECTED, AuthState.EXPIRING, AuthState.DEMO)

    @property
    def needs_reconnect(self) -> bool:
        return self.auth_state in (AuthState.NEEDS_RECONNECT, AuthState.ERROR)


@dataclass(slots=True)
class ConnectionResult:
    """Resultado de una acción de la pantalla."""

    ok: bool
    message: str
    status: ConnectionStatus | None = None
    #: Dirección a abrir en el navegador del sistema (portal documentado).
    open_url: str = ""
    #: True si ahora hay que pegar la credencial.
    awaiting_credential: bool = False


class AuthenticationManager:
    """Ciclo de vida de la conexión directa de cada cuenta."""

    def __init__(
        self,
        db: DatabaseManager,
        accounts: AccountManager,
        client: WallapopClient,
        audit: Any = None,
    ) -> None:
        self._db = db
        self._accounts = accounts
        self._client = client
        self._audit = audit
        #: f(account_ref) cuando una cuenta queda lista para publicar.
        self.ready_listeners: list[Callable[[str], None]] = []

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------
    def procedure(self):
        return self._client.connection_procedure()

    def accounts(self) -> list[AccountInfo]:
        return self._accounts.list_accounts()

    def add_account(self, alias: str) -> AccountInfo:
        if not alias.strip():
            raise ValueError("Escribe un nombre para la cuenta (por ejemplo, «Tienda Madrid»).")
        info = self._accounts.add_account(alias.strip(), is_demo=self._client.is_demo)
        if self._client.is_demo:
            self._set_state(info.internal_ref, AuthState.DEMO, connected=True)
        self._event(info.internal_ref, "connect_started", True, f"Cuenta «{info.alias}» añadida.")
        return info

    def status(self, account_ref: str) -> ConnectionStatus:
        info = self._accounts.get_account(account_ref)
        if info is None:
            raise ValueError(f"No existe la cuenta '{account_ref}'.")
        with self._db.session() as session:
            row = self._row(session, account_ref, create=False)
            data = {
                "state": row.auth_state if row else None,
                "provider": row.provider if row else None,
                "last_connected_at": row.last_connected_at if row else None,
                "last_validated_at": row.last_validated_at if row else None,
                "last_check_at": row.last_check_at if row else None,
                "last_check_ok": row.last_check_ok if row else None,
                "last_error": row.last_error if row else None,
                "login": row.remote_login if row else None,
            }
        state = self._derive_state(info, data["state"])
        detail = data["last_error"] or info.status_detail or ""
        if state in (AuthState.CONNECTED, AuthState.EXPIRING, AuthState.DEMO):
            detail = "" if data["last_check_ok"] is not False else detail
        return ConnectionStatus(
            account_ref=account_ref,
            alias=info.alias,
            account_status=info.status_label,
            auth_state=state,
            provider=data["provider"] or self._client.provider,
            provider_label=self._client.provider_label,
            method_label=info.auth_method_label,
            is_demo=info.is_demo,
            login=data["login"] or info.wallapop_login,
            last_connected_at=data["last_connected_at"],
            last_validated_at=data["last_validated_at"],
            last_check_at=data["last_check_at"],
            last_check_ok=data["last_check_ok"],
            expires_at=info.token_expires_at,
            detail=detail,
        )

    def statuses(self) -> list[ConnectionStatus]:
        return [self.status(a.internal_ref) for a in self._accounts.list_accounts()]

    def _derive_state(self, info: AccountInfo, stored: str | None) -> AuthState:
        if info.is_demo:
            # Una cuenta de demostración nunca puede publicar en Wallapop real.
            usable = info.is_connected and self._client.is_demo
            return AuthState.DEMO if usable else AuthState.NOT_CONNECTED
        if not self._accounts.has_credential(info.internal_ref):
            return AuthState.ERROR if stored == AuthState.ERROR.value else AuthState.NOT_CONNECTED
        if stored in (AuthState.NEEDS_RECONNECT.value, AuthState.ERROR.value):
            return AuthState(stored)
        expires = info.token_expires_at
        if expires is not None:
            remaining = expires - _now()
            if remaining <= timedelta(0) and not self.procedure().can_renew:
                return AuthState.NEEDS_RECONNECT
            if remaining <= EXPIRING_SOON:
                return AuthState.EXPIRING
        return AuthState.CONNECTED

    def history(self, account_ref: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        aliases = {a.internal_ref: a.alias for a in self._accounts.list_accounts()}
        with self._db.session() as session:
            query = select(ConnectionEvent).order_by(ConnectionEvent.id.desc()).limit(limit)
            if account_ref:
                query = query.where(ConnectionEvent.account_ref == account_ref)
            return [
                {
                    "fecha": e.at,
                    "cuenta": aliases.get(e.account_ref, e.account_ref),
                    "cuenta_ref": e.account_ref,
                    "evento": EVENT_LABELS.get(e.event, e.event),
                    "codigo": e.event,
                    "ok": e.ok,
                    "detalle": e.detail,
                }
                for e in session.scalars(query).all()
            ]

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def start_connection(self, account_ref: str) -> ConnectionResult:
        """Paso 1. Según el mecanismo: abre el portal documentado, lanza el
        flujo de autorización o conecta la cuenta de demostración."""
        self._require_account(account_ref)
        procedure = self.procedure()
        if not self._client.can_publish_directly:
            return ConnectionResult(
                False,
                "Activa la conexión por credencial (token) en «Tipo de conexión»: "
                "la conexión directa no usa el navegador.",
            )
        if not procedure.ready:
            return ConnectionResult(
                False,
                "Todavía no se puede conectar. Falta: " + "; ".join(procedure.missing),
            )
        self._event(account_ref, "connect_started", True, procedure.title)

        if self._client.is_demo:
            outcome = self._accounts.connect(account_ref, method=DemoAuthMethod())
            if not outcome.success:
                return self._failed(account_ref, outcome.message)
            self._set_state(account_ref, AuthState.DEMO, connected=True)
            self._event(account_ref, "connected", True, "Cuenta de demostración.")
            self._notify_ready(account_ref)
            return ConnectionResult(True, outcome.message, self.status(account_ref))

        if procedure.needs_credential:
            message = (
                "Se ha abierto la página de Wallapop para obtener la credencial. Pégala "
                "en «Credencial de integración» y pulsa «Validar conexión»."
                if procedure.portal_url
                else "Pega la credencial de integración que te ha dado Wallapop y pulsa "
                "«Validar conexión»."
            )
            return ConnectionResult(
                True,
                message,
                self.status(account_ref),
                open_url=procedure.portal_url,
                awaiting_credential=True,
            )

        return ConnectionResult(
            False, "La conexión directa solo admite la credencial (token) de integración."
        )

    def submit_credential(self, account_ref: str, credential: str) -> ConnectionResult:
        """Paso 2. Guarda (cifrada) la credencial provisional y la valida."""
        self._require_account(account_ref)
        procedure = self.procedure()
        if not procedure.needs_credential:
            return ConnectionResult(
                False, "El tipo de conexión activo no usa una credencial que se pegue a mano."
            )
        if not procedure.ready:
            return ConnectionResult(
                False, "Todavía no se puede conectar. Falta: " + "; ".join(procedure.missing)
            )
        value = (credential or "").strip()
        if not value:
            return ConnectionResult(False, "Pega primero la credencial de integración.")
        if "\n" in value or "\r" in value:
            return ConnectionResult(
                False, "La credencial no puede tener saltos de línea: cópiala de nuevo."
            )
        outcome = self._accounts.connect(account_ref, credential=value)
        if not outcome.success:
            return self._failed(account_ref, outcome.message)
        self._set_state(account_ref, AuthState.CONNECTED, connected=True)
        self._event(account_ref, "connected", True, "Credencial guardada cifrada.")
        result = self.validate(account_ref)
        if not result.ok and result.status and result.status.auth_state is AuthState.ERROR:
            # Wallapop la ha rechazado: no se conserva una credencial inútil.
            self._accounts.disconnect(account_ref, revoke=False)
            self._set_state(account_ref, AuthState.ERROR, error=result.message)
            result.status = self.status(account_ref)
        return result

    def validate(self, account_ref: str) -> ConnectionResult:
        """Comprueba DE VERDAD, contra Wallapop, que la credencial funciona."""
        self._require_account(account_ref)
        if self._client.is_demo:
            ok = self._client.check(account_ref)
            self._set_state(account_ref, AuthState.DEMO, validated=ok, check_ok=ok)
            self._event(account_ref, "validated" if ok else "validation_failed", ok, "Demostración.")
            return ConnectionResult(ok, "Conexión de demostración correcta." if ok else
                                    "La cuenta de demostración no responde.", self.status(account_ref))
        try:
            profile = self._client.verify_account(account_ref)
        except WallapopError as exc:
            kind = classify_error(exc)
            if kind is ErrorKind.AUTH:
                self._set_state(account_ref, AuthState.ERROR, error=exc.user_message, check_ok=False)
                message = (
                    "Wallapop ha rechazado la credencial. Comprueba que la has copiado "
                    "entera y que sigue vigente."
                )
                self._event(account_ref, "rejected", False, exc.detail or message)
                return ConnectionResult(False, message, self.status(account_ref))
            self._set_state(account_ref, None, error=exc.user_message, check_ok=False)
            self._event(account_ref, "validation_failed", False, exc.user_message)
            return ConnectionResult(
                False,
                f"No se ha podido validar ahora: {exc.user_message} La credencial se "
                "conserva; vuelve a pulsar «Comprobar estado» más tarde.",
                self.status(account_ref),
            )
        login = None
        if profile is not None:
            login = profile.login or profile.display_name
        self._set_state(
            account_ref, AuthState.CONNECTED, validated=True, check_ok=True, login=login
        )
        self._accounts.mark_session_checked(account_ref, True)
        self._event(account_ref, "validated", True, f"Usuario: {login}" if login else "")
        self._notify_ready(account_ref)
        return ConnectionResult(
            True,
            "Conexión validada: LOT Bot ya puede publicar con esta cuenta.",
            self.status(account_ref),
        )

    def check(self, account_ref: str) -> ConnectionResult:
        """¿Sigue activa la autenticación? Renueva sola si el mecanismo puede."""
        info = self._require_account(account_ref)
        if info.is_demo:
            return self.validate(account_ref)
        if not self._accounts.has_credential(account_ref):
            self._set_state(account_ref, AuthState.NOT_CONNECTED)
            return ConnectionResult(False, "La cuenta no está conectada.", self.status(account_ref))
        try:
            # Descifra y, si está a punto de caducar, la renueva sola.
            self._accounts.get_credential(account_ref)
            ok = self._client.check(account_ref)
        except AuthenticationError as exc:
            return self._needs_reconnect(account_ref, exc.user_message)
        except WallapopError as exc:
            self._set_state(account_ref, None, error=exc.user_message, check_ok=False)
            self._event(account_ref, "check_failed", False, exc.user_message)
            return ConnectionResult(
                False,
                f"No se ha podido comprobar ahora: {exc.user_message}",
                self.status(account_ref),
            )
        if not ok:
            return self._needs_reconnect(account_ref, "Wallapop ya no acepta la credencial.")
        self._set_state(account_ref, AuthState.CONNECTED, check_ok=True)
        self._event(account_ref, "check_ok", True, "")
        self._notify_ready(account_ref)
        return ConnectionResult(True, "La conexión sigue activa.", self.status(account_ref))

    def check_all(self) -> dict[str, ConnectionResult]:
        return {a.internal_ref: self.check(a.internal_ref) for a in self._accounts.list_accounts()}

    def renew(self, account_ref: str) -> ConnectionResult:
        """Renueva la credencial sin molestar al usuario, si se puede."""
        info = self._require_account(account_ref)
        if info.is_demo:
            return ConnectionResult(True, "Las cuentas de demostración no caducan.",
                                    self.status(account_ref))
        if not self.procedure().can_renew:
            self._event(account_ref, "renew_unavailable", False, "")
            return ConnectionResult(
                False,
                "Esta credencial no se puede renovar automáticamente. Pulsa «Reconectar» "
                "e introduce la nueva credencial que te dé Wallapop.",
                self.status(account_ref),
            )
        if self._accounts.force_renew(account_ref):
            self._set_state(account_ref, AuthState.CONNECTED, connected=True)
            self._event(account_ref, "renewed", True, "")
            return self.validate(account_ref)
        return self._needs_reconnect(account_ref, "No se ha podido renovar la credencial.")

    def reconnect(self, account_ref: str) -> ConnectionResult:
        """Vuelve a empezar la conexión (la credencial actual se mantiene
        hasta que la nueva quede validada)."""
        return self.start_connection(account_ref)

    def disconnect(self, account_ref: str) -> ConnectionResult:
        self._require_account(account_ref)
        self._accounts.disconnect(account_ref, revoke=True)
        self._set_state(account_ref, AuthState.NOT_CONNECTED)
        self._event(account_ref, "disconnected", True, "Credencial eliminada de este equipo.")
        if self._audit is not None:
            self._audit.record_success("Cuenta desconectada", account_ref=account_ref, actor="usuario")
        return ConnectionResult(
            True,
            "Cuenta desconectada. Sus anuncios pendientes se conservan en la cola y se "
            "publicarán cuando la vuelvas a conectar.",
            self.status(account_ref),
        )

    # ------------------------------------------------------------------
    # Para el motor de publicación
    # ------------------------------------------------------------------
    def ensure_ready(self, account_ref: str) -> None:
        """Lanza AuthenticationError si la cuenta no puede publicar ahora."""
        info = self._accounts.get_account(account_ref)
        if info is None:
            raise AuthenticationError(
                f"La cuenta '{account_ref}' no existe.",
                user_message="La cuenta de este anuncio ya no existe en LOT Bot.",
            )
        if info.is_demo:
            if not self._client.is_demo:
                raise AuthenticationError(
                    user_message=(
                        f"«{info.alias}» es una cuenta de demostración: no puede publicar en "
                        "Wallapop real. Añade una cuenta real en «Conectar Wallapop»."
                    )
                )
            if not info.is_connected:
                raise AuthenticationError(user_message="La cuenta de demostración no está conectada.")
            return
        status = self.status(account_ref)
        if status.auth_state in (AuthState.NOT_CONNECTED, AuthState.NEEDS_RECONNECT, AuthState.ERROR):
            raise AuthenticationError(
                f"Cuenta {account_ref}: {status.auth_state.value}",
                user_message=(
                    f"La cuenta «{info.alias}» necesita reconectarse en «Conectar Wallapop»."
                ),
            )
        try:
            self._accounts.get_credential(account_ref)  # renueva si hace falta
        except AuthenticationError as exc:
            self._needs_reconnect(account_ref, exc.user_message)
            raise

    def mark_rejected(self, account_ref: str, detail: str) -> None:
        """Wallapop ha rechazado la credencial durante una publicación."""
        self._needs_reconnect(account_ref, detail)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _require_account(self, account_ref: str) -> AccountInfo:
        info = self._accounts.get_account(account_ref)
        if info is None:
            raise ValueError("Elige primero una cuenta.")
        return info

    def _needs_reconnect(self, account_ref: str, detail: str) -> ConnectionResult:
        self._set_state(account_ref, AuthState.NEEDS_RECONNECT, error=detail, check_ok=False)
        self._event(account_ref, "check_failed", False, detail)
        return ConnectionResult(
            False,
            f"{detail} Pulsa «Reconectar». Los anuncios pendientes de esta cuenta "
            "esperan en la cola sin perderse.",
            self.status(account_ref),
        )

    def _failed(self, account_ref: str, message: str) -> ConnectionResult:
        self._set_state(account_ref, AuthState.ERROR, error=message)
        self._event(account_ref, "error", False, message)
        return ConnectionResult(False, message, self.status(account_ref))

    def _notify_ready(self, account_ref: str) -> None:
        for listener in list(self.ready_listeners):
            try:
                listener(account_ref)
            except Exception:  # la interfaz o el motor nunca deben romper esto
                logger.debug("Aviso de cuenta lista fallido", exc_info=True)

    @staticmethod
    def _row(session, account_ref: str, create: bool = True) -> WallapopConnection | None:
        row = session.scalar(
            select(WallapopConnection).where(WallapopConnection.account_ref == account_ref)
        )
        if row is None and create:
            row = WallapopConnection(account_ref=account_ref)
            session.add(row)
        return row

    def _set_state(
        self,
        account_ref: str,
        state: AuthState | None,
        *,
        connected: bool = False,
        validated: bool = False,
        check_ok: bool | None = None,
        error: str | None = None,
        login: str | None = None,
    ) -> None:
        now = _now()
        with self._db.session() as session:
            row = self._row(session, account_ref)
            row.provider = self._client.provider
            if state is not None:
                row.auth_state = state.value
            if connected:
                row.last_connected_at = now
            if validated:
                row.last_validated_at = now
            if check_ok is not None:
                row.last_check_at = now
                row.last_check_ok = check_ok
            if error is not None:
                row.last_error = error[:500]
            elif state in (AuthState.CONNECTED, AuthState.DEMO, AuthState.NOT_CONNECTED):
                row.last_error = None
            if login:
                row.remote_login = login[:200]
        if state is AuthState.NOT_CONNECTED:
            with self._db.session() as session:
                from lot_bot.database.models import Account

                account = session.scalar(
                    select(Account).where(Account.internal_ref == account_ref)
                )
                if account is not None and not account.credential_enc:
                    account.status = AccountStatus.DISCONNECTED

    def _event(self, account_ref: str, event: str, ok: bool, detail: str) -> None:
        with self._db.session() as session:
            session.add(
                ConnectionEvent(
                    account_ref=account_ref,
                    event=event,
                    ok=ok,
                    provider=self._client.provider,
                    detail=(detail or "")[:1000],
                )
            )
        logger.info("Conexión %s: %s (%s)", account_ref, event, "ok" if ok else "fallo")
