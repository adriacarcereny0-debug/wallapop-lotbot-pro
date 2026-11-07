"""Integración directa con Wallapop (sin navegador).

Seis piezas independientes, cada una con una sola responsabilidad:

=====================  ===================================================
AuthenticationManager  Conectar, validar, comprobar, renovar y desconectar
WallapopClient         Única puerta hacia Wallapop (provisional u oficial)
PublicationEngine      Publica la cola en orden, con límites y recuperación
QueueManager           Cola persistente, duplicados, historial, CSV
ImageManager           Valida, deduplica y sube las fotografías
DatabaseManager        Persistencia, ajustes, integridad y copias
=====================  ===================================================

`build_integration()` las ensambla sobre la `Application` de LOT Bot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lot_bot.integration.auth_manager import (
    AUTH_STATE_LABELS,
    AuthenticationManager,
    AuthState,
    ConnectionResult,
    ConnectionStatus,
)
from lot_bot.integration.database_manager import DatabaseManager
from lot_bot.integration.image_manager import ImageManager, ImagePreparationError
from lot_bot.integration.publication_engine import (
    ENGINE_STATE_LABELS,
    EngineState,
    EngineStatus,
    PublicationEngine,
)
from lot_bot.integration.queue_manager import AddResult, AdInput, QueueManager
from lot_bot.integration.wallapop_client import (
    PROVIDER_LABELS,
    ConnectionProcedure,
    ErrorKind,
    WallapopClient,
    classify_error,
)


@dataclass
class DirectIntegration:
    """Las seis piezas ya conectadas entre sí."""

    database: DatabaseManager
    client: WallapopClient
    auth: AuthenticationManager
    images: ImageManager
    queue: QueueManager
    engine: PublicationEngine

    def shutdown(self) -> None:
        self.engine.shutdown()


def build_integration(app: Any, clock: Any = None) -> DirectIntegration:
    """Construye la integración directa sobre una `Application`."""
    database = DatabaseManager(app.db)
    database.ensure_schema()
    client = WallapopClient(lambda: app.backend)
    images = ImageManager(app.images)
    queue = QueueManager(
        database,
        account_resolver=app.accounts.resolve_ref,
        image_validator=images.validate_paths,
    )
    auth = AuthenticationManager(database, app.accounts, client, app.audit)
    publish_queue = getattr(app, "publish_queue", None)
    engine = PublicationEngine(
        database,
        queue,
        auth,
        client,
        images,
        listings=app.listings,
        audit=app.audit,
        clock=clock,
        other_last_publish=getattr(publish_queue, "_last_publish_ts", None),
    )
    # Al reconectar una cuenta (desde cualquier pantalla), el motor sigue solo.
    app.accounts.connected_listeners.append(engine.account_ready)
    return DirectIntegration(database, client, auth, images, queue, engine)


def enqueue_products(app: Any, product_ids: list[int] | None = None) -> AddResult:
    """Pone en la cola los productos del catálogo, en sus cuentas asignadas.

    El título y la descripción salen de la plantilla del producto (igual que
    en «Productos → Publicar»). Se saltan los que tienen errores de calidad
    que impiden publicar y los archivados.
    """
    from lot_bot.catalog.service import ProductFilter
    from lot_bot.database.models import ProductStatus

    products = app.catalog.list_products(ProductFilter(limit=5000))
    if product_ids is not None:
        wanted = set(product_ids)
        products = [p for p in products if p.id in wanted]
    ads: list[AdInput] = []
    skipped: list[str] = []
    for product in products:
        if product.status == ProductStatus.ARCHIVED.value or not product.accounts:
            if not product.accounts:
                skipped.append(f"{product.sku}: no está asignado a ninguna cuenta.")
            continue
        for preview in app.publishing.build_previews(product.id):
            if not preview.quality.can_publish:
                reasons = "; ".join(i.message for i in preview.quality.errors)
                skipped.append(f"{preview.quality.summary()} {reasons}".strip())
                continue
            ads.append(
                AdInput(
                    account_ref=preview.account_ref,
                    title=preview.title,
                    description=preview.description,
                    price=float(preview.price or 0),
                    category=preview.category,
                    condition=preview.condition,
                    image_paths=list(preview.image_paths),
                    attributes={"caracteristicas": preview.features, "etiquetas": preview.tags},
                    source="catalogo",
                    batch="Catálogo",
                    product_id=product.id,
                )
            )
    result = app.direct.queue.add_many(ads)
    result.invalid = skipped + result.invalid
    return result


__all__ = [
    "AUTH_STATE_LABELS",
    "ENGINE_STATE_LABELS",
    "PROVIDER_LABELS",
    "AdInput",
    "AddResult",
    "AuthState",
    "AuthenticationManager",
    "ConnectionProcedure",
    "ConnectionResult",
    "ConnectionStatus",
    "DatabaseManager",
    "DirectIntegration",
    "EngineState",
    "EngineStatus",
    "ErrorKind",
    "ImageManager",
    "ImagePreparationError",
    "PublicationEngine",
    "QueueManager",
    "WallapopClient",
    "build_integration",
    "classify_error",
    "enqueue_products",
]
