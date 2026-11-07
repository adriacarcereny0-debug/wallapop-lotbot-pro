"""MockWallapopService: backend simulado para el MODO DEMO.

Permite probar TODA la aplicacion (interfaz, agente IA, automatizaciones,
publicacion, mensajeria) sin tocar Wallapop ni necesitar credenciales.

Caracteristicas:
  * Cuatro cuentas de demostracion aisladas entre si.
  * Anuncios de canapes con datos coherentes.
  * Conversaciones y mensajes de compradores.
  * Datos de mercado simulados.
  * Errores reproducibles para poder probar el manejo de errores
    (ver `SIMULATED_ERROR_TRIGGERS`).
  * El estado se guarda en disco, de modo que lo publicado en DEMO sigue
    ahi al reabrir el programa.

Todo lo que devuelve este servicio va marcado como DEMO en la interfaz.
"""

from __future__ import annotations

import json
import logging
import random
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lot_bot.config.paths import get_paths
from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.dto import (
    AccountProfile,
    ChatMessage,
    ConversationSummary,
    Item,
    ItemDraft,
    ItemImage,
    ItemSearchQuery,
    MarketDataPoint,
    OperationResult,
)
from lot_bot.wallapop.errors import (
    NotFoundError,
    RateLimitError,
    ValidationRejectedError,
)
from lot_bot.wallapop.service import WallapopService

logger = logging.getLogger(__name__)

#: Textos que provocan un error simulado, para poder probar el manejo de fallos.
SIMULATED_ERROR_TRIGGERS = {
    "__error_validacion__": "validation",
    "__error_limite__": "rate_limit",
    "__error_noexiste__": "not_found",
}

DEMO_ACCOUNTS: list[dict[str, str]] = [
    {"ref": "demo-1", "alias": "Cuenta 1 (DEMO)", "user_id": "u-demo-1", "login": "tienda.uno"},
    {"ref": "demo-2", "alias": "Cuenta 2 (DEMO)", "user_id": "u-demo-2", "login": "tienda.dos"},
    {"ref": "demo-3", "alias": "Cuenta 3 (DEMO)", "user_id": "u-demo-3", "login": "tienda.tres"},
    {"ref": "demo-4", "alias": "Cuenta 4 (DEMO)", "user_id": "u-demo-4", "login": "tienda.cuatro"},
]

_SIZES = ["90x190", "105x190", "135x190", "150x190", "160x200"]
_COLORS = ["Gris", "Blanco", "Roble", "Nogal", "Gris y Blanco"]
_MATERIALS = ["Madera", "Tapizado 3D", "Polipiel"]
_BASE_PRICES = {"90x190": 230.0, "105x190": 250.0, "135x190": 270.0, "150x190": 290.0, "160x200": 310.0}

_DEMO_DESCRIPTION = (
    "GRAN OFERTA LIMITADA!\n"
    "Renueva tu descanso hoy y paga menos\n"
    "Canape + colchon 90x190 -> 230 EUR\n"
    "Canape + colchon 135x190 -> 270 EUR\n"
    "Canape + colchon 150x190 -> 290 EUR\n"
    "Transporte y montaje GRATUITO\n"
    "Pide el tuyo ahora por WhatsApp\n"
    "Solo por tiempo limitado."
)

_BUYER_QUESTIONS = [
    "Hola, esta disponible el canape?",
    "Que precio tiene con el colchon incluido?",
    "Hacen envio a Sabadell?",
    "El montaje esta incluido?",
    "Lo teneis en 135x190?",
    "Se puede ver en tienda?",
]


def _now() -> datetime:
    return datetime.now(UTC)


class MockWallapopService(WallapopService):
    """Backend simulado. No realiza ninguna llamada de red."""

    backend_name = "DEMO (MockWallapopService)"
    is_mock = True

    def __init__(self, state_file: Path | None = None, seed: int = 20260922) -> None:
        self._lock = threading.RLock()
        self._rng = random.Random(seed)
        self._state_file = state_file if state_file is not None else get_paths().root / "demo_state.json"
        self._items: dict[str, dict[str, dict[str, Any]]] = {}
        self._conversations: dict[str, dict[str, dict[str, Any]]] = {}
        self._messages: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._counter = 1000
        if not self._load():
            self._seed()
            self._save()

    # ------------------------------------------------------------------
    # Capacidades: en DEMO todas estan disponibles
    # ------------------------------------------------------------------
    def capabilities(self) -> set[Capability]:
        return set(Capability)

    # ------------------------------------------------------------------
    # Persistencia del estado de demostracion
    # ------------------------------------------------------------------
    def _load(self) -> bool:
        if self._state_file is None or not self._state_file.is_file():
            return False
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._items = data.get("items", {})
            self._conversations = data.get("conversations", {})
            self._messages = data.get("messages", {})
            self._counter = data.get("counter", 1000)
            return bool(self._items)
        except (OSError, ValueError) as exc:
            logger.warning("Estado DEMO ilegible (%s). Se regenera.", exc)
            return False

    def _save(self) -> None:
        if self._state_file is None:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps(
                    {
                        "items": self._items,
                        "conversations": self._conversations,
                        "messages": self._messages,
                        "counter": self._counter,
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover
            logger.warning("No se ha podido guardar el estado DEMO: %s", exc)

    def reset(self) -> None:
        """Regenera los datos de demostracion desde cero."""
        with self._lock:
            self._items.clear()
            self._conversations.clear()
            self._messages.clear()
            self._counter = 1000
            self._rng = random.Random(20260922)
            self._seed()
            self._save()

    # ------------------------------------------------------------------
    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    def _seed(self) -> None:
        for index, account in enumerate(DEMO_ACCOUNTS):
            ref = account["ref"]
            self._items[ref] = {}
            self._conversations[ref] = {}
            self._messages[ref] = {}

            for n in range(6 + index):
                size = _SIZES[(n + index) % len(_SIZES)]
                color = _COLORS[(n + index) % len(_COLORS)]
                material = _MATERIALS[(n + index) % len(_MATERIALS)]
                item_id = self._next_id("item")
                # Algunos anuncios se crean con datos incompletos a proposito,
                # para que las pantallas de calidad y duplicados tengan trabajo.
                incomplete = n == 2
                duplicate = n == 4 and index > 0
                title = f"Canape abatible {size} {color} {material}"
                if duplicate:
                    title = f"Canape abatible {_SIZES[0]} {_COLORS[0]} {_MATERIALS[0]}"
                self._items[ref][item_id] = {
                    "item_id": item_id,
                    "title": title,
                    "description": "" if incomplete else _DEMO_DESCRIPTION,
                    "price": None if incomplete else _BASE_PRICES.get(size, 260.0),
                    "currency": "EUR",
                    "category": "Hogar y jardin",
                    "condition": "Nuevo",
                    "status": "active",
                    "attributes": {
                        "medida": size,
                        "color": color,
                        "material": material,
                        "estado": "Nuevo",
                    },
                    "images": [
                        {"url": f"demo://foto/{item_id}/{i}", "remote_id": f"img-{item_id}-{i}", "position": i}
                        for i in range(0 if incomplete else 3)
                    ],
                    "views": self._rng.randint(20, 900),
                    "favorites": self._rng.randint(0, 60),
                    "created_at": (_now() - timedelta(days=self._rng.randint(1, 90))).isoformat(),
                    "updated_at": _now().isoformat(),
                }

            for n in range(2 + index):
                conv_id = self._next_id("conv")
                item_ids = list(self._items[ref])
                item_id = item_ids[n % len(item_ids)] if item_ids else None
                self._conversations[ref][conv_id] = {
                    "conversation_id": conv_id,
                    "buyer_name": f"Comprador {n + 1}",
                    "item_id": item_id,
                    "subject": self._items[ref][item_id]["title"] if item_id else None,
                    "unread": 1 if n == 0 else 0,
                    "last_message_at": (_now() - timedelta(hours=self._rng.randint(1, 72))).isoformat(),
                }
                self._messages[ref][conv_id] = [
                    {
                        "message_id": self._next_id("msg"),
                        "conversation_id": conv_id,
                        "direction": "in",
                        "body": _BUYER_QUESTIONS[(n + index) % len(_BUYER_QUESTIONS)],
                        "sent_at": (_now() - timedelta(hours=self._rng.randint(1, 72))).isoformat(),
                    }
                ]

    # ------------------------------------------------------------------
    def _account(self, account_ref: str) -> dict[str, dict[str, Any]]:
        if account_ref not in self._items:
            # Una cuenta DEMO nueva empieza vacia, pero aislada.
            self._items[account_ref] = {}
            self._conversations.setdefault(account_ref, {})
            self._messages.setdefault(account_ref, {})
        return self._items[account_ref]

    @staticmethod
    def _check_triggers(*texts: str | None) -> None:
        """Dispara errores simulados si el texto contiene un marcador."""
        blob = " ".join(t for t in texts if t).lower()
        for trigger, kind in SIMULATED_ERROR_TRIGGERS.items():
            if trigger in blob:
                if kind == "validation":
                    raise ValidationRejectedError(
                        "Error simulado de validación (modo DEMO).",
                        fields={"titulo": "Formato no aceptado"},
                    )
                if kind == "rate_limit":
                    raise RateLimitError("Límite de peticiones simulado (modo DEMO).", retry_after=60)
                if kind == "not_found":
                    raise NotFoundError("Elemento inexistente simulado (modo DEMO).")

    @staticmethod
    def _to_item(raw: dict[str, Any]) -> Item:
        return Item(
            item_id=raw["item_id"],
            title=raw.get("title", ""),
            description=raw.get("description", "") or "",
            price=raw.get("price"),
            currency=raw.get("currency", "EUR"),
            category=raw.get("category"),
            condition=raw.get("condition"),
            status=raw.get("status", "active"),
            attributes=dict(raw.get("attributes") or {}),
            images=[
                ItemImage(url=i["url"], remote_id=i.get("remote_id"), position=i.get("position", 0))
                for i in raw.get("images") or []
            ],
            views=raw.get("views", 0),
            favorites=raw.get("favorites", 0),
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Cuenta
    # ------------------------------------------------------------------
    def get_account_profile(self, account_ref: str) -> AccountProfile:
        with self._lock:
            meta = next((a for a in DEMO_ACCOUNTS if a["ref"] == account_ref), None)
            items = self._account(account_ref)
            return AccountProfile(
                user_id=meta["user_id"] if meta else f"u-{account_ref}",
                display_name=meta["alias"] if meta else f"Cuenta {account_ref} (DEMO)",
                login=meta["login"] if meta else account_ref,
                active_items=sum(1 for i in items.values() if i.get("status") == "active"),
                raw={"demo": True},
            )

    def check_connection(self, account_ref: str) -> bool:
        return True

    # ------------------------------------------------------------------
    # Anuncios - lectura
    # ------------------------------------------------------------------
    def list_items(self, account_ref: str, limit: int = 100, offset: int = 0) -> list[Item]:
        with self._lock:
            values = list(self._account(account_ref).values())
        return [self._to_item(raw) for raw in values[offset : offset + limit]]

    def get_item_stats(self, account_ref: str, item_ref: str) -> dict[str, Any]:
        """Estadísticas SIMULADAS de un anuncio DEMO (crecen con el tiempo).

        Solo existen en modo demostración y se marcan como «demo»: nunca se
        presentan como datos reales de Wallapop.
        """
        item_id = str(item_ref).rstrip("/").rsplit("/", 1)[-1]
        with self._lock:
            raw = self._account(account_ref).get(item_id)
            if raw is None:
                raise NotFoundError(f"El anuncio '{item_id}' no existe en la cuenta {account_ref}.")
            raw["views"] = int(raw.get("views", 0)) + self._rng.randint(0, 40)
            raw["favorites"] = int(raw.get("favorites", 0)) + self._rng.randint(0, 3)
            self._save()
            return {"views": raw["views"], "favorites": raw["favorites"], "source": "demo"}

    def get_item(self, account_ref: str, item_id: str) -> Item:
        with self._lock:
            raw = self._account(account_ref).get(item_id)
        if raw is None:
            raise NotFoundError(f"El anuncio '{item_id}' no existe en la cuenta {account_ref}.")
        return self._to_item(raw)

    def search_items(self, account_ref: str, query: ItemSearchQuery) -> list[Item]:
        self._check_triggers(query.text)
        results: list[Item] = []
        with self._lock:
            for raw in self._account(account_ref).values():
                if query.text:
                    haystack = f"{raw.get('title','')} {raw.get('description','')} {raw.get('attributes')}".lower()
                    if query.text.lower() not in haystack:
                        continue
                price = raw.get("price")
                if query.min_price is not None and (price is None or price < query.min_price):
                    continue
                if query.max_price is not None and (price is None or price > query.max_price):
                    continue
                if query.category and raw.get("category") != query.category:
                    continue
                if query.status and raw.get("status") != query.status:
                    continue
                if query.attributes:
                    attrs = {k.lower(): str(v).lower() for k, v in (raw.get("attributes") or {}).items()}
                    if any(
                        attrs.get(key.lower()) != str(value).lower()
                        for key, value in query.attributes.items()
                    ):
                        continue
                results.append(self._to_item(raw))
        return results[query.offset : query.offset + query.limit]

    # ------------------------------------------------------------------
    # Anuncios - escritura
    # ------------------------------------------------------------------
    def create_item(self, account_ref: str, draft: ItemDraft) -> OperationResult:
        self._check_triggers(draft.title, draft.description)
        missing = []
        if not draft.title.strip():
            missing.append("titulo")
        if not draft.description.strip():
            missing.append("descripcion")
        if draft.price is None or draft.price <= 0:
            missing.append("precio")
        if missing:
            raise ValidationRejectedError(
                f"Campos obligatorios ausentes: {', '.join(missing)}.",
                fields={m: "obligatorio" for m in missing},
            )

        with self._lock:
            items = self._account(account_ref)
            item_id = self._next_id("item")
            items[item_id] = {
                "item_id": item_id,
                "title": draft.title,
                "description": draft.description,
                "price": float(draft.price),
                "currency": draft.currency,
                "category": draft.category,
                "condition": draft.condition or "Nuevo",
                "status": "active",
                "attributes": dict(draft.attributes),
                "images": [
                    {"url": url, "remote_id": f"img-{item_id}-{i}", "position": i}
                    for i, url in enumerate(draft.image_urls or draft.image_paths)
                ],
                "views": 0,
                "favorites": 0,
                "created_at": _now().isoformat(),
                "updated_at": _now().isoformat(),
            }
            self._save()
        return OperationResult(
            success=True, message="Anuncio publicado en modo DEMO (simulado, no está en Wallapop).", item_id=item_id
        )

    def update_item(
        self,
        account_ref: str,
        item_id: str,
        changes: dict[str, Any],
        item_url: str | None = None,
    ) -> OperationResult:
        self._check_triggers(str(changes.get("title")), str(changes.get("description")))
        with self._lock:
            items = self._account(account_ref)
            raw = items.get(item_id)
            if raw is None:
                raise NotFoundError(f"El anuncio '{item_id}' no existe en la cuenta {account_ref}.")
            allowed = {
                "title",
                "description",
                "price",
                "currency",
                "category",
                "condition",
                "status",
                "attributes",
            }
            applied = {}
            for key, value in changes.items():
                if key in allowed:
                    raw[key] = value
                    applied[key] = value
            raw["updated_at"] = _now().isoformat()
            self._save()
        return OperationResult(
            success=True,
            message=f"Anuncio actualizado en modo DEMO (simulado): {', '.join(applied) or 'sin cambios'}.",
            item_id=item_id,
            data=applied,
        )

    def delete_item(
        self, account_ref: str, item_id: str, item_url: str | None = None
    ) -> OperationResult:
        with self._lock:
            items = self._account(account_ref)
            if item_id not in items:
                raise NotFoundError(f"El anuncio '{item_id}' no existe en la cuenta {account_ref}.")
            items.pop(item_id)
            self._save()
        return OperationResult(success=True, message="Anuncio eliminado en modo DEMO (simulado).", item_id=item_id)

    def update_item_price(
        self, account_ref: str, item_id: str, price: float, item_url: str | None = None
    ) -> OperationResult:
        if price is None or price <= 0:
            raise ValidationRejectedError("El precio debe ser mayor que cero.")
        return self.update_item(account_ref, item_id, {"price": float(price)})

    def update_item_images(self, account_ref: str, item_id: str, image_urls: list[str]) -> OperationResult:
        with self._lock:
            items = self._account(account_ref)
            raw = items.get(item_id)
            if raw is None:
                raise NotFoundError(f"El anuncio '{item_id}' no existe en la cuenta {account_ref}.")
            raw["images"] = [
                {"url": url, "remote_id": f"img-{item_id}-{i}", "position": i}
                for i, url in enumerate(image_urls)
            ]
            raw["updated_at"] = _now().isoformat()
            self._save()
        return OperationResult(
            success=True,
            message=f"{len(image_urls)} fotografías actualizadas en modo DEMO (simulado).",
            item_id=item_id,
        )

    def upload_image(self, account_ref: str, image_path: str) -> str:
        name = Path(image_path).name
        return f"demo://subida/{account_ref}/{name}"

    # ------------------------------------------------------------------
    def list_categories(self, account_ref: str) -> list[dict[str, Any]]:
        return [
            {"id": "home_garden", "name": "Hogar y jardin"},
            {"id": "furniture", "name": "Muebles"},
            {"id": "mattresses", "name": "Colchones y canapes"},
            {"id": "decoration", "name": "Decoracion"},
        ]

    # ------------------------------------------------------------------
    # Mensajeria
    # ------------------------------------------------------------------
    def list_conversations(self, account_ref: str, limit: int = 50) -> list[ConversationSummary]:
        with self._lock:
            raws = list(self._conversations.get(account_ref, {}).values())[:limit]
        return [
            ConversationSummary(
                conversation_id=r["conversation_id"],
                buyer_name=r.get("buyer_name"),
                item_id=r.get("item_id"),
                subject=r.get("subject"),
                unread=r.get("unread", 0),
                last_message_at=_parse_dt(r.get("last_message_at")),
                raw=r,
            )
            for r in raws
        ]

    def get_conversation_messages(self, account_ref: str, conversation_id: str) -> list[ChatMessage]:
        with self._lock:
            raws = self._messages.get(account_ref, {}).get(conversation_id)
        if raws is None:
            raise NotFoundError(f"La conversacion '{conversation_id}' no existe.")
        return [
            ChatMessage(
                message_id=r["message_id"],
                conversation_id=r["conversation_id"],
                direction=r["direction"],
                body=r["body"],
                sent_at=_parse_dt(r.get("sent_at")),
                raw=r,
            )
            for r in raws
        ]

    def send_message(self, account_ref: str, conversation_id: str, body: str) -> OperationResult:
        self._check_triggers(body)
        if not body.strip():
            raise ValidationRejectedError("El mensaje no puede estar vacío.")
        with self._lock:
            conversations = self._messages.setdefault(account_ref, {})
            if conversation_id not in conversations:
                raise NotFoundError(f"La conversacion '{conversation_id}' no existe.")
            message_id = self._next_id("msg")
            conversations[conversation_id].append(
                {
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "direction": "out",
                    "body": body,
                    "sent_at": _now().isoformat(),
                }
            )
            conv = self._conversations.get(account_ref, {}).get(conversation_id)
            if conv:
                conv["unread"] = 0
                conv["last_message_at"] = _now().isoformat()
            self._save()
        return OperationResult(success=True, message="Mensaje enviado (DEMO, no ha salido de LOT Bot).", data={"message_id": message_id})

    # ------------------------------------------------------------------
    def get_market_data(self, account_ref: str, query: str, limit: int = 50) -> list[MarketDataPoint]:
        """Datos de mercado simulados, coherentes con el catalogo DEMO."""
        self._check_triggers(query)
        rng = random.Random(f"{account_ref}:{query}")
        points: list[MarketDataPoint] = []
        for size, base in _BASE_PRICES.items():
            if query and size not in query and query.lower() not in "canape canapes":
                continue
            for n in range(min(limit, 6)):
                points.append(
                    MarketDataPoint(
                        title=f"Canape {size} {_COLORS[n % len(_COLORS)]}",
                        price=round(base * rng.uniform(0.82, 1.22), 2),
                        currency="EUR",
                        category="Hogar y jardin",
                        source="DEMO",
                    )
                )
        return points[:limit]


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
