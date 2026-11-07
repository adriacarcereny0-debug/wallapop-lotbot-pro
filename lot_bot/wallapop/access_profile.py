"""Perfil de acceso autorizado a Wallapop.

Un «perfil de acceso» describe, en un unico fichero YAML, las DOS cosas que
LOT Bot necesita saber y que solo Wallapop puede decidir:

    auth:        como se autentica la cuenta (el mecanismo autorizado)
    api + ops:   que operaciones existen y como se llaman (el transporte)

Ninguna de las dos esta escrita en el codigo. El fichero de ejemplo del
repositorio esta vacio a proposito y hay una prueba que lo comprueba.

SUSTITUYE (sin romper) al antiguo `endpoint_map.yaml`, que solo cubria el
transporte y daba por hecho que la autenticacion era OAuth.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lot_bot.wallapop.auth.base import AuthKind
from lot_bot.wallapop.auth.config import (
    AuthConfig,
    DelegatedCredentialConfig,
    SessionHandoffConfig,
)
from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.endpoint_map import EndpointMap, OAuthConfig
from lot_bot.wallapop.errors import ConfigurationError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AccessProfile:
    """Perfil completo: autenticacion + transporte + operaciones."""

    auth: AuthConfig = field(default_factory=AuthConfig)
    transport: EndpointMap = field(default_factory=EndpointMap)
    authorized_by: str = ""
    authorization_ref: str = ""
    notes: str = ""
    source_path: Path | None = None

    # -- Consulta ---------------------------------------------------------
    @property
    def has_auth(self) -> bool:
        """True si el perfil declara COMO autenticarse."""
        return self.auth.is_declared

    @property
    def has_transport(self) -> bool:
        """True si el perfil declara QUE se puede llamar."""
        return self.transport.is_usable

    @property
    def is_complete(self) -> bool:
        return self.has_auth and self.has_transport

    def capabilities(self) -> set[Capability]:
        return self.transport.capabilities()

    def missing_pieces(self) -> list[str]:
        """Que falta para poder conectar de verdad. Texto para el usuario."""
        missing: list[str] = []
        if not self.has_auth:
            missing.append(
                "El mecanismo de autenticación autorizado (bloque «auth:» del perfil): "
                "sin él, LOT Bot no sabe cómo debe identificarse tu cuenta."
            )
        if not self.transport.base_url:
            missing.append(
                "La dirección base del servicio autorizado («api.base_url»): "
                "sin ella, LOT Bot no sabe a dónde dirigir las peticiones."
            )
        if not self.transport.operations:
            missing.append(
                "Al menos una operación autorizada (bloque «operations:»): "
                "sin ellas, LOT Bot no conoce ninguna acción y no inventa ninguna."
            )
        return missing

    def describe(self) -> str:
        lines = []
        if self.authorized_by:
            lines.append(f"Autorización: {self.authorized_by}")
        if self.authorization_ref:
            lines.append(f"Referencia: {self.authorization_ref}")
        lines.append(
            f"Autenticación: {self.auth.method.value if self.auth.method else '(sin declarar)'}"
        )
        lines.append(f"Transporte: {self.transport.base_url or '(sin declarar)'}")
        lines.append(f"Operaciones: {len(self.transport.operations)}")
        return "\n".join(lines)

    # -- Carga ------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> AccessProfile:
        file_path = Path(path).expanduser()
        if not file_path.is_file():
            raise ConfigurationError(
                f"No se encuentra el perfil de acceso en '{file_path}'.",
                user_message=(
                    "No se encuentra el fichero de acceso autorizado de Wallapop. "
                    "Revisa la variable WALLAPOP_ACCESS_PROFILE."
                ),
            )
        try:
            data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(
                f"El perfil '{file_path}' no es un YAML válido: {exc}",
                user_message="El fichero de acceso de Wallapop tiene un formato incorrecto.",
            ) from exc
        profile = cls.from_dict(data)
        profile.source_path = file_path
        return profile

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccessProfile:
        meta = data.get("meta") or {}
        auth_raw = data.get("auth") or {}

        method: AuthKind | None = None
        raw_method = str(auth_raw.get("method") or "").strip().lower()
        if raw_method:
            try:
                method = AuthKind(raw_method)
            except ValueError as exc:
                valid = ", ".join(k.value for k in AuthKind if k is not AuthKind.DEMO)
                raise ConfigurationError(
                    f"Método de autenticación desconocido: '{raw_method}'.",
                    user_message=(
                        f"El perfil declara un método de autenticación que LOT Bot no "
                        f"conoce ('{raw_method}'). Valores admitidos: {valid}."
                    ),
                ) from exc

        oauth_raw = auth_raw.get("oauth") or {}
        session_raw = auth_raw.get("session_handoff") or {}
        delegated_raw = auth_raw.get("delegated_credential") or {}

        auth = AuthConfig(
            method=method,
            oauth=OAuthConfig(
                authorize_url=str(oauth_raw.get("authorize_url") or "").strip(),
                token_url=str(oauth_raw.get("token_url") or "").strip(),
                revoke_url=str(oauth_raw.get("revoke_url") or "").strip(),
                use_pkce=bool(oauth_raw.get("use_pkce", True)),
                scopes=list(oauth_raw.get("scopes") or []),
                audience=str(oauth_raw.get("audience") or ""),
                extra_authorize_params=dict(oauth_raw.get("extra_authorize_params") or {}),
            ),
            session=SessionHandoffConfig(
                login_url=str(session_raw.get("login_url") or "").strip(),
                return_uri=str(
                    session_raw.get("return_uri") or "http://127.0.0.1:8723/callback"
                ).strip(),
                session_fields=list(session_raw.get("session_fields") or []),
                header_template=dict(session_raw.get("header_template") or {}),
                cookie_template=dict(session_raw.get("cookie_template") or {}),
                expires_after_minutes=session_raw.get("expires_after_minutes"),
                validation_operation=str(
                    session_raw.get("validation_operation") or "account_profile"
                ),
                instructions=str(session_raw.get("instructions") or ""),
            ),
            delegated=DelegatedCredentialConfig(
                header_name=str(delegated_raw.get("header_name") or "Authorization"),
                header_format=str(delegated_raw.get("header_format") or "Bearer {credential}"),
                instructions=str(delegated_raw.get("instructions") or ""),
                expires_after_minutes=delegated_raw.get("expires_after_minutes"),
                validation_operation=str(
                    delegated_raw.get("validation_operation") or "account_profile"
                ),
                declared=bool(delegated_raw),
            ),
            client_id_env=str(auth_raw.get("client_id_env") or "WALLAPOP_CLIENT_ID"),
            client_secret_env=str(auth_raw.get("client_secret_env") or "WALLAPOP_CLIENT_SECRET"),
        )

        transport = EndpointMap.from_dict(data)

        return cls(
            auth=auth,
            transport=transport,
            authorized_by=str(meta.get("authorized_by") or ""),
            authorization_ref=str(meta.get("authorization_ref") or ""),
            notes=str(meta.get("notes") or ""),
        )


def empty_profile() -> AccessProfile:
    """Perfil sin nada declarado: no se puede conectar, y se dice por qué."""
    return AccessProfile()
