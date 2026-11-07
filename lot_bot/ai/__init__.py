"""Agente IA: proveedores, herramientas y orquestacion."""

from lot_bot.ai.agent import Agent, AgentMessage, AgentResponse, PendingAction
from lot_bot.ai.anthropic_provider import AnthropicProvider
from lot_bot.ai.provider import AIProvider, ProviderReply, ToolCall
from lot_bot.ai.rule_provider import RuleBasedProvider
from lot_bot.ai.tools import ToolRegistry, build_registry


def build_provider(settings) -> AIProvider:
    """Elige el proveedor de IA segun la configuracion."""
    if settings.ai_provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.ai_model,
            max_tokens=settings.ai_max_tokens,
        )
    return RuleBasedProvider()


__all__ = [
    "Agent",
    "AgentMessage",
    "AgentResponse",
    "PendingAction",
    "AIProvider",
    "AnthropicProvider",
    "RuleBasedProvider",
    "ProviderReply",
    "ToolCall",
    "ToolRegistry",
    "build_registry",
    "build_provider",
]
