"""Orquestador del agente IA.

Coordina: usuario -> proveedor de IA -> herramientas -> resultado.

El agente NUNCA ejecuta una accion de escritura por su cuenta: cuando una
herramienta devuelve un plan (`ConfirmationRequest`), el bucle se detiene y
espera a que el usuario pulse [Confirmar] o [Cancelar] en la interfaz.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from lot_bot.ai.prompts import build_system_prompt
from lot_bot.ai.provider import AIProvider, ProviderReply, ToolCall
from lot_bot.ai.tools.base import ConfirmationRequest, ToolContext, ToolResult
from lot_bot.ai.tools.registry import ToolRegistry

if TYPE_CHECKING:  # pragma: no cover
    from lot_bot.bootstrap import Application

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 6
MAX_HISTORY_MESSAGES = 40


@dataclass(slots=True)
class AgentMessage:
    """Mensaje visible en el chat del asistente."""

    role: str  # usuario | asistente | sistema | herramienta | error
    text: str
    timestamp: datetime = field(default_factory=datetime.now)
    tool_name: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PendingAction:
    """Accion a la espera de confirmacion del usuario."""

    request: ConfirmationRequest
    tool_call_id: str

    @property
    def token(self) -> str:
        return self.request.token


@dataclass(slots=True)
class AgentResponse:
    """Lo que el agente devuelve a la interfaz tras una orden."""

    messages: list[AgentMessage] = field(default_factory=list)
    pending: PendingAction | None = None
    tool_results: list[ToolResult] = field(default_factory=list)

    @property
    def needs_confirmation(self) -> bool:
        return self.pending is not None

    def text(self) -> str:
        return "\n".join(m.text for m in self.messages if m.role == "asistente" and m.text)


class Agent:
    """Agente conversacional con herramientas."""

    def __init__(
        self,
        app: Application,
        registry: ToolRegistry,
        provider: AIProvider,
    ) -> None:
        self._app = app
        self._registry = registry
        self._provider = provider
        self._messages: list[dict[str, Any]] = []
        self._pending: PendingAction | None = None
        #: De qué se está hablando (ver ToolContext.focus).
        self._focus: dict[str, Any] = {}

    # ------------------------------------------------------------------
    @property
    def provider(self) -> AIProvider:
        return self._provider

    def set_provider(self, provider: AIProvider) -> None:
        self._provider = provider
        logger.info("Proveedor de IA cambiado a: %s", provider.describe())

    @property
    def pending(self) -> PendingAction | None:
        return self._pending

    def reset(self) -> None:
        self._messages.clear()
        self._pending = None
        self._focus = {}

    def _resolved_focus(self) -> dict[str, Any]:
        """Tras «publica 3 canapés», «este anuncio» son los que ha publicado
        la cola hasta ahora."""
        focus = dict(self._focus)
        job_id = focus.get("publish_job")
        queue = getattr(self._app, "publish_queue", None)
        if job_id and queue is not None:
            ids = queue.published_listing_ids(job_id)
            if ids:
                focus["listing_ids"] = ids
        return focus

    @property
    def focus(self) -> dict[str, Any]:
        return self._resolved_focus()

    def history(self) -> list[dict[str, Any]]:
        return list(self._messages)

    # ------------------------------------------------------------------
    def _system_prompt(self) -> str:
        capabilities = {c.value for c in self._app.wallapop.capabilities()}
        accounts = self._app.accounts.list_accounts()
        accounts_summary = "\n".join(
            f"  - {a.alias} (referencia interna: {a.internal_ref}) · {a.status_label}"
            for a in accounts
        )
        business = self._app.business_settings
        business_summary = "\n".join(
            f"  - {key}: {value}" for key, value in business.items() if value
        ) or "  (sin datos de negocio configurados)"
        return build_system_prompt(
            self._registry.describe_for_prompt(capabilities),
            demo=self._app.demo_mode,
            backend_label=self._app.backend_label,
            accounts_summary=accounts_summary,
            business_summary=business_summary,
        )

    def _tool_schemas(self) -> list[dict[str, Any]]:
        capabilities = {c.value for c in self._app.wallapop.capabilities()}
        return self._registry.schemas(capabilities=capabilities)

    # ------------------------------------------------------------------
    def ask(self, user_text: str) -> AgentResponse:
        """Procesa una orden del usuario."""
        user_text = (user_text or "").strip()
        if not user_text:
            return AgentResponse(messages=[AgentMessage("asistente", "Dime qué quieres hacer.")])

        if self._pending is not None:
            # Hay algo pendiente de confirmar: no se acepta otra orden encima.
            return AgentResponse(
                messages=[
                    AgentMessage(
                        "asistente",
                        "Antes de seguir, confirma o cancela la acción pendiente.",
                    )
                ],
                pending=self._pending,
            )

        self._append({"role": "user", "content": user_text})
        self._app.audit.record(
            "Orden al asistente", actor="usuario", detail=user_text[:400]
        )
        awaiting = self._focus.pop("awaiting", None)
        if awaiting:
            return self._answer_question(awaiting, user_text)
        return self._run_loop()

    def _answer_question(self, awaiting: dict[str, Any], user_text: str) -> AgentResponse:
        """El asistente había hecho una pregunta (p. ej. «¿En qué cuenta?»): la
        respuesta del usuario completa esa misma acción."""
        if re.search(r"^\s*(no|cancela|cancelar|déjalo|dejalo|olvídalo|olvidalo|nada)\b", user_text.lower()):
            self._append({"role": "assistant", "content": "De acuerdo, lo dejamos."})
            return AgentResponse(messages=[AgentMessage("asistente", "De acuerdo, lo dejamos.")])
        call = ToolCall(
            id=f"respuesta-{awaiting['tool']}",
            name=awaiting["tool"],
            arguments={**awaiting.get("args", {}), "respuesta": user_text},
        )
        result = self._execute(call)
        response = AgentResponse(tool_results=[result])
        response.messages.append(
            AgentMessage("herramienta", result.summary, tool_name=call.name, data=result.data)
        )
        self._append({"role": "assistant", "content": result.summary})
        if result.confirmation is not None:
            self._pending = PendingAction(result.confirmation, call.id)
            response.pending = self._pending
            response.messages.append(AgentMessage("sistema", self._pending.request.message()))
        return response

    # ------------------------------------------------------------------
    def _run_loop(self) -> AgentResponse:
        response = AgentResponse()

        for iteration in range(MAX_ITERATIONS):
            try:
                reply: ProviderReply = self._provider.complete(
                    self._system_prompt(), self._messages, self._tool_schemas()
                )
            except Exception as exc:
                logger.exception("Fallo del proveedor de IA")
                response.messages.append(
                    AgentMessage("error", f"El asistente no ha podido responder: {exc}")
                )
                return response

            if reply.text:
                response.messages.append(AgentMessage("asistente", reply.text))

            if not reply.wants_tools:
                self._append({"role": "assistant", "content": reply.text or "(sin respuesta)"})
                return response

            self._append(
                {"role": "assistant", "content": _assistant_blocks(reply)}
            )

            tool_blocks: list[dict[str, Any]] = []
            for call in reply.tool_calls:
                result = self._execute(call)
                response.tool_results.append(result)
                response.messages.append(
                    AgentMessage(
                        "herramienta",
                        result.summary,
                        tool_name=call.name,
                        data=result.data,
                    )
                )
                tool_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": _as_text(result.to_model_payload()),
                        "is_error": not result.ok,
                    }
                )
                if result.confirmation is not None:
                    self._pending = PendingAction(result.confirmation, call.id)

            self._append({"role": "user", "content": tool_blocks})

            if self._pending is not None:
                response.pending = self._pending
                response.messages.append(
                    AgentMessage("sistema", self._pending.request.message())
                )
                return response

            if iteration == MAX_ITERATIONS - 1:
                response.messages.append(
                    AgentMessage(
                        "asistente",
                        "He alcanzado el número máximo de pasos. Revisa los resultados "
                        "anteriores o concreta un poco más la orden.",
                    )
                )
        return response

    # ------------------------------------------------------------------
    def _execute(self, call: ToolCall, confirmed: bool = False) -> ToolResult:
        logger.info(
            "Herramienta solicitada por la IA: %s(%s)%s",
            call.name,
            ", ".join(call.arguments),
            " [CONFIRMADA]" if confirmed else "",
        )
        context = ToolContext(
            app=self._app, confirmed=confirmed, actor="asistente", focus=self._resolved_focus()
        )
        result = self._registry.execute(call.name, call.arguments, context)
        if result.focus:
            if "listing_ids" in result.focus:
                self._focus.pop("publish_job", None)
            self._focus.update(result.focus)
        if result.confirmation is not None and self._app.demo_mode:
            # Que nadie confunda una confirmación DEMO con una operación real.
            note = "MODO DEMO: no se enviará nada a Wallapop; es una simulación."
            if note not in result.confirmation.lines:
                result.confirmation.lines.append(note)
        return result

    # ------------------------------------------------------------------
    def confirm(self, token: str) -> AgentResponse:
        """El usuario ha pulsado [Confirmar]: ahora si se ejecuta la accion."""
        if self._pending is None or self._pending.token != token:
            return AgentResponse(
                messages=[AgentMessage("error", "No hay ninguna acción pendiente con ese código.")]
            )

        request = self._pending.request
        self._pending = None
        call = ToolCall(id=f"confirmed-{request.token}", name=request.tool_name, arguments=request.arguments)
        result = self._execute(call, confirmed=True)

        self._app.audit.record(
            f"Confirmación de acción: {request.tool_name}",
            actor="usuario",
            result="ok" if result.ok else "error",
            detail=request.title,
            error=None if result.ok else result.summary,
        )

        response = AgentResponse(tool_results=[result])
        response.messages.append(
            AgentMessage("herramienta", result.summary, tool_name=request.tool_name, data=result.data)
        )
        self._append(
            {
                "role": "user",
                "content": (
                    f"[El usuario ha CONFIRMADO la acción '{request.tool_name}'. "
                    f"Resultado: {result.summary}] "
                    f"Resume brevemente el resultado al usuario."
                ),
            }
        )
        # Solo pedimos un resumen al modelo si entiende lenguaje natural; con el
        # proveedor de ordenes directas basta con el resultado de la herramienta.
        final_text = result.summary
        if self._provider.natural_language:
            summary = self._provider.complete(
                self._system_prompt(), self._messages, self._tool_schemas()
            )
            if summary.text:
                final_text = summary.text
        response.messages.append(AgentMessage("asistente", final_text))
        self._append({"role": "assistant", "content": final_text})
        return response

    def cancel(self, token: str) -> AgentResponse:
        """El usuario ha pulsado [Cancelar]: no se ejecuta nada."""
        if self._pending is None or self._pending.token != token:
            return AgentResponse(
                messages=[AgentMessage("error", "No hay ninguna acción pendiente con ese código.")]
            )
        request = self._pending.request
        self._pending = None
        self._app.audit.record_cancelled(
            f"Acción cancelada: {request.tool_name}", actor="usuario", detail=request.title
        )
        self._append(
            {
                "role": "user",
                "content": f"[El usuario ha CANCELADO la acción '{request.tool_name}'. No se ha ejecutado nada.]",
            }
        )
        self._append({"role": "assistant", "content": "De acuerdo, no he hecho ningún cambio."})
        return AgentResponse(
            messages=[
                AgentMessage("asistente", "De acuerdo, no he hecho ningún cambio."),
            ]
        )

    # ------------------------------------------------------------------
    def _append(self, message: dict[str, Any]) -> None:
        self._messages.append(message)
        if len(self._messages) > MAX_HISTORY_MESSAGES:
            # Se recorta por el principio, respetando que el historial no
            # empiece por un bloque de resultados de herramienta huerfano.
            excess = len(self._messages) - MAX_HISTORY_MESSAGES
            trimmed = self._messages[excess:]
            while trimmed and _is_tool_result_message(trimmed[0]):
                trimmed = trimmed[1:]
            self._messages = trimmed


def _assistant_blocks(reply: ProviderReply) -> list[dict[str, Any]]:
    """Reconstruye los bloques del mensaje del asistente en formato API."""
    blocks: list[dict[str, Any]] = []
    if reply.text:
        blocks.append({"type": "text", "text": reply.text})
    for call in reply.tool_calls:
        blocks.append(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        )
    return blocks or [{"type": "text", "text": "(sin contenido)"}]


def _is_tool_result_message(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def _as_text(payload: dict[str, Any]) -> str:
    import json

    try:
        return json.dumps(payload, ensure_ascii=False, default=str)[:12000]
    except (TypeError, ValueError):
        return str(payload)[:12000]
