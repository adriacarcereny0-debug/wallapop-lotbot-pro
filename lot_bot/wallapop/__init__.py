"""Capa de integracion con Wallapop.

Punto unico de comunicacion con Wallapop. Ningun otro modulo de LOT Bot hace
peticiones a Wallapop directamente.
"""

from lot_bot.wallapop.access_profile import AccessProfile, empty_profile
from lot_bot.wallapop.account_manager import AccountInfo, AccountManager
from lot_bot.wallapop.auth import (
    AuthCredential,
    AuthKind,
    AuthMethod,
    AuthOutcome,
    AuthRequirement,
    available_methods,
    build_auth_method,
)
from lot_bot.wallapop.authorized_service import (
    AuthorizedWallapopService,
    ConnectWallapopService,
)
from lot_bot.wallapop.capabilities import (
    CAPABILITY_LABELS,
    DESTRUCTIVE_CAPABILITIES,
    WRITE_CAPABILITIES,
    Capability,
)
from lot_bot.wallapop.dto import (
    AccountProfile,
    ChatMessage,
    ConversationSummary,
    Item,
    ItemDraft,
    ItemImage,
    ItemSearchQuery,
    MarketDataPoint,
    OAuthTokens,
    OperationResult,
)
from lot_bot.wallapop.endpoint_map import EndpointMap
from lot_bot.wallapop.errors import (
    AccessNotConfiguredError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    NetworkError,
    NotAvailableWithCurrentAccessError,
    NotAvailableWithCurrentAPIError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    ValidationRejectedError,
    WallapopError,
)
from lot_bot.wallapop.factory import WallapopBackend, build_backend
from lot_bot.wallapop.mock_service import MockWallapopService
from lot_bot.wallapop.service import WallapopService

__all__ = [
    "AccessProfile",
    "empty_profile",
    "AccountInfo",
    "AccountManager",
    "AuthCredential",
    "AuthKind",
    "AuthMethod",
    "AuthOutcome",
    "AuthRequirement",
    "available_methods",
    "build_auth_method",
    "AuthorizedWallapopService",
    "AccountProfile",
    "Capability",
    "CAPABILITY_LABELS",
    "WRITE_CAPABILITIES",
    "DESTRUCTIVE_CAPABILITIES",
    "ChatMessage",
    "ConnectWallapopService",
    "ConversationSummary",
    "EndpointMap",
    "Item",
    "ItemDraft",
    "ItemImage",
    "ItemSearchQuery",
    "MarketDataPoint",
    "MockWallapopService",
    "OAuthTokens",
    "OperationResult",
    "WallapopBackend",
    "WallapopService",
    "build_backend",
    "WallapopError",
    "AuthenticationError",
    "AuthorizationError",
    "ConfigurationError",
    "NetworkError",
    "NotAvailableWithCurrentAccessError",
    "NotAvailableWithCurrentAPIError",
    "AccessNotConfiguredError",
    "NotFoundError",
    "RateLimitError",
    "ServiceUnavailableError",
    "ValidationRejectedError",
]
