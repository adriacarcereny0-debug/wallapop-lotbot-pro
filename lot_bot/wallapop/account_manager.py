"""Gestion de cuentas de Wallapop y de sus credenciales de acceso.

INDEPENDIENTE DEL MECANISMO
---------------------------
Este modulo no sabe si la cuenta se conecto por OAuth, por una sesion
autorizada o con una credencial delegada. Solo sabe que cada cuenta tiene una
`AuthCredential` opaca que:

  * se guarda cifrada con la clave maestra local,
  * se descifra unicamente en el momento de hacer la peticion,
  * se renueva o se invalida segun diga su mecanismo,
  * y se puede revocar desde la aplicacion.

GARANTIAS DE AISLAMIENTO MULTICUENTA
------------------------------------
  * Cada cuenta tiene un `internal_ref` unico que identifica todo lo suyo.
  * Las credenciales se guardan por cuenta y nunca se comparten.
  * Cada cuenta recuerda con que mecanismo se conecto.
  * Nunca se guarda la contrasena de Wallapop.
  * Ninguna credencial aparece en pantalla, en los logs ni en la IA.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from lot_bot.config.secrets import SecretBox, SecretsError, get_secret_box
from lot_bot.database.engine import Database
from lot_bot.database.models import Account, AccountStatus
from lot_bot.logs.redaction import register_secret
from lot_bot.wallapop.auth import AuthCredential, AuthKind, AuthMethod, AuthOutcome
from lot_bot.wallapop.auth.demo import DemoAuthMethod
from lot_bot.wallapop.dto import OAuthTokens
from lot_bot.wallapop.errors import AuthenticationError

logger = logging.getLogger(__name__)

#: Margen antes de la caducidad a partir del cual se renueva la credencial.
RENEWAL_MARGIN = timedelta(minutes=5)


@dataclass(slots=True)
class AccountInfo:
    """Vista de solo lectura de una cuenta, apta para la interfaz.

    NO contiene credenciales: la interfaz nunca las necesita y nunca debe
    poder mostrarlas por accidente.
    """

    id: int
    internal_ref: str
    alias: str
    status: AccountStatus
    status_detail: str | None
    wallapop_login: str | None
    wallapop_user_id: str | None
    last_sync_at: datetime | None
    token_expires_at: datetime | None
    is_demo: bool
    scopes: list[str]
    auth_method: str | None = None

    @property
    def is_connected(self) -> bool:
        return self.status == AccountStatus.CONNECTED

    @property
    def status_label(self) -> str:
        return {
            AccountStatus.CONNECTED: "Conectada",
            AccountStatus.DISCONNECTED: "Desconectada",
            AccountStatus.EXPIRED: "Sesión caducada",
            AccountStatus.ERROR: "Error",
        }[self.status]

    @property
    def needs_reauthentication(self) -> bool:
        return self.status in (AccountStatus.EXPIRED, AccountStatus.ERROR)

    @property
    def auth_method_label(self) -> str:
        from lot_bot.wallapop.auth.base import AUTH_KIND_LABELS

        if not self.auth_method:
            return "—"
        try:
            return AUTH_KIND_LABELS[AuthKind(self.auth_method)]
        except ValueError:
            return self.auth_method


class AccountManager:
    """Alta, conexion, renovacion y revocacion de credenciales por cuenta."""

    def __init__(
        self,
        database: Database,
        secret_box: SecretBox | None = None,
        auth_method: AuthMethod | None = None,
    ) -> None:
        self._db = database
        self._box = secret_box or get_secret_box()
        self._auth = auth_method or DemoAuthMethod()
        #: Perfiles de navegador por cuenta (integración por navegador).
        self._browser_profiles: Any = None
        #: Funciones f(internal_ref) avisadas cuando una cuenta vuelve a
        #: tener sesión válida (p. ej. la cola continúa tras «Reconectar»).
        self.connected_listeners: list[Any] = []

    def _notify_connected(self, internal_ref: str) -> None:
        for listener in list(self.connected_listeners):
            try:
                listener(internal_ref)
            except Exception:  # un aviso nunca debe romper la conexión
                logger.exception("Error avisando de la reconexión de %s", internal_ref)

    def set_browser_profiles(self, store: Any, release: Any = None) -> None:
        """Almacén de perfiles de navegador: al desconectar o eliminar una
        cuenta conectada por navegador, se cierra su navegador (`release`) y
        se borra su sesión guardada."""
        self._browser_profiles = store
        self._browser_release = release

    def _forget_browser_session(self, internal_ref: str) -> None:
        if self._browser_profiles is None:
            return
        try:
            release = getattr(self, "_browser_release", None)
            if release is not None:
                release(internal_ref)
            self._browser_profiles.delete(internal_ref)
        except Exception as exc:  # nunca debe impedir desconectar
            logger.warning("No se ha podido borrar la sesión del navegador: %s", type(exc).__name__)

    # ------------------------------------------------------------------
    # Mecanismo activo
    # ------------------------------------------------------------------
    @property
    def auth_method(self) -> AuthMethod:
        return self._auth

    def set_auth_method(self, method: AuthMethod) -> None:
        self._auth = method
        logger.info("Mecanismo de acceso activo: %s", method.describe())

    def set_oauth_client(self, client: Any) -> None:
        """Compatibilidad con la versión anterior. Ya no se usa: el mecanismo
        de autenticación se configura con `set_auth_method`."""
        logger.debug("set_oauth_client está obsoleto; usa set_auth_method.")

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------
    def list_accounts(self, include_demo: bool = True) -> list[AccountInfo]:
        with self._db.session_scope() as session:
            accounts = session.scalars(select(Account).order_by(Account.id)).all()
            return [self._to_info(a) for a in accounts if include_demo or not a.is_demo]

    def get_account(self, internal_ref: str) -> AccountInfo | None:
        with self._db.session_scope() as session:
            account = self._find(session, internal_ref)
            return self._to_info(account) if account else None

    def get_account_by_id(self, account_id: int) -> AccountInfo | None:
        with self._db.session_scope() as session:
            account = session.get(Account, account_id)
            return self._to_info(account) if account else None

    def resolve_ref(self, identifier: str | int) -> str | None:
        """Acepta id numerico, internal_ref o alias y devuelve el internal_ref."""
        with self._db.session_scope() as session:
            if isinstance(identifier, int) or str(identifier).isdigit():
                account = session.get(Account, int(identifier))
                return account.internal_ref if account else None
            text = str(identifier).strip()
            account = session.scalar(select(Account).where(Account.internal_ref == text))
            if account:
                return account.internal_ref
            account = session.scalar(select(Account).where(Account.alias == text))
            if account:
                return account.internal_ref
            lowered = text.lower()
            for candidate in session.scalars(select(Account)).all():
                if candidate.alias.lower() == lowered or lowered in candidate.alias.lower():
                    return candidate.internal_ref
            return None

    @staticmethod
    def _find(session, internal_ref: str) -> Account | None:
        return session.scalar(select(Account).where(Account.internal_ref == internal_ref))

    @staticmethod
    def _to_info(account: Account) -> AccountInfo:
        return AccountInfo(
            id=account.id,
            internal_ref=account.internal_ref,
            alias=account.alias,
            status=account.status,
            status_detail=account.status_detail,
            wallapop_login=account.wallapop_login,
            wallapop_user_id=account.wallapop_user_id,
            last_sync_at=account.last_sync_at,
            token_expires_at=account.token_expires_at,
            is_demo=account.is_demo,
            scopes=(account.scopes or "").split(),
            auth_method=account.auth_method,
        )

    # ------------------------------------------------------------------
    # Alta y baja
    # ------------------------------------------------------------------
    def add_account(
        self, alias: str, *, is_demo: bool = False, internal_ref: str | None = None
    ) -> AccountInfo:
        ref = internal_ref or f"acc-{uuid.uuid4().hex[:10]}"
        with self._db.session_scope() as session:
            if self._find(session, ref):
                raise ValueError(f"Ya existe una cuenta con la referencia '{ref}'.")
            account = Account(
                internal_ref=ref,
                alias=alias.strip() or ref,
                is_demo=is_demo,
                status=AccountStatus.DISCONNECTED,
                status_detail=None,
                auth_method=AuthKind.DEMO.value if is_demo else None,
            )
            session.add(account)
            session.flush()
            logger.info("Cuenta añadida: %s (%s)", account.alias, ref)
            info = self._to_info(account)

        if is_demo:
            # Las cuentas DEMO quedan conectadas al instante, con una
            # credencial marcada como simulada.
            self.connect(ref, method=DemoAuthMethod())
            return self.get_account(ref) or info
        return info

    def rename_account(self, internal_ref: str, alias: str) -> AccountInfo | None:
        with self._db.session_scope() as session:
            account = self._find(session, internal_ref)
            if account is None:
                return None
            account.alias = alias.strip() or account.alias
            session.flush()
            return self._to_info(account)

    def remove_account(self, internal_ref: str) -> bool:
        """Elimina la cuenta y TODO lo que le pertenece (anuncios, mensajes)."""
        with self._db.session_scope() as session:
            account = self._find(session, internal_ref)
            if account is None:
                return False
            session.delete(account)
            logger.info("Cuenta eliminada: %s", internal_ref)
        self._forget_browser_session(internal_ref)
        return True

    # ------------------------------------------------------------------
    # Conexion
    # ------------------------------------------------------------------
    def connect(
        self, internal_ref: str, method: AuthMethod | None = None, **context: Any
    ) -> AuthOutcome:
        """Conecta una cuenta con el mecanismo autorizado.

        El mecanismo decide cómo: abrir el navegador, pedir una credencial
        delegada... LOT Bot nunca maneja la contraseña del usuario.
        """
        auth = method or self._auth
        if self.get_account(internal_ref) is None:
            raise ValueError(f"Cuenta '{internal_ref}' no encontrada.")

        logger.info(
            "Conectando '%s' mediante '%s'.", internal_ref, auth.describe()
        )
        outcome = auth.authenticate(internal_ref, **context)

        if not outcome.success or outcome.credential is None:
            detail = outcome.message
            if outcome.missing:
                detail += " Faltan: " + ", ".join(r.label for r in outcome.missing)
            self._mark_error(internal_ref, detail)
            return outcome

        self.store_credential(internal_ref, outcome.credential, auth.kind)
        self._notify_connected(internal_ref)
        return outcome

    def reauthenticate(self, internal_ref: str, **context: Any) -> AuthOutcome:
        """Vuelve a autenticar una cuenta cuya sesión ha dejado de ser válida."""
        info = self.get_account(internal_ref)
        method = self._auth
        if info is not None and info.is_demo:
            method = DemoAuthMethod()
        return self.connect(internal_ref, method=method, **context)

    def store_credential(
        self, internal_ref: str, credential: AuthCredential, kind: AuthKind | None = None
    ) -> AccountInfo:
        """Guarda la credencial cifrada y marca la cuenta como conectada."""
        for secret in credential.secret_values():
            register_secret(secret)

        payload = json.dumps(credential.to_storage(), ensure_ascii=False)
        with self._db.session_scope() as session:
            account = self._find(session, internal_ref)
            if account is None:
                raise ValueError(f"Cuenta '{internal_ref}' no encontrada.")
            account.credential_enc = self._box.encrypt(payload)
            account.auth_method = (kind or credential.kind).value
            account.token_expires_at = _naive_utc(credential.expires_at)
            account.scopes = " ".join(credential.metadata.get("scopes", []) or [])
            account.status = AccountStatus.CONNECTED
            account.status_detail = None
            account.is_demo = credential.kind is AuthKind.DEMO
            if credential.metadata.get("user_id"):
                account.wallapop_user_id = str(credential.metadata["user_id"])
            if credential.metadata.get("login"):
                account.wallapop_login = str(credential.metadata["login"])
            # Los campos antiguos dejan de usarse.
            account.access_token_enc = None
            account.refresh_token_enc = None
            session.flush()
            logger.info("Cuenta '%s' conectada correctamente.", internal_ref)
            return self._to_info(account)

    def disconnect(self, internal_ref: str, revoke: bool = True) -> bool:
        """Borra la credencial local y, si se puede, la revoca en Wallapop."""
        credential: AuthCredential | None = None
        with self._db.session_scope() as session:
            account = self._find(session, internal_ref)
            if account is None:
                return False
            if revoke and account.credential_enc:
                try:
                    credential = self._decrypt(account.credential_enc)
                except SecretsError:
                    credential = None
            account.credential_enc = None
            account.access_token_enc = None
            account.refresh_token_enc = None
            account.token_expires_at = None
            account.scopes = None
            account.status = AccountStatus.DISCONNECTED
            account.status_detail = "Desconectada por el usuario"
            session.flush()

        self._forget_browser_session(internal_ref)
        if credential is not None and credential.kind not in (
            AuthKind.DEMO,
            AuthKind.BROWSER_SESSION,
        ):
            try:
                self._auth.revoke(credential)
            except Exception as exc:  # revocar es «mejor esfuerzo»
                logger.warning("No se ha podido revocar la credencial: %s", type(exc).__name__)
        logger.info("Cuenta '%s' desconectada.", internal_ref)
        return True

    # ------------------------------------------------------------------
    # Credenciales
    # ------------------------------------------------------------------
    def _decrypt(self, ciphertext: str) -> AuthCredential:
        raw = self._box.decrypt(ciphertext)
        return AuthCredential.from_storage(json.loads(raw or "{}"))

    def get_credential(self, internal_ref: str) -> AuthCredential:
        """Devuelve la credencial vigente, renovándola si está a punto de caducar.

        Es el `CredentialProvider` que consume `AuthorizedWallapopService`.
        """
        with self._db.session_scope() as session:
            account = self._find(session, internal_ref)
            if account is None:
                raise AuthenticationError(f"Cuenta '{internal_ref}' no encontrada.")
            if not account.credential_enc:
                account.status = AccountStatus.DISCONNECTED
                raise AuthenticationError(
                    f"La cuenta '{internal_ref}' no está conectada.",
                    user_message=(
                        "Esta cuenta no está conectada. Conéctala desde Cuentas Wallapop."
                    ),
                )
            ciphertext = account.credential_enc

        credential = self._decrypt(ciphertext)
        for secret in credential.secret_values():
            register_secret(secret)

        if not credential.is_expired(margin_seconds=int(RENEWAL_MARGIN.total_seconds())):
            return credential

        # --- Caducada: intentar renovarla sin molestar al usuario ---
        logger.info("La credencial de '%s' ha caducado; intentando renovar.", internal_ref)
        renewed = None
        try:
            renewed = self._auth.renew(credential)
        except Exception as exc:
            logger.warning("Fallo al renovar '%s': %s", internal_ref, type(exc).__name__)

        if renewed is None:
            self._mark_expired(internal_ref)
            raise AuthenticationError(
                f"La credencial de '{internal_ref}' ha caducado y no se puede renovar.",
                user_message=(
                    "La sesión de esta cuenta ha caducado. Pulsa «Volver a autenticar» "
                    "en Cuentas Wallapop."
                ),
            )
        self.store_credential(internal_ref, renewed)
        return renewed

    def credential_provider(self):
        """Callable listo para inyectar en `AuthorizedWallapopService`."""
        return self.get_credential

    def get_access_token(self, internal_ref: str) -> str:
        """Compatibilidad: devuelve el valor de la cabecera de autorización.

        Se mantiene porque algunas integraciones antiguas lo usaban. El código
        nuevo debe usar `get_credential`.
        """
        credential = self.get_credential(internal_ref)
        header = credential.headers.get("Authorization", "")
        return header.split(" ", 1)[-1] if " " in header else header

    def token_provider(self):
        """Compatibilidad con la versión anterior."""
        return self.get_credential

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------
    def mark_synced(self, internal_ref: str) -> None:
        with self._db.session_scope() as session:
            account = self._find(session, internal_ref)
            if account is not None:
                account.last_sync_at = datetime.now(UTC).replace(tzinfo=None)
                account.status = AccountStatus.CONNECTED
                account.status_detail = None

    def mark_session_checked(self, internal_ref: str, ok: bool, detail: str = "") -> None:
        """Resultado de una comprobación REAL de la sesión de la cuenta.

        Una comprobación correcta solo mantiene conectada una cuenta que ya
        se había conectado (con confirmación del usuario); nunca conecta una
        cuenta nueva por sí sola.
        """
        if ok:
            with self._db.session_scope() as session:
                account = self._find(session, internal_ref)
                if account is not None and account.credential_enc:
                    account.status = AccountStatus.CONNECTED
                    account.status_detail = None
                    account.last_sync_at = datetime.now(UTC).replace(tzinfo=None)
                    reconnected = True
                else:
                    reconnected = False
            if reconnected:
                self._notify_connected(internal_ref)
        else:
            self._mark_expired(internal_ref, detail or "La sesión no es válida. Pulsa «Reconectar».")

    def mark_session_invalid(self, internal_ref: str, detail: str = "") -> None:
        """Marca que Wallapop ha rechazado la sesión de esta cuenta."""
        self._mark_expired(internal_ref, detail)

    def _mark_error(self, internal_ref: str, detail: str) -> None:
        with self._db.session_scope() as session:
            account = self._find(session, internal_ref)
            if account is not None:
                account.status = AccountStatus.ERROR
                account.status_detail = detail[:500]

    def restore_expired_accounts(self) -> int:
        """Las cuentas marcadas como «caducadas» por versiones anteriores
        vuelven a estar conectadas (si tienen su sesión guardada)."""
        restored = 0
        with self._db.session_scope() as session:
            for account in session.scalars(
                select(Account).where(Account.status == AccountStatus.EXPIRED)
            ).all():
                if account.credential_enc:
                    account.status = AccountStatus.CONNECTED
                    account.status_detail = None
                    restored += 1
        return restored

    def _mark_expired(self, internal_ref: str, detail: str = "") -> None:
        """Las cuentas NO caducan nunca en LOT Bot.

        Una cuenta conectada sigue conectada hasta que el usuario la elimina o
        la desconecta. Si Wallapop pide volver a entrar, se avisa (y la
        publicación espera a que el usuario inicie sesión en la misma
        ventana), pero la cuenta no se marca como caducada.
        """
        logger.info("Aviso de sesión para %s (la cuenta sigue conectada): %s", internal_ref, detail)
        with self._db.session_scope() as session:
            account = self._find(session, internal_ref)
            if account is not None and account.status != AccountStatus.CONNECTED:
                account.status_detail = (detail or "")[:500] or account.status_detail

    def ensure_demo_accounts(self, demo_refs: list[tuple[str, str]]) -> list[AccountInfo]:
        """Crea las cuentas de demostracion si no existen."""
        created: list[AccountInfo] = []
        for ref, alias in demo_refs:
            existing = self.get_account(ref)
            if existing is None:
                created.append(self.add_account(alias, is_demo=True, internal_ref=ref))
            else:
                created.append(existing)
        return created

    # ------------------------------------------------------------------
    # Compatibilidad con la version anterior (OAuth)
    # ------------------------------------------------------------------
    def store_tokens(self, internal_ref: str, tokens: OAuthTokens) -> AccountInfo:
        """Guarda tokens OAuth como credencial genérica."""
        return self.store_credential(
            internal_ref,
            AuthCredential(
                kind=AuthKind.OAUTH,
                headers={"Authorization": f"{tokens.token_type} {tokens.access_token}".strip()},
                expires_at=tokens.expires_at,
                renewal_material=tokens.refresh_token,
                metadata={"scopes": tokens.scopes},
            ),
        )

    def connect_interactive(self, internal_ref: str, timeout: float = 300.0) -> AccountInfo:
        """Nombre anterior de `connect`."""
        outcome = self.connect(internal_ref, timeout=timeout)
        if not outcome.success:
            raise AuthenticationError(outcome.message, user_message=outcome.message)
        info = self.get_account(internal_ref)
        assert info is not None
        return info


def _naive_utc(value: datetime | None) -> datetime | None:
    """SQLite guarda datetimes sin zona: normalizamos a UTC naive."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
