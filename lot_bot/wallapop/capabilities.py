"""Catalogo de operaciones que LOT Bot puede pedir a Wallapop.

Cada implementacion de `WallapopService` declara cuales soporta. Si una
operacion no esta declarada, la capa superior recibe
`NotAvailableWithCurrentAPIError` y NUNCA simula haberla ejecutado.
"""

from __future__ import annotations

import enum


class Capability(str, enum.Enum):
    # --- Cuenta ---
    ACCOUNT_PROFILE = "account_profile"

    # --- Anuncios (lectura) ---
    LIST_ITEMS = "list_items"
    GET_ITEM = "get_item"
    SEARCH_ITEMS = "search_items"

    # --- Anuncios (escritura) ---
    CREATE_ITEM = "create_item"
    UPDATE_ITEM = "update_item"
    DELETE_ITEM = "delete_item"
    UPDATE_ITEM_PRICE = "update_item_price"
    UPDATE_ITEM_IMAGES = "update_item_images"
    UPLOAD_IMAGE = "upload_image"

    # --- Categorias ---
    LIST_CATEGORIES = "list_categories"

    # --- Mensajeria ---
    LIST_CONVERSATIONS = "list_conversations"
    GET_CONVERSATION = "get_conversation"
    SEND_MESSAGE = "send_message"

    # --- Datos de mercado ---
    MARKET_DATA = "market_data"

    # --- Estadísticas de los anuncios propios ---
    ITEM_STATS = "item_stats"


#: Operaciones que modifican o publican informacion y por tanto exigen
#: confirmacion explicita del usuario antes de ejecutarse.
WRITE_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.CREATE_ITEM,
        Capability.UPDATE_ITEM,
        Capability.DELETE_ITEM,
        Capability.UPDATE_ITEM_PRICE,
        Capability.UPDATE_ITEM_IMAGES,
        Capability.UPLOAD_IMAGE,
        Capability.SEND_MESSAGE,
    }
)

#: Operaciones destructivas: ademas de confirmar, se avisa de forma destacada.
DESTRUCTIVE_CAPABILITIES: frozenset[Capability] = frozenset({Capability.DELETE_ITEM})

CAPABILITY_LABELS: dict[Capability, str] = {
    Capability.ACCOUNT_PROFILE: "Leer perfil de la cuenta",
    Capability.LIST_ITEMS: "Listar anuncios",
    Capability.GET_ITEM: "Consultar un anuncio",
    Capability.SEARCH_ITEMS: "Buscar anuncios",
    Capability.CREATE_ITEM: "Crear anuncio",
    Capability.UPDATE_ITEM: "Modificar anuncio",
    Capability.DELETE_ITEM: "Eliminar anuncio",
    Capability.UPDATE_ITEM_PRICE: "Cambiar precio",
    Capability.UPDATE_ITEM_IMAGES: "Actualizar fotografias",
    Capability.UPLOAD_IMAGE: "Subir fotografia",
    Capability.LIST_CATEGORIES: "Listar categorias",
    Capability.LIST_CONVERSATIONS: "Listar conversaciones",
    Capability.GET_CONVERSATION: "Leer una conversacion",
    Capability.SEND_MESSAGE: "Enviar mensaje",
    Capability.MARKET_DATA: "Consultar datos de mercado",
    Capability.ITEM_STATS: "Estadísticas de los anuncios",
}
