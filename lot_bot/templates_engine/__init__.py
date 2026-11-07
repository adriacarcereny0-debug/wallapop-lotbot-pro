"""Sistema de plantillas de anuncios."""

from lot_bot.templates_engine.defaults import (
    CANAPE_TEMPLATE,
    DEFAULT_TEMPLATES,
    TEMPLATE_VARIABLES,
    ensure_default_templates,
)
from lot_bot.templates_engine.engine import (
    RenderedListing,
    TemplateEngine,
    TemplateError,
    extract_variables,
)

__all__ = [
    "CANAPE_TEMPLATE",
    "DEFAULT_TEMPLATES",
    "TEMPLATE_VARIABLES",
    "TemplateEngine",
    "TemplateError",
    "RenderedListing",
    "ensure_default_templates",
    "extract_variables",
]
