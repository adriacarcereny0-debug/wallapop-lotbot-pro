"""Definicion de las herramientas que la IA puede utilizar.

PRINCIPIOS DE SEGURIDAD
-----------------------
1. La IA solo puede llamar a herramientas registradas aqui. No puede ejecutar
   codigo, ni hacer peticiones HTTP, ni tocar la base de datos directamente.
2. Toda herramienta que publique o modifique informacion lleva
   `requires_confirmation=True`: la primera llamada NO ejecuta nada, devuelve
   un plan que el usuario debe confirmar en la interfaz.
3. La IA nunca ve credenciales ni tokens.
4. Si la operacion necesita un permiso de Wallapop que no tenemos, la
   herramienta responde NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS en lugar de fingir.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from lot_bot.bootstrap import Application

logger = logging.getLogger(__name__)


class ToolCategory(str, Enum):
    ACCOUNTS = "cuentas"
    CATALOG = "catalogo"
    LISTINGS = "anuncios"
    CONTENT = "contenido"
    IMAGES = "imagenes"
    MESSAGES = "mensajes"
    MARKET = "mercado"


@dataclass(slots=True)
class ToolContext:
    """Todo lo que una herramienta necesita para trabajar."""

    app: Application
    confirmed: bool = False
    actor: str = "asistente"
    #: De qué se está hablando ahora mismo: el anuncio, la conversación o la
    #: plantilla que se acaba de mostrar. Es lo que da sentido a frases como
    #: «cambia el precio de este anuncio» o «prepara una respuesta para este
    #: cliente». Solo contiene identificadores, nunca datos sensibles.
    focus: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConfirmationRequest:
    """Plan de una accion pendiente de que el usuario la confirme."""

    token: str
    tool_name: str
    arguments: dict[str, Any]
    title: str
    lines: list[str] = field(default_factory=list)
    destructive: bool = False
    affected: int = 0

    def message(self) -> str:
        body = "\n".join(f"• {line}" for line in self.lines)
        warning = "\n\n⚠ ESTA ACCIÓN NO SE PUEDE DESHACER." if self.destructive else ""
        return f"{self.title}\n\n{body}{warning}\n\n¿Quieres continuar?"


@dataclass(slots=True)
class ToolResult:
    """Resultado de ejecutar una herramienta."""

    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    confirmation: ConfirmationRequest | None = None
    unavailable: bool = False
    #: Nuevo foco de la conversación (p. ej. {"listing_ids": [7]}).
    focus: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_confirmation(self) -> bool:
        return self.confirmation is not None

    def to_model_payload(self) -> dict[str, Any]:
        """Lo que se devuelve al modelo de IA (sin datos sensibles)."""
        payload: dict[str, Any] = {"ok": self.ok, "resumen": self.summary}
        if self.unavailable:
            payload["codigo"] = "NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS"
        if self.confirmation is not None:
            payload["estado"] = "PENDIENTE_DE_CONFIRMACION"
            payload["plan"] = self.confirmation.lines
            payload["nota"] = (
                "La acción no se ha ejecutado. Se ha mostrado al usuario para que "
                "la confirme o la cancele. No vuelvas a llamar a esta herramienta."
            )
        if self.data:
            payload["datos"] = self.data
        return payload


#: Firma de una herramienta.
ToolHandler = Callable[[ToolContext, dict[str, Any]], ToolResult]


@dataclass(slots=True)
class Tool:
    """Una funcion que la IA puede invocar."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    category: ToolCategory
    requires_confirmation: bool = False
    destructive: bool = False
    #: Capacidad de Wallapop necesaria (None si es puramente local).
    capability: str | None = None

    def schema(self) -> dict[str, Any]:
        """Esquema en el formato de function calling de la API de Claude."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.parameters.get("properties", {}),
                "required": self.parameters.get("required", []),
            },
        }


def new_confirmation_token() -> str:
    return uuid.uuid4().hex[:12]


def unavailable(operation: str, reason: str = "") -> ToolResult:
    """Resultado estandar cuando la operacion no esta autorizada."""
    text = (
        f"NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS: la operación '{operation}' no está "
        f"disponible con la integración autorizada actual."
    )
    if reason:
        text += f" {reason}"
    return ToolResult(ok=False, summary=text, unavailable=True)


def ok(summary: str, **data: Any) -> ToolResult:
    return ToolResult(ok=True, summary=summary, data=data)


def fail(summary: str, **data: Any) -> ToolResult:
    return ToolResult(ok=False, summary=summary, data=data)


def confirm_first(
    tool_name: str,
    arguments: dict[str, Any],
    title: str,
    lines: list[str],
    *,
    destructive: bool = False,
    affected: int = 0,
) -> ToolResult:
    """Devuelve un plan pendiente de confirmacion, SIN ejecutar nada."""
    request = ConfirmationRequest(
        token=new_confirmation_token(),
        tool_name=tool_name,
        arguments=arguments,
        title=title,
        lines=lines,
        destructive=destructive,
        affected=affected,
    )
    return ToolResult(
        ok=True,
        summary=f"Pendiente de confirmación: {title}",
        confirmation=request,
    )
