"""Mecanismo DEMO: credenciales simuladas que no sirven para nada real."""

from __future__ import annotations

from typing import Any

from lot_bot.wallapop.auth.base import (
    AuthCredential,
    AuthKind,
    AuthMethod,
    AuthOutcome,
    AuthRequirement,
)


class DemoAuthMethod(AuthMethod):
    """Conecta cuentas de demostracion. No contacta con Wallapop.

    La credencial que devuelve esta marcada como DEMO y la capa de transporte
    la rechaza si alguna vez intentara usarse contra Wallapop de verdad.
    """

    kind = AuthKind.DEMO
    display_name = "Modo demostración"
    description = (
        "Cuentas simuladas para probar la aplicación. No se conecta con Wallapop "
        "y nada de lo que hagas afecta a anuncios reales."
    )
    interactive = False

    def requirements(self) -> list[AuthRequirement]:
        return []

    def authenticate(self, account_ref: str, **context: Any) -> AuthOutcome:
        return AuthOutcome(
            success=True,
            credential=AuthCredential(
                kind=AuthKind.DEMO,
                headers={},
                metadata={"demo": True, "account_ref": account_ref},
            ),
            message="Cuenta de demostración conectada (datos simulados).",
        )
