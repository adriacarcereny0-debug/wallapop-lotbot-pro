"""Mapa declarativo de endpoints oficiales de Wallapop.

MOTIVO DE ESTE MODULO
---------------------
LOT Bot no incluye ninguna URL de Wallapop codificada en el programa. Los
endpoints, metodos, parametros y el mapeo de la respuesta se declaran en un
fichero YAML que se rellena EXCLUSIVAMENTE con la documentacion oficial
entregada junto a las credenciales de la integracion autorizada.

Consecuencias:
  * No se inventa ningun endpoint.
  * Una operacion que no figure en el fichero no existe para la aplicacion:
    se responde `NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS`.
  * Si Wallapop cambia su API, se actualiza el YAML sin tocar el codigo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.errors import ConfigurationError

logger = logging.getLogger(__name__)

_VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def dig(data: Any, dotted_path: str, default: Any = None) -> Any:
    """Extrae un valor anidado con notacion `a.b.0.c`."""
    if not dotted_path:
        return data
    current = data
    for part in dotted_path.split("."):
        if current is None:
            return default
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return default
        elif isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
        else:
            return default
    return current if current is not None else default


@dataclass(slots=True)
class ResponseMapping:
    """Como traducir la respuesta oficial a los DTO de LOT Bot."""

    collection_path: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    total_path: str = ""

    def extract_collection(self, payload: Any) -> list[Any]:
        data = dig(payload, self.collection_path, payload if not self.collection_path else [])
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []

    def apply(self, raw: Any) -> dict[str, Any]:
        """Traduce un elemento crudo al diccionario de campos de LOT Bot."""
        if not isinstance(raw, dict):
            return {}
        if not self.fields:
            return dict(raw)
        return {name: dig(raw, path) for name, path in self.fields.items()}


@dataclass(slots=True)
class Operation:
    """Una operacion HTTP autorizada."""

    name: str
    method: str
    path: str
    query: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    response: ResponseMapping = field(default_factory=ResponseMapping)
    notes: str = ""

    def render_path(self, params: dict[str, Any]) -> str:
        try:
            return self.path.format(**params)
        except KeyError as exc:
            raise ConfigurationError(
                f"La operacion '{self.name}' requiere el parametro {exc} en su ruta."
            ) from exc


@dataclass(slots=True)
class OAuthConfig:
    """Parametros del flujo OAuth, tomados de la documentacion oficial."""

    authorize_url: str = ""
    token_url: str = ""
    revoke_url: str = ""
    use_pkce: bool = True
    scopes: list[str] = field(default_factory=list)
    audience: str = ""
    extra_authorize_params: dict[str, str] = field(default_factory=dict)

    @property
    def is_configured(self) -> bool:
        return bool(self.authorize_url and self.token_url)


@dataclass(slots=True)
class EndpointMap:
    """Conjunto completo de endpoints autorizados."""

    base_url: str = ""
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    default_headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    oauth: OAuthConfig = field(default_factory=OAuthConfig)
    operations: dict[str, Operation] = field(default_factory=dict)
    source_path: Path | None = None

    # -- Consulta ---------------------------------------------------------
    def has(self, capability: Capability | str) -> bool:
        key = capability.value if isinstance(capability, Capability) else capability
        return key in self.operations

    def get(self, capability: Capability | str) -> Operation | None:
        key = capability.value if isinstance(capability, Capability) else capability
        return self.operations.get(key)

    def capabilities(self) -> set[Capability]:
        """Capacidades derivadas de las operaciones declaradas."""
        found: set[Capability] = set()
        for name in self.operations:
            try:
                found.add(Capability(name))
            except ValueError:
                logger.warning(
                    "El mapa de endpoints declara '%s', que no es una operacion conocida "
                    "de LOT Bot. Se ignora.",
                    name,
                )
        return found

    @property
    def is_usable(self) -> bool:
        return bool(self.base_url and self.operations)

    # -- Carga ------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> EndpointMap:
        file_path = Path(path).expanduser()
        if not file_path.is_file():
            raise ConfigurationError(
                f"No se encuentra el mapa de endpoints en '{file_path}'.",
                user_message=(
                    "No se encuentra el fichero de endpoints de Wallapop. "
                    "Revisa la variable WALLAPOP_ENDPOINT_MAP."
                ),
            )
        try:
            data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(
                f"El mapa de endpoints '{file_path}' no es un YAML valido: {exc}",
                user_message="El fichero de endpoints de Wallapop tiene un formato incorrecto.",
            ) from exc
        instance = cls.from_dict(data)
        instance.source_path = file_path
        return instance

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EndpointMap:
        api = data.get("api") or {}
        oauth_raw = data.get("oauth") or {}
        operations_raw = data.get("operations") or {}

        oauth = OAuthConfig(
            authorize_url=str(oauth_raw.get("authorize_url") or "").strip(),
            token_url=str(oauth_raw.get("token_url") or "").strip(),
            revoke_url=str(oauth_raw.get("revoke_url") or "").strip(),
            use_pkce=bool(oauth_raw.get("use_pkce", True)),
            scopes=list(oauth_raw.get("scopes") or []),
            audience=str(oauth_raw.get("audience") or ""),
            extra_authorize_params=dict(oauth_raw.get("extra_authorize_params") or {}),
        )

        operations: dict[str, Operation] = {}
        for name, spec in operations_raw.items():
            if not spec:
                # Entrada vacia = operacion no concedida. Se ignora en silencio.
                continue
            if not isinstance(spec, dict):
                logger.warning("Operacion '%s' mal definida en el mapa de endpoints.", name)
                continue
            method = str(spec.get("method", "GET")).upper()
            path = str(spec.get("path") or "").strip()
            if method not in _VALID_METHODS:
                raise ConfigurationError(
                    f"Metodo HTTP no valido ('{method}') en la operacion '{name}'."
                )
            if not path:
                logger.warning(
                    "Operacion '%s' sin 'path' en el mapa de endpoints: se ignora.", name
                )
                continue
            response_raw = spec.get("response") or {}
            operations[name] = Operation(
                name=name,
                method=method,
                path=path,
                query=dict(spec.get("query") or {}),
                body=dict(spec.get("body") or {}),
                headers=dict(spec.get("headers") or {}),
                response=ResponseMapping(
                    collection_path=str(response_raw.get("collection_path") or ""),
                    fields=dict(response_raw.get("fields") or {}),
                    total_path=str(response_raw.get("total_path") or ""),
                ),
                notes=str(spec.get("notes") or ""),
            )

        return cls(
            base_url=str(api.get("base_url") or "").rstrip("/"),
            auth_header=str(api.get("auth_header") or "Authorization"),
            auth_scheme=str(api.get("auth_scheme") or "Bearer"),
            default_headers=dict(api.get("default_headers") or {}),
            timeout_seconds=float(api.get("timeout_seconds") or 30.0),
            oauth=oauth,
            operations=operations,
        )

    def describe(self) -> str:
        """Resumen legible para la pantalla de configuracion."""
        if not self.operations:
            return "Sin operaciones declaradas (modo DEMO)."
        lines = [f"Base: {self.base_url or '(sin definir)'}"]
        for name in sorted(self.operations):
            op = self.operations[name]
            lines.append(f"  - {name}: {op.method} {op.path}")
        return "\n".join(lines)


def empty_map() -> EndpointMap:
    """Mapa vacio: ninguna operacion disponible (situacion por defecto)."""
    return EndpointMap()
