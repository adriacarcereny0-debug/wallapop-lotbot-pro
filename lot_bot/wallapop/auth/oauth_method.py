"""Mecanismo OAuth 2.0 (codigo de autorizacion + PKCE).

Solo aplicable SI Wallapop proporciona un flujo OAuth y las credenciales de
cliente correspondientes. Si no las hay, este metodo declara exactamente que
le falta en vez de fallar de forma opaca.
"""

from __future__ import annotations

import logging
from typing import Any

from lot_bot.wallapop.auth.base import (
    AuthCredential,
    AuthKind,
    AuthMethod,
    AuthOutcome,
    AuthRequirement,
    RequirementSource,
)
from lot_bot.wallapop.auth.config import AuthConfig
from lot_bot.wallapop.errors import AuthenticationError, WallapopError
from lot_bot.wallapop.oauth import (
    LocalCallbackServer,
    OAuthClient,
    generate_pkce_pair,
    new_state,
    open_browser,
)

logger = logging.getLogger(__name__)


class OAuthAuthMethod(AuthMethod):
    """Autorización OAuth iniciada desde LOT Bot."""

    kind = AuthKind.OAUTH
    display_name = "Autorización OAuth de Wallapop"
    description = (
        "Se abre el navegador en Wallapop, inicias sesión allí y autorizas a "
        "LOT Bot. LOT Bot nunca ve tu contraseña."
    )

    def __init__(self, config: AuthConfig, redirect_uri: str = "") -> None:
        self.config = config
        self.redirect_uri = redirect_uri or "http://127.0.0.1:8723/callback"

    # ------------------------------------------------------------------
    def requirements(self) -> list[AuthRequirement]:
        oauth = self.config.oauth
        return [
            AuthRequirement(
                key="authorize_url",
                label="URL de autorización",
                description="Dirección donde Wallapop muestra la pantalla de autorización.",
                source=RequirementSource.WALLAPOP,
                satisfied=bool(oauth.authorize_url),
                where="perfil de acceso → auth.oauth.authorize_url",
            ),
            AuthRequirement(
                key="token_url",
                label="URL de token",
                description="Dirección donde se canjea el código por un token de acceso.",
                source=RequirementSource.WALLAPOP,
                satisfied=bool(oauth.token_url),
                where="perfil de acceso → auth.oauth.token_url",
            ),
            AuthRequirement(
                key="client_id",
                label="Identificador de cliente",
                description="Identificador que Wallapop asigna a la integración.",
                source=RequirementSource.WALLAPOP,
                satisfied=bool(self.config.client_id()),
                where=f".env → {self.config.client_id_env}",
            ),
            AuthRequirement(
                key="redirect_uri",
                label="URI de redirección registrada",
                description="Debe coincidir exactamente con la registrada en Wallapop.",
                source=RequirementSource.WALLAPOP,
                satisfied=bool(self.redirect_uri),
                where=".env → WALLAPOP_REDIRECT_URI",
            ),
        ]

    # ------------------------------------------------------------------
    def _client(self) -> OAuthClient:
        return OAuthClient(
            config=self.config.oauth,
            client_id=self.config.client_id(),
            client_secret=self.config.client_secret(),
            redirect_uri=self.redirect_uri,
            scopes=self.config.oauth.scopes,
        )

    def authenticate(self, account_ref: str, timeout: float = 300.0, **context: Any) -> AuthOutcome:
        missing = self.missing_requirements()
        if missing:
            return AuthOutcome(
                success=False,
                message="Faltan datos para iniciar la autorización OAuth.",
                missing=missing,
            )

        client = self._client()
        state = new_state()
        verifier, challenge = generate_pkce_pair()
        url = client.build_authorization_url(state, challenge)

        with LocalCallbackServer(self.redirect_uri) as server:
            open_browser(url)
            result = server.wait(timeout=timeout)

        if result.error or not result.code:
            return AuthOutcome(
                success=False,
                message=f"No se ha completado la autorización: {result.error or 'sin código'}.",
            )
        if result.state != state:
            return AuthOutcome(
                success=False,
                message="La respuesta de autorización no es válida (el parámetro «state» no coincide).",
            )

        try:
            tokens = client.exchange_code(result.code, verifier)
        except WallapopError as exc:
            return AuthOutcome(success=False, message=exc.user_message)

        return AuthOutcome(
            success=True,
            credential=AuthCredential(
                kind=AuthKind.OAUTH,
                headers={"Authorization": f"{tokens.token_type} {tokens.access_token}".strip()},
                expires_at=tokens.expires_at,
                renewal_material=tokens.refresh_token,
                metadata={"scopes": tokens.scopes},
            ),
            message="Cuenta autorizada correctamente.",
        )

    # ------------------------------------------------------------------
    def renew(self, credential: AuthCredential) -> AuthCredential | None:
        if not credential.renewal_material:
            return None
        try:
            tokens = self._client().refresh(credential.renewal_material)
        except AuthenticationError:
            logger.info("El token de refresco ha sido rechazado: hay que reautenticar.")
            return None
        return AuthCredential(
            kind=AuthKind.OAUTH,
            headers={"Authorization": f"{tokens.token_type} {tokens.access_token}".strip()},
            expires_at=tokens.expires_at,
            renewal_material=tokens.refresh_token or credential.renewal_material,
            metadata={"scopes": tokens.scopes},
        )

    def revoke(self, credential: AuthCredential) -> bool:
        header = credential.headers.get("Authorization", "")
        token = header.split(" ", 1)[-1] if " " in header else header
        if not token:
            return False
        return self._client().revoke(token)
