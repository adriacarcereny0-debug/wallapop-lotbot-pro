"""AuthorizedWallapopService: acceso REAL a Wallapop.

Este servicio NO contiene ninguna URL ni endpoint de Wallapop y NO presupone
ningun mecanismo de autenticacion. Todo se lee del perfil de acceso autorizado:

  * QUE se puede llamar  -> `access_profile.transport` (operaciones declaradas)
  * COMO se autentica    -> `AuthCredential` que entrega `AccountManager`

El servicio pide la credencial de la cuenta y aplica sus cabeceras y cookies.
Le da igual si vienen de OAuth, de una sesion autorizada o de una credencial
delegada: es el mecanismo quien lo decide.

Si una operacion no figura en el perfil, se lanza
`NotAvailableWithCurrentAccessError` y la aplicacion lo muestra tal cual.
Nunca se inventa un endpoint ni se simula un resultado.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from lot_bot.wallapop.auth.base import AuthCredential, AuthKind
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
from lot_bot.wallapop.endpoint_map import EndpointMap, Operation
from lot_bot.wallapop.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    NetworkError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    ValidationRejectedError,
    WallapopError,
)
from lot_bot.wallapop.service import WallapopService

logger = logging.getLogger(__name__)

#: Funcion que devuelve la credencial vigente de una cuenta (renovandola si
#: hace falta). La proporciona `AccountManager`; este servicio nunca accede
#: directamente a la base de datos ni descifra nada por su cuenta.
CredentialProvider = Callable[[str], AuthCredential]

#: Nombre anterior, mantenido por compatibilidad.
TokenProvider = CredentialProvider


class AuthorizedWallapopService(WallapopService):
    """Cliente HTTP generico gobernado por el perfil de acceso autorizado."""

    backend_name = "Wallapop (acceso autorizado)"
    is_mock = False

    def __init__(
        self,
        endpoint_map: EndpointMap,
        credential_provider: CredentialProvider,
        client: httpx.Client | None = None,
        auth_method_name: str = "",
    ) -> None:
        if not endpoint_map.base_url:
            raise ConfigurationError(
                "El perfil de acceso no define 'api.base_url'.",
                user_message=(
                    "El acceso a Wallapop no está configurado: falta la dirección base "
                    "en el perfil de acceso autorizado."
                ),
            )
        self.map = endpoint_map
        self._credential_provider = credential_provider
        self._client = client or httpx.Client(timeout=endpoint_map.timeout_seconds)
        self.auth_method_name = auth_method_name
        if auth_method_name:
            self.backend_name = f"Wallapop · {auth_method_name}"

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    def capabilities(self) -> set[Capability]:
        return self.map.capabilities()

    # ------------------------------------------------------------------
    # Motor de peticiones
    # ------------------------------------------------------------------
    def _credential(self, account_ref: str) -> AuthCredential:
        credential = self._credential_provider(account_ref)
        if credential is None or credential.is_empty:
            raise AuthenticationError(
                f"La cuenta '{account_ref}' no tiene una sesión válida.",
                user_message=(
                    "Esta cuenta no está conectada o su sesión ha caducado. "
                    "Vuelve a autenticarla desde Cuentas Wallapop."
                ),
            )
        if credential.kind is AuthKind.DEMO:
            # Blindaje: una credencial DEMO jamas debe salir a la red.
            raise AuthenticationError(
                f"La cuenta '{account_ref}' es de demostración y no puede usarse "
                f"contra Wallapop real.",
                user_message=(
                    "Esta es una cuenta de demostración. Conéctala con el mecanismo "
                    "autorizado para operar con Wallapop real."
                ),
            )
        return credential

    def _headers(self, credential: AuthCredential, operation: Operation) -> dict[str, str]:
        """Cabeceras de la peticion, con la credencial ya aplicada."""
        headers = {"Accept": "application/json"}
        headers.update(self.map.default_headers)
        headers.update(operation.headers)
        # El mecanismo de autenticacion decide en que cabeceras viaja.
        headers.update(credential.headers)
        return headers

    @staticmethod
    def _render(template: Any, params: dict[str, Any]) -> Any:
        """Sustituye marcadores `{campo}` en valores de query/body."""
        if isinstance(template, str):
            if template.startswith("{") and template.endswith("}") and template.count("{") == 1:
                return params.get(template[1:-1])
            try:
                return template.format(**params)
            except (KeyError, IndexError):
                return template
        if isinstance(template, dict):
            return {k: ConnectWallapopService._render(v, params) for k, v in template.items()}
        if isinstance(template, list):
            return [ConnectWallapopService._render(v, params) for v in template]
        return template

    def _call(
        self,
        capability: Capability,
        account_ref: str,
        params: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> Any:
        self.require(capability)
        operation = self.map.get(capability)
        assert operation is not None  # garantizado por require()
        params = params or {}

        url = f"{self.map.base_url}{operation.render_path(params)}"
        query = {k: v for k, v in self._render(operation.query, params).items() if v is not None}
        body: dict[str, Any] | None = None
        if operation.method in {"POST", "PUT", "PATCH", "DELETE"}:
            body = {k: v for k, v in self._render(operation.body, params).items() if v is not None}
            if extra_body:
                body.update(extra_body)
            if not body:
                body = extra_body or None

        credential = self._credential(account_ref)
        # Se registra la operacion y la cuenta, NUNCA la credencial.
        logger.info(
            "Wallapop %s %s (cuenta=%s, operacion=%s)",
            operation.method,
            operation.path,
            account_ref,
            capability.value,
        )
        try:
            response = self._client.request(
                operation.method,
                url,
                params=query or None,
                json=body,
                headers=self._headers(credential, operation),
                cookies=credential.cookies or None,
            )
        except httpx.TimeoutException as exc:
            raise NetworkError(f"Tiempo de espera agotado en {capability.value}.") from exc
        except httpx.HTTPError as exc:
            raise NetworkError(
                f"Error de red en {capability.value}: {type(exc).__name__}"
            ) from exc

        return self._handle_response(response, capability)

    def _handle_response(self, response: httpx.Response, capability: Capability) -> Any:
        status = response.status_code
        if status < 300:
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                return {"raw_text": response.text}

        detail = self._safe_detail(response)
        if status in (401,):
            raise AuthenticationError(f"{capability.value}: 401 {detail}")
        if status in (403,):
            raise AuthorizationError(f"{capability.value}: 403 {detail}")
        if status == 404:
            raise NotFoundError(f"{capability.value}: 404 {detail}")
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                f"{capability.value}: 429 {detail}",
                retry_after=int(retry_after) if (retry_after or "").isdigit() else None,
            )
        if status in (400, 409, 422):
            raise ValidationRejectedError(f"{capability.value}: {status} {detail}")
        if status >= 500:
            raise ServiceUnavailableError(f"{capability.value}: {status} {detail}")
        raise WallapopError(f"{capability.value}: {status} {detail}")

    @staticmethod
    def _safe_detail(response: httpx.Response) -> str:
        """Extrae el motivo del error sin volcar cuerpos enormes ni secretos."""
        try:
            payload = response.json()
        except ValueError:
            return response.text[:300]
        if isinstance(payload, dict):
            for key in ("message", "error_description", "error", "detail", "title"):
                value = payload.get(key)
                if isinstance(value, str):
                    return value[:300]
        return str(payload)[:300]

    # ------------------------------------------------------------------
    # Conversores
    # ------------------------------------------------------------------
    def _map_items(self, capability: Capability, payload: Any) -> list[Item]:
        operation = self.map.get(capability)
        assert operation is not None
        mapping = operation.response
        return [
            self._build_item(mapping.apply(raw), raw)
            for raw in mapping.extract_collection(payload)
        ]

    @staticmethod
    def _build_item(fields: dict[str, Any], raw: Any) -> Item:
        images_raw = fields.get("images") or []
        images: list[ItemImage] = []
        if isinstance(images_raw, list):
            for index, entry in enumerate(images_raw):
                if isinstance(entry, str):
                    images.append(ItemImage(url=entry, position=index))
                elif isinstance(entry, dict):
                    url = entry.get("url") or entry.get("href") or ""
                    if url:
                        images.append(
                            ItemImage(url=url, remote_id=entry.get("id"), position=index)
                        )
        return Item(
            item_id=str(fields.get("item_id") or ""),
            title=str(fields.get("title") or ""),
            description=str(fields.get("description") or ""),
            price=_as_float(fields.get("price")),
            currency=str(fields.get("currency") or "EUR"),
            category=_as_str_or_none(fields.get("category")),
            condition=_as_str_or_none(fields.get("condition")),
            status=str(fields.get("status") or "active"),
            attributes=fields.get("attributes") if isinstance(fields.get("attributes"), dict) else {},
            images=images,
            views=int(_as_float(fields.get("views")) or 0),
            favorites=int(_as_float(fields.get("favorites")) or 0),
            created_at=_as_datetime(fields.get("created_at")),
            updated_at=_as_datetime(fields.get("updated_at")),
            raw=raw if isinstance(raw, dict) else {},
        )

    # ------------------------------------------------------------------
    # Cuenta
    # ------------------------------------------------------------------
    def get_account_profile(self, account_ref: str) -> AccountProfile:
        payload = self._call(Capability.ACCOUNT_PROFILE, account_ref)
        operation = self.map.get(Capability.ACCOUNT_PROFILE)
        assert operation is not None
        source = payload
        if operation.response.collection_path:
            collection = operation.response.extract_collection(payload)
            source = collection[0] if collection else {}
        fields = operation.response.apply(source)
        return AccountProfile(
            user_id=str(fields.get("user_id") or ""),
            display_name=str(fields.get("display_name") or fields.get("login") or account_ref),
            login=_as_str_or_none(fields.get("login")),
            active_items=int(_as_float(fields.get("active_items")) or 0) or None,
            raw=source if isinstance(source, dict) else {},
        )

    def check_connection(self, account_ref: str) -> bool:
        probe = (
            Capability.ACCOUNT_PROFILE
            if self.supports(Capability.ACCOUNT_PROFILE)
            else Capability.LIST_ITEMS
        )
        try:
            self._call(probe, account_ref, {"limit": 1, "offset": 0})
            return True
        except (AuthenticationError, AuthorizationError):
            return False

    # ------------------------------------------------------------------
    # Anuncios
    # ------------------------------------------------------------------
    def list_items(self, account_ref: str, limit: int = 100, offset: int = 0) -> list[Item]:
        payload = self._call(Capability.LIST_ITEMS, account_ref, {"limit": limit, "offset": offset})
        return self._map_items(Capability.LIST_ITEMS, payload)

    def get_item(self, account_ref: str, item_id: str) -> Item:
        payload = self._call(Capability.GET_ITEM, account_ref, {"item_id": item_id})
        operation = self.map.get(Capability.GET_ITEM)
        assert operation is not None
        source = payload
        if operation.response.collection_path:
            collection = operation.response.extract_collection(payload)
            if not collection:
                raise NotFoundError(f"El anuncio '{item_id}' no existe.")
            source = collection[0]
        return self._build_item(operation.response.apply(source), source)

    def search_items(self, account_ref: str, query: ItemSearchQuery) -> list[Item]:
        if self.supports(Capability.SEARCH_ITEMS):
            payload = self._call(
                Capability.SEARCH_ITEMS,
                account_ref,
                {
                    "text": query.text or "",
                    "min_price": query.min_price,
                    "max_price": query.max_price,
                    "category": query.category,
                    "status": query.status,
                    "limit": query.limit,
                    "offset": query.offset,
                },
            )
            items = self._map_items(Capability.SEARCH_ITEMS, payload)
        else:
            # Sin endpoint de busqueda, filtramos en local sobre el listado.
            self.require(Capability.LIST_ITEMS)
            items = self.list_items(account_ref, limit=max(query.limit, 200))
        return _filter_items_locally(items, query)

    def create_item(self, account_ref: str, draft: ItemDraft) -> OperationResult:
        payload = self._call(
            Capability.CREATE_ITEM,
            account_ref,
            {
                "title": draft.title,
                "description": draft.description,
                "price": draft.price,
                "currency": draft.currency,
                "category": draft.category,
                "condition": draft.condition,
                "images": draft.image_urls,
            },
            extra_body=draft.to_payload() if not self._has_body_template(Capability.CREATE_ITEM) else None,
        )
        operation = self.map.get(Capability.CREATE_ITEM)
        assert operation is not None
        fields = operation.response.apply(payload if isinstance(payload, dict) else {})
        return OperationResult(
            success=True,
            message="Anuncio publicado en Wallapop.",
            item_id=_as_str_or_none(fields.get("item_id")),
            data=fields,
        )

    def update_item(
        self,
        account_ref: str,
        item_id: str,
        changes: dict[str, Any],
        item_url: str | None = None,
    ) -> OperationResult:
        params: dict[str, Any] = {"item_id": item_id}
        params.update(changes)
        payload = self._call(
            Capability.UPDATE_ITEM,
            account_ref,
            params,
            extra_body=dict(changes) if not self._has_body_template(Capability.UPDATE_ITEM) else None,
        )
        return OperationResult(
            success=True,
            message=f"Anuncio actualizado: {', '.join(changes) or 'sin cambios'}.",
            item_id=item_id,
            data=payload if isinstance(payload, dict) else {},
        )

    def delete_item(
        self, account_ref: str, item_id: str, item_url: str | None = None
    ) -> OperationResult:
        self._call(Capability.DELETE_ITEM, account_ref, {"item_id": item_id})
        return OperationResult(success=True, message="Anuncio eliminado.", item_id=item_id)

    def update_item_price(
        self, account_ref: str, item_id: str, price: float, item_url: str | None = None
    ) -> OperationResult:
        if self.supports(Capability.UPDATE_ITEM_PRICE):
            self._call(
                Capability.UPDATE_ITEM_PRICE,
                account_ref,
                {"item_id": item_id, "price": price},
                extra_body={"price": price}
                if not self._has_body_template(Capability.UPDATE_ITEM_PRICE)
                else None,
            )
            return OperationResult(
                success=True, message=f"Precio actualizado a {price:.2f} EUR.", item_id=item_id
            )
        return self.update_item(account_ref, item_id, {"price": price})

    def update_item_images(self, account_ref: str, item_id: str, image_urls: list[str]) -> OperationResult:
        self._call(
            Capability.UPDATE_ITEM_IMAGES,
            account_ref,
            {"item_id": item_id, "images": image_urls},
            extra_body={"images": image_urls}
            if not self._has_body_template(Capability.UPDATE_ITEM_IMAGES)
            else None,
        )
        return OperationResult(
            success=True, message=f"{len(image_urls)} fotografias actualizadas.", item_id=item_id
        )

    def upload_image(self, account_ref: str, image_path: str) -> str:
        self.require(Capability.UPLOAD_IMAGE)
        operation = self.map.get(Capability.UPLOAD_IMAGE)
        assert operation is not None
        url = f"{self.map.base_url}{operation.render_path({'image_path': image_path})}"
        try:
            credential = self._credential(account_ref)
            with open(image_path, "rb") as handle:
                response = self._client.request(
                    operation.method,
                    url,
                    files={"file": (image_path.split("/")[-1], handle)},
                    headers=self._headers(credential, operation),
                    cookies=credential.cookies or None,
                )
        except OSError as exc:
            raise WallapopError(f"No se puede leer la imagen '{image_path}': {exc}") from exc
        payload = self._handle_response(response, Capability.UPLOAD_IMAGE)
        fields = operation.response.apply(payload if isinstance(payload, dict) else {})
        remote = fields.get("url") or fields.get("image_url") or fields.get("id")
        if not remote:
            raise ValidationRejectedError(
                "Wallapop no ha devuelto la URL de la imagen subida. "
                "Revisa el mapeo 'response.fields' de la operacion upload_image."
            )
        return str(remote)

    # ------------------------------------------------------------------
    def list_categories(self, account_ref: str) -> list[dict[str, Any]]:
        payload = self._call(Capability.LIST_CATEGORIES, account_ref)
        operation = self.map.get(Capability.LIST_CATEGORIES)
        assert operation is not None
        return [
            operation.response.apply(raw) or (raw if isinstance(raw, dict) else {})
            for raw in operation.response.extract_collection(payload)
        ]

    # ------------------------------------------------------------------
    def list_conversations(self, account_ref: str, limit: int = 50) -> list[ConversationSummary]:
        payload = self._call(Capability.LIST_CONVERSATIONS, account_ref, {"limit": limit})
        operation = self.map.get(Capability.LIST_CONVERSATIONS)
        assert operation is not None
        result: list[ConversationSummary] = []
        for raw in operation.response.extract_collection(payload):
            fields = operation.response.apply(raw)
            result.append(
                ConversationSummary(
                    conversation_id=str(fields.get("conversation_id") or ""),
                    buyer_name=_as_str_or_none(fields.get("buyer_name")),
                    item_id=_as_str_or_none(fields.get("item_id")),
                    subject=_as_str_or_none(fields.get("subject")),
                    unread=int(_as_float(fields.get("unread")) or 0),
                    last_message_at=_as_datetime(fields.get("last_message_at")),
                    raw=raw if isinstance(raw, dict) else {},
                )
            )
        return result

    def get_conversation_messages(self, account_ref: str, conversation_id: str) -> list[ChatMessage]:
        payload = self._call(
            Capability.GET_CONVERSATION, account_ref, {"conversation_id": conversation_id}
        )
        operation = self.map.get(Capability.GET_CONVERSATION)
        assert operation is not None
        messages: list[ChatMessage] = []
        for raw in operation.response.extract_collection(payload):
            fields = operation.response.apply(raw)
            messages.append(
                ChatMessage(
                    message_id=str(fields.get("message_id") or ""),
                    conversation_id=str(fields.get("conversation_id") or conversation_id),
                    direction=str(fields.get("direction") or "in"),
                    body=str(fields.get("body") or ""),
                    sent_at=_as_datetime(fields.get("sent_at")),
                    raw=raw if isinstance(raw, dict) else {},
                )
            )
        return messages

    def send_message(self, account_ref: str, conversation_id: str, body: str) -> OperationResult:
        if not body.strip():
            raise ValidationRejectedError("El mensaje no puede estar vacio.")
        payload = self._call(
            Capability.SEND_MESSAGE,
            account_ref,
            {"conversation_id": conversation_id, "body": body, "text": body},
            extra_body={"text": body} if not self._has_body_template(Capability.SEND_MESSAGE) else None,
        )
        return OperationResult(
            success=True,
            message="Mensaje enviado.",
            data=payload if isinstance(payload, dict) else {},
        )

    # ------------------------------------------------------------------
    def get_market_data(self, account_ref: str, query: str, limit: int = 50) -> list[MarketDataPoint]:
        payload = self._call(Capability.MARKET_DATA, account_ref, {"query": query, "limit": limit})
        operation = self.map.get(Capability.MARKET_DATA)
        assert operation is not None
        points: list[MarketDataPoint] = []
        for raw in operation.response.extract_collection(payload):
            fields = operation.response.apply(raw)
            price = _as_float(fields.get("price"))
            if price is None:
                continue
            points.append(
                MarketDataPoint(
                    title=str(fields.get("title") or ""),
                    price=price,
                    currency=str(fields.get("currency") or "EUR"),
                    category=_as_str_or_none(fields.get("category")),
                    source="wallapop",
                    raw=raw if isinstance(raw, dict) else {},
                )
            )
        return points

    # ------------------------------------------------------------------
    def _has_body_template(self, capability: Capability) -> bool:
        operation = self.map.get(capability)
        return bool(operation and operation.body)


# ---------------------------------------------------------------------------
# Utilidades de conversion
# ---------------------------------------------------------------------------
def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ".").replace("EUR", "").strip())
    except ValueError:
        return None


def _as_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:

            seconds = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _filter_items_locally(items: list[Item], query: ItemSearchQuery) -> list[Item]:
    """Aplica los filtros que la API no haya podido aplicar."""
    results: list[Item] = []
    for item in items:
        if query.text:
            haystack = f"{item.title} {item.description} {item.attributes}".lower()
            if query.text.lower() not in haystack:
                continue
        if query.min_price is not None and (item.price is None or item.price < query.min_price):
            continue
        if query.max_price is not None and (item.price is None or item.price > query.max_price):
            continue
        if query.category and item.category != query.category:
            continue
        if query.status and item.status != query.status:
            continue
        if query.attributes:
            attrs = {k.lower(): str(v).lower() for k, v in item.attributes.items()}
            if any(
                attrs.get(key.lower()) != str(value).lower()
                for key, value in query.attributes.items()
            ):
                continue
        results.append(item)
    return results[query.offset : query.offset + query.limit]


#: Nombre anterior del servicio, mantenido para no romper importaciones.
ConnectWallapopService = AuthorizedWallapopService
