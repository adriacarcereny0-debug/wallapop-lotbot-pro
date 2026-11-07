"""Mecanismo «inicio de sesion autorizado» (session handoff).

COMO FUNCIONA
-------------
  1. LOT Bot abre el navegador del sistema en el punto de entrada que Wallapop
     haya autorizado (`auth.session_handoff.login_url`).
  2. El usuario se autentica EN WALLAPOP, en el dominio de Wallapop, con todos
     los pasos de seguridad que Wallapop exija (incluido MFA o CAPTCHA).
  3. El flujo autorizado devuelve a LOT Bot, en la direccion local declarada,
     UNICAMENTE los campos de sesion que ese flujo entregue.
  4. LOT Bot compone la credencial segun las plantillas declaradas y la guarda
     cifrada, asociada a esa cuenta.

LO QUE ESTE MODULO NO HACE, Y NO VA A HACER
-------------------------------------------
  * No lee el perfil del navegador del usuario ni extrae sus cookies.
  * No pide, no maneja y no almacena la contrasena de Wallapop.
  * No automatiza el formulario de inicio de sesion.
  * No evita ni intenta resolver MFA, CAPTCHA ni ningun otro control.
  * No deduce ni adivina la URL de inicio de sesion: si no esta declarada, el
    metodo dice que falta ese dato y no hace nada.

Todo lo anterior seria eludir controles de acceso. LOT Bot se limita a
escuchar el retorno del flujo que Wallapop haya autorizado expresamente.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from lot_bot.wallapop.auth.base import (
    AuthCredential,
    AuthKind,
    AuthMethod,
    AuthOutcome,
    AuthRequirement,
    RequirementSource,
)
from lot_bot.wallapop.auth.config import SessionHandoffConfig
from lot_bot.wallapop.oauth import LocalCallbackServer, new_state, open_browser

logger = logging.getLogger(__name__)


class SessionHandoffAuthMethod(AuthMethod):
    """El usuario inicia sesión en Wallapop y autoriza a LOT Bot."""

    kind = AuthKind.SESSION_HANDOFF
    display_name = "Inicio de sesión autorizado"
    description = (
        "Se abre el navegador en Wallapop. Inicias sesión allí, con tus pasos de "
        "seguridad habituales, y autorizas a LOT Bot. La aplicación nunca ve ni "
        "guarda tu contraseña."
    )

    def __init__(self, config: SessionHandoffConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    def requirements(self) -> list[AuthRequirement]:
        config = self.config
        return [
            AuthRequirement(
                key="login_url",
                label="Punto de entrada del inicio de sesión autorizado",
                description=(
                    "Dirección exacta que Wallapop autoriza para iniciar el flujo. "
                    "LOT Bot no la deduce ni la inventa."
                ),
                source=RequirementSource.WALLAPOP,
                satisfied=bool(config.login_url),
                where="perfil de acceso → auth.session_handoff.login_url",
            ),
            AuthRequirement(
                key="return_uri",
                label="Dirección de retorno",
                description=(
                    "Dirección local a la que el flujo autorizado devuelve la sesión. "
                    "Wallapop debe tenerla registrada."
                ),
                source=RequirementSource.WALLAPOP,
                satisfied=bool(config.return_uri),
                where="perfil de acceso → auth.session_handoff.return_uri",
            ),
            AuthRequirement(
                key="session_fields",
                label="Campos de sesión que devuelve el flujo",
                description=(
                    "Nombre de los datos que Wallapop entrega al volver "
                    "(por ejemplo, el identificador de sesión)."
                ),
                source=RequirementSource.WALLAPOP,
                satisfied=bool(config.session_fields),
                where="perfil de acceso → auth.session_handoff.session_fields",
            ),
            AuthRequirement(
                key="application",
                label="Cómo se aplica la sesión a cada petición",
                description=(
                    "Cabecera o cookie en la que viaja la sesión "
                    "(por ejemplo, «Authorization: Bearer {session_token}»)."
                ),
                source=RequirementSource.WALLAPOP,
                satisfied=bool(config.header_template or config.cookie_template),
                where="perfil de acceso → auth.session_handoff.header_template / cookie_template",
            ),
        ]

    # ------------------------------------------------------------------
    def authenticate(self, account_ref: str, timeout: float = 300.0, **context: Any) -> AuthOutcome:
        missing = self.missing_requirements()
        if missing:
            return AuthOutcome(
                success=False,
                message=(
                    "No se puede iniciar el inicio de sesión autorizado: faltan datos "
                    "técnicos que debe facilitar Wallapop."
                ),
                missing=missing,
            )

        state = new_state()
        separator = "&" if "?" in self.config.login_url else "?"
        url = f"{self.config.login_url}{separator}state={state}"

        logger.info("Abriendo el flujo de inicio de sesión autorizado para '%s'.", account_ref)
        with LocalCallbackServer(self.config.return_uri) as server:
            if not open_browser(url):
                return AuthOutcome(
                    success=False,
                    message=(
                        "No se ha podido abrir el navegador. Ábrelo manualmente y "
                        "completa el inicio de sesión."
                    ),
                )
            result = server.wait(timeout=timeout)

        if result.error:
            return AuthOutcome(
                success=False, message=f"El inicio de sesión no se ha completado: {result.error}."
            )
        if not result.params:
            return AuthOutcome(
                success=False,
                message="No se ha recibido ninguna sesión desde Wallapop. Inténtalo de nuevo.",
            )
        # El `state` protege contra respuestas que no procedan de este intento.
        returned_state = result.get("state")
        if returned_state is not None and returned_state != state:
            return AuthOutcome(
                success=False,
                message="La respuesta recibida no corresponde a este intento de conexión.",
            )

        credential = self._build_credential(result.params)
        if credential is None or credential.is_empty:
            expected = ", ".join(self.config.session_fields) or "(sin declarar)"
            return AuthOutcome(
                success=False,
                message=(
                    f"El flujo ha respondido, pero no trae los campos de sesión esperados "
                    f"({expected}). Revisa «session_fields» del perfil de acceso."
                ),
            )
        return AuthOutcome(
            success=True,
            credential=credential,
            message="Sesión autorizada y guardada de forma cifrada.",
        )

    # ------------------------------------------------------------------
    def _build_credential(self, params: dict[str, str]) -> AuthCredential | None:
        """Compone la credencial aplicando las plantillas declaradas.

        Solo se usan los campos que el flujo autorizado ha devuelto. Si una
        plantilla referencia un campo que no ha llegado, esa entrada se
        descarta en vez de rellenarse con un valor inventado.
        """
        headers = _render_template(self.config.header_template, params)
        cookies = _render_template(self.config.cookie_template, params)
        if not headers and not cookies:
            return None

        expires_at = None
        if self.config.expires_after_minutes:
            expires_at = datetime.now(UTC) + timedelta(
                minutes=int(self.config.expires_after_minutes)
            )
        elif params.get("expires_in", "").isdigit():
            expires_at = datetime.now(UTC) + timedelta(
                seconds=int(params["expires_in"])
            )

        # En `metadata` solo van datos NO sensibles, porque se muestran en la
        # pantalla de cuentas.
        metadata: dict[str, Any] = {}
        for key in ("user_id", "login", "display_name", "scope"):
            if params.get(key):
                metadata[key] = params[key]

        return AuthCredential(
            kind=AuthKind.SESSION_HANDOFF,
            headers=headers,
            cookies=cookies,
            expires_at=expires_at,
            renewal_material=params.get("refresh_token") or None,
            metadata=metadata,
        )

    def renew(self, credential: AuthCredential) -> AuthCredential | None:
        """Una sesión no se renueva sola: el usuario vuelve a autenticarse.

        Si Wallapop documenta un mecanismo de renovación, se implementa aquí.
        """
        return None

    def revoke(self, credential: AuthCredential) -> bool:
        """Borrar la sesión en local siempre funciona; revocarla en Wallapop
        requiere que el mecanismo autorizado ofrezca esa operación."""
        return False


def _render_template(template: dict[str, str], params: dict[str, str]) -> dict[str, str]:
    """Sustituye `{campo}` con lo recibido. Descarta lo que no haya llegado."""
    rendered: dict[str, str] = {}
    for name, pattern in template.items():
        try:
            value = pattern.format(**params)
        except (KeyError, IndexError):
            logger.debug("La plantilla '%s' referencia un campo que no ha llegado.", name)
            continue
        if value and "{" not in value:
            rendered[name] = value
    return rendered
