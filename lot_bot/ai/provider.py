"""Contrato de los proveedores de IA.

Un proveedor recibe la conversacion y las herramientas y devuelve, o bien una
respuesta de texto, o bien una o varias llamadas a herramientas.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolCall:
    """Peticion del modelo para ejecutar una herramienta."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderReply:
    """Respuesta de un proveedor en una vuelta del bucle del agente."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finished: bool = True
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class AIProvider(ABC):
    """Interfaz comun a todos los proveedores de IA."""

    name: str = "generico"
    #: True si entiende lenguaje natural libre.
    natural_language: bool = False

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ProviderReply:
        """Genera la siguiente respuesta del asistente."""

    @property
    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name
