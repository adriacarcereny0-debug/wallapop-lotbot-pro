"""Registro de herramientas disponibles para el agente IA."""

from __future__ import annotations

import logging
from typing import Any

from lot_bot.ai.tools.base import (
    Tool,
    ToolCategory,
    ToolContext,
    ToolResult,
    fail,
    unavailable,
)
from lot_bot.wallapop.capabilities import Capability

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Contiene las herramientas y las ejecuta de forma controlada."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ------------------------------------------------------------------
    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"La herramienta '{tool.name}' ya está registrada.")
        self._tools[tool.name] = tool

    def register_all(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[name] for name in sorted(self._tools)]

    def by_category(self) -> dict[ToolCategory, list[Tool]]:
        grouped: dict[ToolCategory, list[Tool]] = {}
        for tool in self.all():
            grouped.setdefault(tool.category, []).append(tool)
        return grouped

    # ------------------------------------------------------------------
    def schemas(self, available_only: bool = True, capabilities: set[str] | None = None) -> list[dict[str, Any]]:
        """Esquemas para el modelo. Las no disponibles se marcan, no se ocultan.

        Se mantienen visibles para que la IA pueda explicar al usuario que esa
        funcion existe pero no esta autorizada, en vez de callarse o inventar.
        """
        schemas: list[dict[str, Any]] = []
        for tool in self.all():
            schema = tool.schema()
            if (
                available_only
                and capabilities is not None
                and tool.capability is not None
                and tool.capability not in capabilities
            ):
                schema["description"] = (
                    f"[NO DISPONIBLE con los permisos actuales] {schema['description']} "
                    f"Si el usuario la pide, explica que requiere el permiso "
                    f"'{tool.capability}' y no la llames."
                )
            schemas.append(schema)
        return schemas

    def describe_for_prompt(self, capabilities: set[str] | None = None) -> str:
        """Listado legible de herramientas para el prompt del sistema."""
        lines: list[str] = []
        for category, tools in self.by_category().items():
            lines.append(f"\n{category.value.upper()}:")
            for tool in tools:
                marks = []
                if tool.requires_confirmation:
                    marks.append("requiere confirmación")
                if tool.destructive:
                    marks.append("destructiva")
                if (
                    capabilities is not None
                    and tool.capability is not None
                    and tool.capability not in capabilities
                ):
                    marks.append("NO DISPONIBLE")
                suffix = f" [{', '.join(marks)}]" if marks else ""
                lines.append(f"  - {tool.name}: {tool.description}{suffix}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def execute(
        self, name: str, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        """Ejecuta una herramienta comprobando permisos y confirmacion."""
        tool = self._tools.get(name)
        if tool is None:
            logger.warning("La IA ha pedido una herramienta inexistente: '%s'", name)
            return fail(
                f"La herramienta '{name}' no existe. "
                f"Herramientas disponibles: {', '.join(self.names())}."
            )

        # --- Permiso de Wallapop ---
        if tool.capability is not None:
            try:
                capability = Capability(tool.capability)
            except ValueError:
                capability = None
            if capability is not None and not context.app.wallapop.supports(capability):
                logger.info(
                    "Herramienta '%s' no disponible: falta la capacidad '%s'.",
                    name,
                    tool.capability,
                )
                return unavailable(
                    tool.capability,
                    "Concede el endpoint correspondiente en el fichero de endpoints oficial.",
                )

        # --- Confirmacion obligatoria ---
        if tool.requires_confirmation and not context.confirmed:
            logger.info("La herramienta '%s' requiere confirmación del usuario.", name)

        try:
            result = tool.handler(context, arguments or {})
        except NotImplementedError as exc:
            return unavailable(name, str(exc))
        except Exception as exc:  # una herramienta no debe tumbar la aplicacion
            logger.exception("Error ejecutando la herramienta '%s'", name)
            context.app.audit.record_error(
                f"Herramienta IA: {name}", error=str(exc), actor=context.actor
            )
            return fail(f"Error al ejecutar '{name}': {exc}")

        # Blindaje: una herramienta de escritura jamas puede devolver exito
        # sin confirmacion previa.
        if (
            tool.requires_confirmation
            and not context.confirmed
            and result.confirmation is None
            and result.ok
        ):
            logger.error(
                "BLOQUEADO: '%s' ha intentado ejecutarse sin confirmación.", name
            )
            return fail(
                f"La acción '{name}' modifica información y requiere confirmación "
                f"explícita del usuario. No se ha ejecutado."
            )
        return result
