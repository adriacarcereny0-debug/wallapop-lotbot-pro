"""Mecanismos de autenticacion autorizados por Wallapop.

LOT Bot no presupone que el acceso sea una API con client_id/client_secret.
Aqui viven todos los mecanismos que sabe manejar; el perfil de acceso decide
cual se usa.
"""

from lot_bot.wallapop.auth.base import (
    AUTH_KIND_LABELS,
    AuthCredential,
    AuthKind,
    AuthMethod,
    AuthOutcome,
    AuthRequirement,
    RequirementSource,
)
from lot_bot.wallapop.auth.demo import DemoAuthMethod
from lot_bot.wallapop.auth.factory import available_methods, build_auth_method
from lot_bot.wallapop.auth.oauth_method import OAuthAuthMethod
from lot_bot.wallapop.auth.session_method import SessionHandoffAuthMethod
from lot_bot.wallapop.auth.token_method import DelegatedCredentialAuthMethod

__all__ = [
    "AuthKind",
    "AUTH_KIND_LABELS",
    "AuthMethod",
    "AuthCredential",
    "AuthOutcome",
    "AuthRequirement",
    "RequirementSource",
    "DemoAuthMethod",
    "OAuthAuthMethod",
    "SessionHandoffAuthMethod",
    "DelegatedCredentialAuthMethod",
    "build_auth_method",
    "available_methods",
]
