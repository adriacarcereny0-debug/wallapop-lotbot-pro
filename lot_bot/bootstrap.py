"""Arranque y ensamblado de LOT Bot.

`Application` es el contenedor de servicios: se construye una vez y se pasa a
la interfaz y al agente IA. Aqui, y solo aqui, se decide que implementacion de
`WallapopService` se usa.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from lot_bot import APP_NAME, __version__
from lot_bot.ai.agent import Agent
from lot_bot.ai.provider import AIProvider
from lot_bot.ai.tools import build_registry
from lot_bot.automation.scheduler import AutomationScheduler
from lot_bot.catalog.service import CatalogService
from lot_bot.config.api_keys import FLUX, ApiKeyStore
from lot_bot.config.paths import AppPaths, get_paths
from lot_bot.config.settings import Settings, get_settings
from lot_bot.core.audit import AuditService
from lot_bot.core.events import EventBus, get_event_bus
from lot_bot.database.engine import Database, get_database
from lot_bot.database.models import Setting
from lot_bot.images.generation import (
    DemoImageGenerator,
    FluxImageService,
    ImageGenerationService,
    ImageGenerator,
)
from lot_bot.images.service import ImageService
from lot_bot.logs.setup import configure_logging
from lot_bot.market.service import MarketService
from lot_bot.master_ad.service import MasterAdService
from lot_bot.messages.service import MessageService
from lot_bot.publishing.listings import ListingService
from lot_bot.publishing.queue import PublishQueue
from lot_bot.publishing.service import PublishingService
from lot_bot.stats import Optimizer, StatsAnalyzer, StatsService
from lot_bot.templates_engine.defaults import ensure_default_templates
from lot_bot.wallapop.account_manager import AccountManager
from lot_bot.wallapop.factory import WallapopBackend, build_backend
from lot_bot.wallapop.mock_service import DEMO_ACCOUNTS
from lot_bot.wallapop.service import WallapopService

logger = logging.getLogger(__name__)

BUSINESS_SETTINGS_KEY = "negocio"
INTEGRATION_SETTINGS_KEY = "integracion_wallapop"

#: Valores por defecto de la configuracion de negocio. Vacios a proposito:
#: los datos comerciales reales los introduce el cliente en Ajustes.
DEFAULT_BUSINESS_SETTINGS: dict[str, Any] = {
    "name": "",
    "whatsapp": "",
    "delivery": "",
    "assembly": "",
    "payment": "",
    "location": "",
    "precio_90": "",
    "precio_135": "",
    "precio_150": "",
}


@dataclass
class Application:
    """Contenedor de servicios de LOT Bot."""

    settings: Settings
    paths: AppPaths
    db: Database
    events: EventBus
    audit: AuditService
    accounts: AccountManager
    backend: WallapopBackend
    catalog: CatalogService
    images: ImageService
    listings: ListingService
    publishing: PublishingService
    messages: MessageService
    market: MarketService
    master_ads: MasterAdService
    automations: AutomationScheduler
    agent: Agent
    api_keys: ApiKeyStore = None  # type: ignore[assignment]
    image_generation: ImageGenerationService = None  # type: ignore[assignment]
    publish_queue: PublishQueue = None  # type: ignore[assignment]
    stats: StatsService = None  # type: ignore[assignment]
    analyzer: StatsAnalyzer = None  # type: ignore[assignment]
    optimizer: Optimizer = None  # type: ignore[assignment]
    business_settings: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def wallapop(self) -> WallapopService:
        return self.backend.service

    @property
    def demo_mode(self) -> bool:
        return self.backend.demo

    @property
    def backend_label(self) -> str:
        return self.backend.label

    @property
    def auth_method(self):
        """Mecanismo de acceso activo (DEMO, OAuth, sesión autorizada...)."""
        return self.backend.auth_method

    @property
    def access_profile(self):
        """Perfil de acceso autorizado en uso."""
        return self.backend.profile

    @property
    def missing_access_data(self) -> list[str]:
        """Qué falta exactamente para poder conectar con Wallapop real."""
        return list(self.backend.missing)

    @property
    def version(self) -> str:
        return __version__

    # ------------------------------------------------------------------
    def reload_business_settings(self) -> dict[str, Any]:
        self.business_settings = load_business_settings(self.db)
        self.publishing.set_business_settings(self.business_settings)
        return self.business_settings

    def save_business_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        merged = {**DEFAULT_BUSINESS_SETTINGS, **(self.business_settings or {}), **values}
        with self.db.session_scope() as session:
            row = session.get(Setting, BUSINESS_SETTINGS_KEY)
            if row is None:
                row = Setting(key=BUSINESS_SETTINGS_KEY, value=merged)
                session.add(row)
            else:
                row.value = merged
        self.business_settings = merged
        self.publishing.set_business_settings(merged)
        self.audit.record("Cambio de configuración de negocio", actor="usuario")
        return merged

    # ------------------------------------------------------------------
    # Integración con Wallapop elegida en Configuración
    # ------------------------------------------------------------------
    @property
    def integration_mode(self) -> str:
        return load_integration_mode(self.db)

    def set_integration_mode(self, mode: str) -> WallapopBackend:
        """«navegador» (sesión del usuario) o «demo». Se guarda en local."""
        if mode not in ("navegador", "demo"):
            raise ValueError(f"Modo de integración desconocido: {mode}")
        with self.db.session_scope() as session:
            row = session.get(Setting, INTEGRATION_SETTINGS_KEY)
            if row is None:
                session.add(Setting(key=INTEGRATION_SETTINGS_KEY, value={"modo": mode}))
            else:
                row.value = {"modo": mode}
        self.audit.record_success("Cambio de integración con Wallapop", detail=mode, actor="usuario")
        return self.rebuild_backend()

    @property
    def browser_auth(self):
        """Mecanismo de conexión por navegador, si está activo."""
        from lot_bot.wallapop.browser import BrowserSessionAuthMethod

        method = self.backend.auth_method
        return method if isinstance(method, BrowserSessionAuthMethod) else None

    def image_generator(self) -> ImageGenerator:
        return build_image_generator(self.backend.demo, self.api_keys)

    def refresh_image_generator(self) -> None:
        self.image_generation.set_generator(self.image_generator())

    def rebuild_backend(self, settings: Settings | None = None) -> WallapopBackend:
        """Vuelve a elegir el backend (tras cambiar credenciales o modo DEMO)."""
        if settings is not None:
            self.settings = settings
        old_close = getattr(self.backend.service, "shutdown", None)
        if callable(old_close):
            old_close()  # cierra los navegadores abiertos del backend anterior
        self.backend = build_backend(self.settings, self.accounts, self.integration_mode)
        self.accounts.set_auth_method(self.backend.auth_method)
        self.listings.set_backend(self.wallapop)
        self.publishing.set_backend(self.wallapop)
        self.messages.set_backend(self.wallapop)
        self.market.set_backend(self.wallapop)
        self.master_ads.set_backend(self.wallapop, self.backend.demo)
        self.audit.demo_mode = self.backend.demo
        if self.backend.demo:
            self.accounts.ensure_demo_accounts([(a["ref"], a["alias"]) for a in DEMO_ACCOUNTS])
        if self.image_generation is not None:
            self.refresh_image_generator()
        logger.info("Backend de Wallapop: %s (%s)", self.backend.label, self.backend.reason)
        return self.backend

    def set_ai_provider(self, provider: AIProvider) -> None:
        self.agent.set_provider(provider)

    def shutdown(self) -> None:
        try:
            if self.publish_queue is not None:
                self.publish_queue.shutdown()
            close_browsers = getattr(self.backend.service, "shutdown", None)
            if callable(close_browsers):
                close_browsers()
            self.automations.shutdown()
        finally:
            self.db.dispose()
        logger.info("%s cerrado correctamente.", APP_NAME)


# ---------------------------------------------------------------------------
def load_integration_mode(database: Database) -> str:
    with database.session_scope() as session:
        row = session.get(Setting, INTEGRATION_SETTINGS_KEY)
        return str((row.value or {}).get("modo", "")) if row else ""


def build_image_generator(demo: bool, api_keys: ApiKeyStore) -> ImageGenerator:
    """En DEMO, imágenes de prueba locales (sin créditos ni red). En real,
    FLUX.2 Pro con la clave guardada cifrada."""
    if demo:
        return DemoImageGenerator()
    return FluxImageService(lambda: api_keys.get(FLUX))


def load_business_settings(database: Database) -> dict[str, Any]:
    with database.session_scope() as session:
        row = session.get(Setting, BUSINESS_SETTINGS_KEY)
        if row is None:
            return dict(DEFAULT_BUSINESS_SETTINGS)
        return {**DEFAULT_BUSINESS_SETTINGS, **(row.value or {})}


def seed_business_from_env(app: Application) -> None:
    """Rellena los datos de negocio vacios con las variables de entorno."""
    updates: dict[str, Any] = {}
    if app.settings.business_whatsapp and not app.business_settings.get("whatsapp"):
        updates["whatsapp"] = app.settings.business_whatsapp
    if app.settings.business_name and not app.business_settings.get("name"):
        updates["name"] = app.settings.business_name
    if updates:
        app.save_business_settings(updates)


_application: Application | None = None


def current_application() -> Application | None:
    """Devuelve la aplicacion en marcha (util para pruebas y herramientas)."""
    return _application


def create_application(
    settings: Settings | None = None,
    database: Database | None = None,
    provider: AIProvider | None = None,
    start_scheduler: bool = True,
) -> Application:
    """Construye y conecta todos los servicios de LOT Bot."""
    global _application

    settings = settings or get_settings()
    paths = get_paths()
    configure_logging(settings.log_level, settings.describe_redactions())
    logger.info("Iniciando %s %s (entorno: %s)", APP_NAME, __version__, settings.environment)

    db = database or get_database()
    db.create_all()

    created = ensure_default_templates(db)
    if created:
        logger.info("Plantillas creadas: %s", ", ".join(created))

    events = get_event_bus()
    audit = AuditService(db)
    accounts = AccountManager(db)
    # En LOT Bot las cuentas no caducan: se deshace el estado «caducada» antiguo.
    accounts.restore_expired_accounts()

    backend = build_backend(settings, accounts, load_integration_mode(db))
    accounts.set_auth_method(backend.auth_method)
    logger.info("Backend de Wallapop: %s (%s)", backend.label, backend.reason)
    for item in backend.missing:
        logger.info("  Pendiente para el acceso real: %s", item)

    if backend.demo:
        accounts.ensure_demo_accounts([(a["ref"], a["alias"]) for a in DEMO_ACCOUNTS])

    catalog = CatalogService(db)
    images = ImageService(paths.images)
    listings = ListingService(db, backend.service)
    business = load_business_settings(db)
    publishing = PublishingService(db, backend.service, catalog, listings, audit, business)
    messages = MessageService(db, backend.service, audit)
    market = MarketService(backend.service, listings)
    audit.demo_mode = backend.demo
    master_ads = MasterAdService(
        db, backend.service, listings, audit, paths.images, demo_mode=backend.demo
    )
    # El anuncio principal del negocio existe siempre, en DEMO y en real.
    master_ads.ensure_default()

    from lot_bot.ai import build_provider

    ai_provider = provider or build_provider(settings)
    registry = build_registry()

    app = Application(
        settings=settings,
        paths=paths,
        db=db,
        events=events,
        audit=audit,
        accounts=accounts,
        backend=backend,
        catalog=catalog,
        images=images,
        listings=listings,
        publishing=publishing,
        messages=messages,
        market=market,
        master_ads=master_ads,
        automations=None,  # type: ignore[arg-type]
        agent=None,  # type: ignore[arg-type]
        business_settings=business,
    )
    app.automations = AutomationScheduler(app, db, events)
    app.agent = Agent(app, registry, ai_provider)

    app.api_keys = ApiKeyStore(db)
    app.image_generation = ImageGenerationService(
        db, build_image_generator(backend.demo, app.api_keys), paths.images / "generadas"
    )
    app.publish_queue = PublishQueue(db, audit, app)
    app.accounts.connected_listeners.append(app.publish_queue.account_reconnected)
    app.stats = StatsService(db, app)
    app.analyzer = StatsAnalyzer(app.stats)
    app.optimizer = Optimizer(app.analyzer)

    app.automations.ensure_definitions()
    if start_scheduler:
        # Lo único que se automatiza es la cola de publicación (≥ 60 s entre
        # anuncios). Las tareas programadas antiguas ya no se arrancan.
        app.publish_queue.start_worker()

    seed_business_from_env(app)

    if backend.demo:
        _sync_demo_listings(app)

    logger.info(
        "%s listo. Proveedor de IA: %s. Cuentas: %d.",
        APP_NAME,
        ai_provider.describe(),
        len(accounts.list_accounts()),
    )
    _application = app
    return app


def _sync_demo_listings(app: Application) -> None:
    """En DEMO, trae los anuncios simulados la primera vez, para que las
    pantallas no aparezcan vacias."""
    try:
        if app.listings.stats()["total"] > 0:
            return
        refs = [a.internal_ref for a in app.accounts.list_accounts() if a.is_connected]
        results = app.listings.sync_all(refs)
        total = sum(r.get("nuevos", 0) for r in results.values())
        logger.info("Anuncios de demostracion sincronizados: %d.", total)
        # También las conversaciones: si no, «¿qué mensajes nuevos hay?»
        # respondería que ninguno en una instalación recién estrenada.
        if app.messages.messaging_available:
            app.messages.sync_all(refs)
    except Exception as exc:  # nunca debe impedir el arranque
        logger.warning("No se han podido sincronizar los anuncios DEMO: %s", exc)


def seed_demo_catalog(app: Application) -> int:
    """Crea productos de ejemplo en DEMO si el catalogo esta vacio."""
    from lot_bot.catalog.service import ProductFilter

    if app.catalog.list_products(ProductFilter(limit=1)):
        return 0
    if not app.demo_mode:
        return 0

    sizes = [("90x190", 230.0), ("135x190", 270.0), ("150x190", 290.0)]
    colors = ["Gris", "Blanco", "Roble"]
    account_refs = [a.internal_ref for a in app.accounts.list_accounts()]
    created = 0
    for index, (size, price) in enumerate(sizes):
        product = app.catalog.create_product(
            {
                "name": f"Canapé abatible {size} {colors[index]}",
                "product_type": "Canapé abatible",
                "category": "Hogar y jardín",
                "subcategory": "Colchones y canapés",
                "size": size,
                "color": colors[index],
                "material": "Madera",
                "condition": "Nuevo",
                "price": price,
                "stock": 5,
                "description": "Canapé abatible con gran capacidad de almacenaje.",
            }
        )
        if account_refs:
            app.catalog.assign_to_accounts(product.id, account_refs[: index + 2])
        created += 1
    logger.info("Catálogo de demostración creado: %d productos.", created)
    return created
