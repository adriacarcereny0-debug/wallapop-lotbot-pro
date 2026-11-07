"""Proveedor de IA basado en la API de Claude (Anthropic) con function calling."""

from __future__ import annotations

import logging
from typing import Any

from lot_bot.ai.provider import AIProvider, ProviderReply, ToolCall
from lot_bot.logs.redaction import register_secret

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"


class AnthropicProvider(AIProvider):
    """Interpreta lenguaje natural y decide que herramientas usar.

    La clave de API nunca se registra en los logs ni se muestra en pantalla.
    """

    name = "Claude (Anthropic)"
    natural_language = True

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 4096,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self._client = client
        if api_key:
            register_secret(api_key)

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def describe(self) -> str:
        return f"{self.name} · {self.model}"

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "La librería 'anthropic' no está instalada. "
                    "Cambia el proveedor de IA a 'rules' en Ajustes."
                ) from exc
            self._client = Anthropic(api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------
    def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ProviderReply:
        if not self.available:
            return ProviderReply(
                text=(
                    "No hay clave de API configurada para el asistente de IA. "
                    "Configúrala en Ajustes → IA o usa el modo de órdenes directas."
                )
            )

        client = self._get_client()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt,
                messages=messages,
                tools=tools or None,
            )
        except Exception as exc:
            logger.exception("Error llamando a la API de Claude")
            return ProviderReply(
                text=(
                    f"No he podido contactar con el servicio de IA ({type(exc).__name__}). "
                    f"Comprueba la conexión a internet y la clave de API en Ajustes."
                )
            )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in getattr(response, "content", []) or []:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(getattr(block, "text", ""))
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=getattr(block, "id", ""),
                        name=getattr(block, "name", ""),
                        arguments=dict(getattr(block, "input", {}) or {}),
                    )
                )

        stop_reason = getattr(response, "stop_reason", None)
        return ProviderReply(
            text="\n".join(p for p in text_parts if p).strip(),
            tool_calls=tool_calls,
            finished=stop_reason != "tool_use",
            raw=response,
        )
