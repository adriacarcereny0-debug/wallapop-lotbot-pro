"""Configuracion de cada mecanismo de autenticacion.

Vive dentro del paquete `auth` a proposito: describe la autenticacion, no el
transporte. Asi `auth/` no depende de `access_profile.py` y no hay
dependencias circulares. El perfil de acceso construye estos objetos y se los
entrega al mecanismo correspondiente.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from lot_bot.wallapop.auth.base import AuthKind
from lot_bot.wallapop.endpoint_map import OAuthConfig


@dataclass(slots=True)
class SessionHandoffConfig:
    """Parametros del mecanismo «inicio de sesion autorizado».

    El usuario se autentica en Wallapop, en el dominio de Wallapop, y el flujo
    autorizado devuelve a LOT Bot una sesion de uso. LOT Bot NO lee el
    navegador del usuario, NO extrae cookies de su perfil y NO maneja su
    contrasena: solo escucha el retorno del flujo que Wallapop autorice.
    """

    #: Punto de entrada del flujo autorizado (lo indica Wallapop).
    login_url: str = ""
    #: Direccion local a la que el flujo devuelve la sesion.
    return_uri: str = "http://127.0.0.1:8723/callback"
    #: Nombres de los campos que devuelve el flujo autorizado.
    session_fields: list[str] = field(default_factory=list)
    #: Como se aplica la sesion. Ej: {"Authorization": "Bearer {session_token}"}
    header_template: dict[str, str] = field(default_factory=dict)
    #: Cookies a reenviar, si el mecanismo usa cookies. Ej: {"sid": "{session_id}"}
    cookie_template: dict[str, str] = field(default_factory=dict)
    #: Minutos de validez, si Wallapop los documenta.
    expires_after_minutes: int | None = None
    #: Operacion barata para comprobar que la sesion sigue viva.
    validation_operation: str = "account_profile"
    #: Texto que se muestra al usuario antes de abrir el navegador.
    instructions: str = ""

    @property
    def is_configured(self) -> bool:
        """Hace falta saber a donde enviar al usuario Y como aplicar el retorno."""
        return bool(self.login_url) and bool(self.header_template or self.cookie_template)


@dataclass(slots=True)
class DelegatedCredentialConfig:
    """Parametros de una credencial delegada por cuenta.

    NO es un `client_secret` global ni una API key de aplicacion: es un valor
    que Wallapop emite para UNA cuenta concreta.
    """

    header_name: str = "Authorization"
    header_format: str = "Bearer {credential}"
    instructions: str = ""
    expires_after_minutes: int | None = None
    validation_operation: str = "account_profile"
    #: True solo si el perfil declara explicitamente este bloque. El formato
    #: por defecto de arriba es una plantilla nuestra, NO un dato de Wallapop:
    #: darlo por bueno seria suponer como se autentica su servicio.
    declared: bool = False

    @property
    def is_configured(self) -> bool:
        return (
            self.declared
            and bool(self.header_name)
            and "{credential}" in self.header_format
        )


@dataclass(slots=True)
class AuthConfig:
    """Bloque `auth:` del perfil de acceso."""

    method: AuthKind | None = None
    oauth: OAuthConfig = field(default_factory=OAuthConfig)
    session: SessionHandoffConfig = field(default_factory=SessionHandoffConfig)
    delegated: DelegatedCredentialConfig = field(default_factory=DelegatedCredentialConfig)
    #: Variables de entorno de donde leer los secretos (nunca van en el YAML).
    client_id_env: str = "WALLAPOP_CLIENT_ID"
    client_secret_env: str = "WALLAPOP_CLIENT_SECRET"

    @property
    def is_declared(self) -> bool:
        return self.method is not None

    def client_id(self) -> str:
        return os.environ.get(self.client_id_env, "").strip()

    def client_secret(self) -> str:
        return os.environ.get(self.client_secret_env, "").strip()
