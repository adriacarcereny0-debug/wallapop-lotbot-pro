"""Herramientas disponibles para el agente IA."""

from lot_bot.ai.tools.account_tools import ACCOUNT_TOOLS
from lot_bot.ai.tools.base import (
    ConfirmationRequest,
    Tool,
    ToolCategory,
    ToolContext,
    ToolResult,
)
from lot_bot.ai.tools.catalog_tools import CATALOG_TOOLS
from lot_bot.ai.tools.content_tools import CONTENT_TOOLS
from lot_bot.ai.tools.image_tools import IMAGE_TOOLS
from lot_bot.ai.tools.listing_tools import LISTING_TOOLS
from lot_bot.ai.tools.market_tools import MARKET_TOOLS
from lot_bot.ai.tools.master_ad_tools import MASTER_AD_TOOLS
from lot_bot.ai.tools.message_tools import MESSAGE_TOOLS
from lot_bot.ai.tools.registry import ToolRegistry
from lot_bot.ai.tools.stats_tools import STATS_TOOLS


def build_registry() -> ToolRegistry:
    """Crea el registro con todas las herramientas de LOT Bot."""
    registry = ToolRegistry()
    registry.register_all(ACCOUNT_TOOLS)
    registry.register_all(CATALOG_TOOLS)
    registry.register_all(LISTING_TOOLS)
    registry.register_all(MASTER_AD_TOOLS)
    registry.register_all(CONTENT_TOOLS)
    registry.register_all(MESSAGE_TOOLS)
    registry.register_all(MARKET_TOOLS)
    registry.register_all(IMAGE_TOOLS)
    registry.register_all(STATS_TOOLS)
    return registry


__all__ = [
    "build_registry",
    "ToolRegistry",
    "Tool",
    "ToolCategory",
    "ToolContext",
    "ToolResult",
    "ConfirmationRequest",
]
