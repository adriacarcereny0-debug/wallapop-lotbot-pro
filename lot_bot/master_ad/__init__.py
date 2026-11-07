"""Anuncio principal del negocio (plantilla maestra)."""

from lot_bot.master_ad.defaults import (
    CLIENT_DESCRIPTION_RENDERED,
    CLIENT_MASTER_AD,
    MASTER_KEY,
    MASTER_NAME,
)
from lot_bot.master_ad.service import (
    DEMO_CATEGORY,
    EDITABLE_FIELDS,
    OVERRIDABLE_FIELDS,
    MasterAdService,
    MasterAdView,
    format_price_value,
)

__all__ = [
    "CLIENT_DESCRIPTION_RENDERED",
    "CLIENT_MASTER_AD",
    "MASTER_KEY",
    "MASTER_NAME",
    "DEMO_CATEGORY",
    "EDITABLE_FIELDS",
    "OVERRIDABLE_FIELDS",
    "MasterAdService",
    "MasterAdView",
    "format_price_value",
]
