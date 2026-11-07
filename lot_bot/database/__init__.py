"""Capa de persistencia local (SQLite + SQLAlchemy)."""

from lot_bot.database.engine import (
    Database,
    get_database,
    session_scope,
    set_database,
)
from lot_bot.database.models import (
    Account,
    AccountStatus,
    AuditLog,
    Automation,
    Conversation,
    Listing,
    ListingStatus,
    Message,
    Product,
    ProductAssignment,
    ProductImage,
    ProductStatus,
    Setting,
    Template,
)

__all__ = [
    "Database",
    "get_database",
    "set_database",
    "session_scope",
    "Account",
    "AccountStatus",
    "AuditLog",
    "Automation",
    "Conversation",
    "Listing",
    "ListingStatus",
    "Message",
    "Product",
    "ProductAssignment",
    "ProductImage",
    "ProductStatus",
    "Setting",
    "Template",
]
