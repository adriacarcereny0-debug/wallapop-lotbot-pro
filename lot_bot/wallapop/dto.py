"""Objetos de transferencia entre la capa Wallapop y el resto de la aplicacion.

Son deliberadamente neutros: no dependen del formato concreto de ninguna API.
El mapeo entre estos DTO y la respuesta real se define en el mapa de endpoints
oficial (ver `endpoint_map.py`), no en el codigo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: list[str] = field(default_factory=list)
    token_type: str = "Bearer"


@dataclass(slots=True)
class AccountProfile:
    user_id: str
    display_name: str
    login: str | None = None
    active_items: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ItemImage:
    url: str
    remote_id: str | None = None
    position: int = 0


@dataclass(slots=True)
class Item:
    """Anuncio en Wallapop."""

    item_id: str
    title: str
    description: str = ""
    price: float | None = None
    currency: str = "EUR"
    category: str | None = None
    condition: str | None = None
    status: str = "active"
    attributes: dict[str, Any] = field(default_factory=dict)
    images: list[ItemImage] = field(default_factory=list)
    views: int = 0
    favorites: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def image_urls(self) -> list[str]:
        return [img.url for img in self.images]


@dataclass(slots=True)
class ItemDraft:
    """Datos de un anuncio a crear o modificar."""

    title: str
    description: str
    price: float
    currency: str = "EUR"
    category: str | None = None
    condition: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    image_paths: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": self.title,
            "description": self.description,
            "price": self.price,
            "currency": self.currency,
        }
        if self.category:
            payload["category"] = self.category
        if self.condition:
            payload["condition"] = self.condition
        if self.attributes:
            payload["attributes"] = dict(self.attributes)
        if self.image_urls:
            payload["images"] = list(self.image_urls)
        return payload


@dataclass(slots=True)
class ItemSearchQuery:
    text: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    category: str | None = None
    status: str | None = None
    #: Filtro por atributos del anuncio, p.ej. {"medida": "135x190"}.
    attributes: dict[str, str] = field(default_factory=dict)
    limit: int = 50
    offset: int = 0


@dataclass(slots=True)
class ConversationSummary:
    conversation_id: str
    buyer_name: str | None = None
    item_id: str | None = None
    subject: str | None = None
    unread: int = 0
    last_message_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChatMessage:
    message_id: str
    conversation_id: str
    direction: str  # "in" (comprador) | "out" (nuestra cuenta)
    body: str
    sent_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MarketDataPoint:
    title: str
    price: float
    currency: str = "EUR"
    category: str | None = None
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OperationResult:
    """Resultado uniforme de una operacion de escritura."""

    success: bool
    message: str = ""
    item_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
