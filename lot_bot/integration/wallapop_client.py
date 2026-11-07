"""WallapopClient: comunicación directa con Wallapop, sin navegador.

Es la única pieza del motor de publicación que habla con Wallapop. Por debajo
usa el `WallapopService` que elige `wallapop/factory.py`:

    demo       -> MockWallapopService            (datos simulados)
    perfil     -> AuthorizedWallapopService      (credencial provisional autorizada)
    oficial    -> AuthorizedWallapopService      (API oficial de Wallapop)

PREPARADO PARA LA API OFICIAL
-----------------------------
El proveedor provisional y la API oficial usan EXACTAMENTE el mismo código:
la diferencia está en el perfil YAML (qué URL, qué operaciones, cómo se
autentica, qué límites). Cuando Wallapop conceda la API oficial basta con
rellenar `config/official_api.local.yaml` y elegir «API oficial» en
«Conectar Wallapop». El motor, la cola, las fotos y la interfaz no cambian.

Si algún día la API oficial necesitara algo que el perfil no puede expresar,
se implementa una subclase de `WallapopService` y se registra en la
factoría: este cliente y todo lo que hay encima siguen igual.

Lo que añade este cliente sobre el servicio:

* límite de peticiones por minuto (`limits.max_requests_per_minute`),
* clasificación de errores en categorías que el motor sabe tratar,
* clave de idempotencia al crear anuncios (si el servicio la admite),
* búsqueda de un anuncio ya publicado (para recuperar interrupciones),
* descripción del procedimiento de conexión para la pantalla.

No contiene ninguna URL de Wallapop, no lee cookies del navegador ni usa
tokens internos: solo lo que el perfil autorizado declara.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from lot_bot.core.text import fold as _normalize_text
from lot_bot.wallapop.access_profile import UsageLimits
from lot_bot.wallapop.auth.base import AuthKind
from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.dto import AccountProfile, Item, ItemDraft, ItemSearchQuery, OperationResult
from lot_bot.wallapop.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    NetworkError,
    NotAvailableWithCurrentAccessError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    ValidationRejectedError,
    VerificationRequiredError,
)

logger = logging.getLogger(__name__)

PROVIDER_LABELS = {
    "demo": "Modo demostración (simulado)",
    "perfil": "Conexión directa · credencial de integración provisional",
    "oficial": "Conexión directa · API oficial de Wallapop",
    "navegador": "Navegador (sistema anterior)",
}


class ErrorKind(str, enum.Enum):
    """Qué tipo de problema es, desde el punto de vista del motor."""

    #: Credencial ausente, caducada o rechazada: hay que reconectar.
    AUTH = "auth"
    #: Permiso insuficiente o configuración incompleta: lo arregla una persona.
    CONFIG = "config"
    #: Wallapop limita las peticiones: esperar sin perder nada.
    RATE_LIMIT = "rate_limit"
    #: La petición no salió (sin conexión): reintentar es seguro.
    NETWORK_SAFE = "network_safe"
    #: La petición pudo llegar a Wallapop sin respuesta: comprobar antes de repetir.
    NETWORK_UNCERTAIN = "network_uncertain"
    #: Wallapop no está disponible (5xx): reintentar más tarde.
    UNAVAILABLE = "unavailable"
    #: Datos rechazados (400/422): reintentar no lo arregla.
    REJECTED = "rejected"
    #: Wallapop pide una verificación que debe hacer el usuario.
    VERIFICATION = "verification"
    UNKNOWN = "unknown"


def classify_error(exc: BaseException) -> ErrorKind:
    """Traduce cualquier excepción a una categoría que el motor sabe tratar."""
    if isinstance(exc, AuthenticationError):
        return ErrorKind.AUTH
    if isinstance(exc, (AuthorizationError, ConfigurationError, NotAvailableWithCurrentAccessError)):
        return ErrorKind.CONFIG
    if isinstance(exc, RateLimitError):
        return ErrorKind.RATE_LIMIT
    if isinstance(exc, NetworkError):
        return ErrorKind.NETWORK_UNCERTAIN if exc.request_sent else ErrorKind.NETWORK_SAFE
    if isinstance(exc, ServiceUnavailableError):
        return ErrorKind.UNAVAILABLE
    if isinstance(exc, (ValidationRejectedError, NotFoundError)):
        return ErrorKind.REJECTED
    if isinstance(exc, VerificationRequiredError):
        return ErrorKind.VERIFICATION
    return ErrorKind.UNKNOWN


@dataclass(slots=True)
class ConnectionProcedure:
    """Qué tiene que hacer el cliente para conectar su cuenta. Sin secretos."""

    method: str
    title: str
    steps: list[str] = field(default_factory=list)
    #: Página documentada por Wallapop donde se obtiene la credencial.
    portal_url: str = ""
    documentation_url: str = ""
    #: True si la pantalla debe mostrar el campo «Credencial de integración».
    needs_credential: bool = False
    #: True si «Iniciar conexión» lanza un flujo en el navegador del sistema
    #: (OAuth o inicio de sesión autorizado) y espera el retorno.
    opens_authorization_flow: bool = False
    ready: bool = True
    missing: list[str] = field(default_factory=list)
    can_renew: bool = False


class RequestRateLimiter:
    """Ventana deslizante de 60 s: nunca más de N peticiones por minuto."""

    def __init__(
        self,
        per_minute: int | None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Espera si hace falta. Devuelve los segundos esperados."""
        if not self.per_minute:
            return 0.0
        waited = 0.0
        with self._lock:
            while True:
                now = self._clock()
                while self._calls and now - self._calls[0] >= 60.0:
                    self._calls.popleft()
                if len(self._calls) < self.per_minute:
                    self._calls.append(now)
                    return waited
                pause = 60.0 - (now - self._calls[0]) + 0.01
                self._sleep(pause)
                waited += pause


class WallapopClient:
    """Fachada del servicio activo para el motor de publicación directa."""

    def __init__(
        self,
        backend_getter: Callable[[], Any],
        *,
        limiter_clock: Callable[[], float] = time.monotonic,
        limiter_sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        #: Devuelve el `WallapopBackend` vigente (cambia al elegir otro modo).
        self._backend_getter = backend_getter
        self._limiter_clock = limiter_clock
        self._limiter_sleep = limiter_sleep
        self._limiter: RequestRateLimiter | None = None
        self._limiter_key: Any = None

    # ------------------------------------------------------------------
    # Proveedor activo
    # ------------------------------------------------------------------
    @property
    def backend(self) -> Any:
        return self._backend_getter()

    @property
    def service(self) -> Any:
        return self.backend.service

    @property
    def provider(self) -> str:
        backend = self.backend
        return "demo" if backend.demo else getattr(backend, "provider", "perfil")

    @property
    def provider_label(self) -> str:
        return PROVIDER_LABELS.get(self.provider, self.provider)

    @property
    def is_demo(self) -> bool:
        return bool(self.backend.demo)

    @property
    def is_direct(self) -> bool:
        """Conexión directa (provisional u oficial): sin navegador."""
        return bool(getattr(self.backend, "is_direct", False))

    @property
    def uses_browser(self) -> bool:
        return bool(getattr(self.backend, "uses_browser", False))

    @property
    def can_publish_directly(self) -> bool:
        """El motor directo solo trabaja en DEMO o con conexión directa."""
        return self.is_demo or self.is_direct

    @property
    def limits(self) -> UsageLimits:
        profile = getattr(self.backend, "profile", None)
        return getattr(profile, "limits", None) or UsageLimits()

    def capabilities(self) -> set[Capability]:
        return set(self.service.capabilities())

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities()

    def missing_configuration(self) -> list[str]:
        return list(getattr(self.backend, "missing", []) or [])

    # ------------------------------------------------------------------
    # Procedimiento de conexión
    # ------------------------------------------------------------------
    def connection_procedure(self) -> ConnectionProcedure:
        backend = self.backend
        method = backend.auth_method
        kind = method.kind
        missing = [
            f"{r.label} — {r.description}" for r in method.missing_requirements()
        ]
        if backend.demo:
            return ConnectionProcedure(
                method=AuthKind.DEMO.value,
                title="Modo demostración",
                steps=[
                    "Pulsa «Iniciar conexión»: la cuenta se conecta con datos simulados.",
                    "Nada de lo que hagas se envía a Wallapop.",
                ],
                ready=True,
                missing=list(backend.missing),
            )
        if kind is AuthKind.DELEGATED_CREDENTIAL:
            config = method.config
            steps = [
                "Pulsa «Iniciar conexión»: se abre la página que Wallapop ha indicado "
                "para obtener la credencial de integración de tu cuenta."
                if config.portal_url
                else "Obtén la credencial de integración que Wallapop ha emitido para tu cuenta.",
                "Copia la credencial y pégala en el campo «Credencial de integración».",
                "Pulsa «Validar conexión». LOT Bot la guarda cifrada y comprueba que funciona.",
            ]
            if config.instructions:
                steps.insert(0, config.instructions)
            return ConnectionProcedure(
                method=kind.value,
                title="Credencial de integración autorizada por Wallapop",
                steps=steps,
                portal_url=config.portal_url,
                documentation_url=config.documentation_url,
                needs_credential=True,
                ready=method.is_ready,
                missing=missing,
                can_renew=False,
            )
        if kind is AuthKind.OAUTH:
            return ConnectionProcedure(
                method=kind.value,
                title="Autorización oficial de Wallapop (OAuth)",
                steps=[
                    "Pulsa «Iniciar conexión»: se abre Wallapop en tu navegador habitual.",
                    "Inicia sesión allí y acepta los permisos que pide LOT Bot.",
                    "Vuelve a LOT Bot: la conexión se valida sola.",
                ],
                opens_authorization_flow=True,
                ready=method.is_ready,
                missing=missing,
                can_renew=True,
            )
        if kind is AuthKind.SESSION_HANDOFF:
            config = method.config
            steps = [
                "Pulsa «Iniciar conexión»: se abre la página de acceso que Wallapop ha autorizado.",
                "Inicia sesión allí con todos los pasos que pida Wallapop.",
                "Al terminar, Wallapop devuelve la sesión a LOT Bot y se valida sola.",
            ]
            if config.instructions:
                steps.insert(0, config.instructions)
            return ConnectionProcedure(
                method=kind.value,
                title="Inicio de sesión autorizado",
                steps=steps,
                opens_authorization_flow=True,
                ready=method.is_ready,
                missing=missing,
            )
        if kind is AuthKind.BROWSER_SESSION:
            return ConnectionProcedure(
                method=kind.value,
                title="Navegador (sistema anterior)",
                steps=[
                    "Este modo controla un navegador. La publicación masiva directa no lo usa.",
                    "Elige «Credencial provisional» o «API oficial» arriba para publicar sin navegador.",
                ],
                ready=False,
                missing=["La publicación directa no funciona en modo navegador."],
            )
        return ConnectionProcedure(
            method=kind.value, title=method.describe(), ready=method.is_ready, missing=missing
        )

    # ------------------------------------------------------------------
    # Peticiones
    # ------------------------------------------------------------------
    def _throttle(self) -> None:
        per_minute = self.limits.max_requests_per_minute
        key = (id(self.service), per_minute)
        if self._limiter is None or self._limiter_key != key:
            self._limiter = RequestRateLimiter(
                per_minute, clock=self._limiter_clock, sleep=self._limiter_sleep
            )
            self._limiter_key = key
        self._limiter.acquire()

    def verify_account(self, account_ref: str) -> AccountProfile | None:
        """Comprueba de verdad que la cuenta responde con su credencial.

        Devuelve el perfil si la operación existe; None si solo se pudo
        comprobar con un listado. Lanza el error de Wallapop si falla.
        """
        self._throttle()
        service = self.service
        if service.supports(Capability.ACCOUNT_PROFILE):
            return service.get_account_profile(account_ref)
        if service.supports(Capability.LIST_ITEMS):
            service.list_items(account_ref, limit=1)
            return None
        raise NotAvailableWithCurrentAccessError(
            Capability.ACCOUNT_PROFILE.value,
            "No hay ninguna operación con la que comprobar la conexión (account_profile o list_items).",
        )

    def check(self, account_ref: str) -> bool:
        """Comprobación ligera: True si la sesión sigue activa."""
        self._throttle()
        return bool(self.service.check_connection(account_ref))

    def upload_image(self, account_ref: str, path: str) -> str:
        self._throttle()
        return self.service.upload_image(account_ref, path)

    def create_listing(
        self, account_ref: str, draft: ItemDraft, idempotency_key: str | None = None
    ) -> OperationResult:
        self._throttle()
        service = self.service
        service.require(Capability.CREATE_ITEM)
        if idempotency_key and getattr(service, "idempotency_header", ""):
            return service.create_item(account_ref, draft, idempotency_key=idempotency_key)
        return service.create_item(account_ref, draft)

    @property
    def supports_idempotency(self) -> bool:
        return bool(getattr(self.service, "idempotency_header", ""))

    @property
    def can_search_published(self) -> bool:
        caps = self.capabilities()
        return Capability.LIST_ITEMS in caps or Capability.SEARCH_ITEMS in caps

    def find_published(self, account_ref: str, title: str, price: float | None) -> Item | None:
        """Busca en Wallapop un anuncio de esta cuenta con ese título (y precio).

        Sirve para saber si una publicación interrumpida llegó a crearse.
        Lanza NotAvailableWithCurrentAccessError si no se puede consultar.
        """
        self._throttle()
        service = self.service
        if service.supports(Capability.SEARCH_ITEMS):
            items = service.search_items(account_ref, ItemSearchQuery(text=title, limit=50))
        elif service.supports(Capability.LIST_ITEMS):
            items = service.list_items(account_ref, limit=200)
        else:
            raise NotAvailableWithCurrentAccessError(
                Capability.LIST_ITEMS.value,
                "Sin listar anuncios no se puede comprobar si una publicación llegó a crearse.",
            )
        wanted = _normalize_text(title)
        for item in items:
            if _normalize_text(item.title) != wanted:
                continue
            if price is not None and item.price is not None and abs(item.price - price) > 0.005:
                continue
            return item
        return None
