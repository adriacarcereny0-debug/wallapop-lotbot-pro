"""Construccion del mecanismo de autenticacion que corresponda al perfil."""

from __future__ import annotations

import logging

from lot_bot.wallapop.auth.base import AuthKind, AuthMethod
from lot_bot.wallapop.auth.config import AuthConfig
from lot_bot.wallapop.auth.demo import DemoAuthMethod
from lot_bot.wallapop.auth.oauth_method import OAuthAuthMethod
from lot_bot.wallapop.auth.session_method import SessionHandoffAuthMethod
from lot_bot.wallapop.auth.token_method import DelegatedCredentialAuthMethod

logger = logging.getLogger(__name__)


def build_auth_method(config: AuthConfig, redirect_uri: str = "") -> AuthMethod:
    """Devuelve el mecanismo declarado en el perfil.

    Si el perfil no declara ninguno, se devuelve el de DEMO: LOT Bot no supone
    un mecanismo por su cuenta.
    """
    method = config.method
    if method is AuthKind.OAUTH:
        return OAuthAuthMethod(config, redirect_uri=redirect_uri)
    if method is AuthKind.SESSION_HANDOFF:
        return SessionHandoffAuthMethod(config.session)
    if method is AuthKind.DELEGATED_CREDENTIAL:
        return DelegatedCredentialAuthMethod(config.delegated)
    if method is not None:
        logger.warning("Método de autenticación no implementado: %s", method)
    return DemoAuthMethod()


def available_methods(config: AuthConfig, redirect_uri: str = "") -> list[AuthMethod]:
    """Todos los mecanismos que LOT Bot sabe manejar, con su estado actual.

    La pantalla de cuentas los muestra junto a los datos que le faltan a cada
    uno, para que se vea exactamente por qué no se puede conectar todavía.
    """
    return [
        OAuthAuthMethod(config, redirect_uri=redirect_uri),
        SessionHandoffAuthMethod(config.session),
        DelegatedCredentialAuthMethod(config.delegated),
    ]
