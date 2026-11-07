"""Mecanismo «credencial delegada por cuenta».

QUE ES Y QUE NO ES
------------------
NO es una API key de aplicacion ni un `client_secret` global. Es un valor que
Wallapop emite para UNA cuenta concreta y que el titular de esa cuenta
introduce una sola vez en LOT Bot.

Se incluye porque es el mecanismo que algunas integraciones autorizadas usan
cuando no hay un flujo OAuth. Si Wallapop no emite nada parecido, este metodo
simplemente no se ofrece.

LOT Bot no genera, no deduce y no adivina este valor: lo introduce el usuario
con lo que Wallapop le haya entregado.
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
from lot_bot.wallapop.auth.config import DelegatedCredentialConfig

logger = logging.getLogger(__name__)


class DelegatedCredentialAuthMethod(AuthMethod):
    """El usuario introduce la credencial que Wallapop ha emitido para su cuenta."""

    kind = AuthKind.DELEGATED_CREDENTIAL
    display_name = "Credencial delegada por cuenta"
    description = (
        "Wallapop emite una credencial para esta cuenta concreta y la introduces "
        "una sola vez. LOT Bot la guarda cifrada y no vuelve a mostrarla."
    )
    interactive = False

    def __init__(self, config: DelegatedCredentialConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    def requirements(self) -> list[AuthRequirement]:
        return [
            AuthRequirement(
                key="header_format",
                label="Formato en el que viaja la credencial",
                description=(
                    "Cómo debe enviarse en cada petición "
                    "(por ejemplo, «Authorization: Bearer {credential}»)."
                ),
                source=RequirementSource.WALLAPOP,
                satisfied=self.config.is_configured,
                where="perfil de acceso → auth.delegated_credential.header_format",
            ),
            AuthRequirement(
                key="credential",
                label="Credencial de la cuenta",
                description=(
                    "El valor que Wallapop ha emitido para esta cuenta. "
                    "Se introduce al conectar y no se vuelve a pedir."
                ),
                source=RequirementSource.USUARIO,
                satisfied=True,  # se aporta en el momento de conectar
                where="pantalla Cuentas Wallapop → Conectar",
            ),
        ]

    # ------------------------------------------------------------------
    def authenticate(self, account_ref: str, credential: str = "", **context: Any) -> AuthOutcome:
        missing = [r for r in self.missing_requirements() if r.key != "credential"]
        if missing:
            return AuthOutcome(
                success=False,
                message="Falta declarar cómo debe enviarse la credencial en cada petición.",
                missing=missing,
            )

        value = (credential or "").strip()
        if not value:
            return AuthOutcome(
                success=False,
                message="No has introducido la credencial que Wallapop ha emitido para esta cuenta.",
            )

        try:
            header_value = self.config.header_format.format(credential=value)
        except (KeyError, IndexError):
            return AuthOutcome(
                success=False,
                message=(
                    "El formato declarado para la credencial no es válido: debe contener "
                    "«{credential}»."
                ),
            )

        expires_at = None
        if self.config.expires_after_minutes:
            expires_at = datetime.now(UTC) + timedelta(
                minutes=int(self.config.expires_after_minutes)
            )

        logger.info("Credencial delegada registrada para la cuenta '%s'.", account_ref)
        return AuthOutcome(
            success=True,
            credential=AuthCredential(
                kind=AuthKind.DELEGATED_CREDENTIAL,
                headers={self.config.header_name: header_value},
                expires_at=expires_at,
            ),
            message="Credencial guardada de forma cifrada.",
        )
