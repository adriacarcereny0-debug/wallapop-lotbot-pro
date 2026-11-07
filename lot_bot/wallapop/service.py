"""Contrato unico de comunicacion con Wallapop.

REGLA DE ARQUITECTURA: ningun otro modulo de LOT Bot habla con Wallapop.
Todo pasa por una implementacion de `WallapopService`. Esto permite cambiar
`MockWallapopService` por `ConnectWallapopService` sin tocar el resto del codigo.

Las implementaciones declaran sus capacidades en `capabilities()`. Cualquier
operacion no soportada debe lanzar `NotAvailableWithCurrentAPIError`; jamas
devolver un resultado inventado.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.dto import (
    AccountProfile,
    ChatMessage,
    ConversationSummary,
    Item,
    ItemDraft,
    ItemSearchQuery,
    MarketDataPoint,
    OperationResult,
)
from lot_bot.wallapop.errors import NotAvailableWithCurrentAPIError


class WallapopService(ABC):
    """Interfaz que toda integracion con Wallapop debe cumplir."""

    #: Etiqueta mostrada en la interfaz ("MODO DEMO" / "WALLAPOP REAL").
    backend_name: str = "abstracto"
    #: True si los datos son simulados.
    is_mock: bool = False

    # ------------------------------------------------------------------
    # Capacidades
    # ------------------------------------------------------------------
    @abstractmethod
    def capabilities(self) -> set[Capability]:
        """Operaciones realmente disponibles con los permisos actuales."""

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities()

    def require(self, capability: Capability) -> None:
        """Lanza NotAvailableWithCurrentAPIError si la operacion no existe."""
        if not self.supports(capability):
            raise NotAvailableWithCurrentAPIError(
                capability.value,
                "No figura en la configuracion de endpoints autorizados.",
            )

    # ------------------------------------------------------------------
    # Cuenta
    # ------------------------------------------------------------------
    @abstractmethod
    def get_account_profile(self, account_ref: str) -> AccountProfile:
        """Perfil de la cuenta conectada."""

    @abstractmethod
    def check_connection(self, account_ref: str) -> bool:
        """True si la cuenta responde correctamente con su token actual."""

    # ------------------------------------------------------------------
    # Anuncios - lectura
    # ------------------------------------------------------------------
    @abstractmethod
    def list_items(self, account_ref: str, limit: int = 100, offset: int = 0) -> list[Item]:
        ...

    @abstractmethod
    def get_item(self, account_ref: str, item_id: str) -> Item:
        ...

    @abstractmethod
    def search_items(self, account_ref: str, query: ItemSearchQuery) -> list[Item]:
        ...

    # ------------------------------------------------------------------
    # Anuncios - escritura
    # ------------------------------------------------------------------
    @abstractmethod
    def create_item(self, account_ref: str, draft: ItemDraft) -> OperationResult:
        ...

    @abstractmethod
    def update_item(
        self,
        account_ref: str,
        item_id: str,
        changes: dict[str, Any],
        item_url: str | None = None,
    ) -> OperationResult:
        ...

    @abstractmethod
    def delete_item(
        self, account_ref: str, item_id: str, item_url: str | None = None
    ) -> OperationResult:
        ...

    @abstractmethod
    def update_item_price(
        self, account_ref: str, item_id: str, price: float, item_url: str | None = None
    ) -> OperationResult:
        ...

    @abstractmethod
    def update_item_images(
        self, account_ref: str, item_id: str, image_urls: list[str]
    ) -> OperationResult:
        ...

    @abstractmethod
    def upload_image(self, account_ref: str, image_path: str) -> str:
        """Sube una imagen y devuelve la URL/identificador remoto."""

    # ------------------------------------------------------------------
    # Categorias
    # ------------------------------------------------------------------
    @abstractmethod
    def list_categories(self, account_ref: str) -> list[dict[str, Any]]:
        ...

    # ------------------------------------------------------------------
    # Mensajeria
    # ------------------------------------------------------------------
    @abstractmethod
    def list_conversations(self, account_ref: str, limit: int = 50) -> list[ConversationSummary]:
        ...

    @abstractmethod
    def get_conversation_messages(
        self, account_ref: str, conversation_id: str
    ) -> list[ChatMessage]:
        ...

    @abstractmethod
    def send_message(
        self, account_ref: str, conversation_id: str, body: str
    ) -> OperationResult:
        ...

    # ------------------------------------------------------------------
    # Mercado
    # ------------------------------------------------------------------
    @abstractmethod
    def get_market_data(
        self, account_ref: str, query: str, limit: int = 50
    ) -> list[MarketDataPoint]:
        ...

    # ------------------------------------------------------------------
    # Estadisticas de los anuncios propios
    # ------------------------------------------------------------------
    def get_item_stats(self, account_ref: str, item_ref: str) -> dict[str, Any]:
        """{"views": int|None, "favorites": int|None, "source": str}.

        Por defecto no disponible: solo lo implementa quien puede leer los
        datos de verdad. Nunca se devuelven cifras estimadas.
        """
        raise NotAvailableWithCurrentAPIError(
            Capability.ITEM_STATS.value, "Esta integración no puede leer estadísticas."
        )
