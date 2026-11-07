"""Servicio de publicacion.

Flujo obligatorio:

    Producto -> Validacion -> Generacion (plantilla/IA) -> Vista previa
             -> CONFIRMACION -> Publicacion -> Resultado

Nada se publica ni se modifica sin `confirmed=True`. El agente IA no puede
saltarse este paso: las herramientas de escritura exigen confirmacion previa
del usuario en la interfaz.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from lot_bot.catalog.service import CatalogService
from lot_bot.catalog.validation import QualityReport, validate_listing_data
from lot_bot.core.audit import AuditService
from lot_bot.database.engine import Database
from lot_bot.database.models import ProductStatus, Template
from lot_bot.publishing.listings import ListingService
from lot_bot.templates_engine.engine import TemplateEngine
from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.dto import ItemDraft
from lot_bot.wallapop.errors import WallapopError
from lot_bot.wallapop.service import WallapopService

logger = logging.getLogger(__name__)


class ConfirmationRequiredError(RuntimeError):
    """Se ha intentado publicar o modificar sin confirmacion del usuario."""

    def __init__(self, action: str) -> None:
        self.action = action
        super().__init__(
            f"La acción '{action}' modifica información en Wallapop y requiere "
            f"confirmación explícita del usuario."
        )


@dataclass(slots=True)
class ListingPreview:
    """Vista previa de lo que se va a publicar en una cuenta concreta."""

    product_id: int
    product_sku: str
    account_ref: str
    title: str
    description: str
    price: float | None
    currency: str
    category: str | None
    condition: str | None
    features: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    image_paths: list[str] = field(default_factory=list)
    quality: QualityReport | None = None
    missing_variables: list[str] = field(default_factory=list)

    @property
    def can_publish(self) -> bool:
        return bool(self.quality and self.quality.can_publish and not self.missing_variables)

    def summary(self) -> str:
        lines = [
            f"Cuenta: {self.account_ref}",
            f"Título: {self.title}",
            f"Precio: {self.price:.2f} €" if self.price else "Precio: (sin definir)",
            f"Fotografías: {len(self.image_paths)}",
        ]
        if self.quality:
            lines.append(f"Calidad: {self.quality.score}/100")
            for issue in self.quality.errors:
                lines.append(f"  ERROR · {issue.message}")
            for issue in self.quality.warnings:
                lines.append(f"  aviso · {issue.message}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "producto": self.product_sku,
            "cuenta": self.account_ref,
            "titulo": self.title,
            "descripcion": self.description,
            "precio": self.price,
            "categoria": self.category,
            "caracteristicas": self.features,
            "etiquetas": self.tags,
            "fotografias": len(self.image_paths),
            "publicable": self.can_publish,
            "calidad": self.quality.to_dict() if self.quality else None,
        }


@dataclass(slots=True)
class PublishOutcome:
    """Resultado de publicar un producto en una cuenta."""

    product_sku: str
    account_ref: str
    success: bool
    message: str
    item_id: str | None = None
    error_code: str | None = None
    #: Dirección pública del anuncio, si Wallapop la muestra.
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "producto": self.product_sku,
            "cuenta": self.account_ref,
            "correcto": self.success,
            "mensaje": self.message,
            "anuncio": self.item_id,
            "codigo_error": self.error_code,
        }


class PublishingService:
    """Prepara, valida y publica anuncios a partir del catalogo."""

    def __init__(
        self,
        database: Database,
        wallapop: WallapopService,
        catalog: CatalogService,
        listings: ListingService,
        audit: AuditService,
        business_settings: dict[str, Any] | None = None,
    ) -> None:
        self._db = database
        self._wallapop = wallapop
        self._catalog = catalog
        self._listings = listings
        self._audit = audit
        self._engine = TemplateEngine()
        self.business = business_settings or {}

    def set_backend(self, wallapop: WallapopService) -> None:
        self._wallapop = wallapop

    def set_business_settings(self, settings: dict[str, Any]) -> None:
        self.business = settings or {}

    # ------------------------------------------------------------------
    # Generacion y vista previa
    # ------------------------------------------------------------------
    def _load_template(self, template_id: int | None) -> Template | None:
        with self._db.session_scope() as session:
            if template_id:
                template = session.get(Template, template_id)
                if template is not None:
                    session.expunge(template)
                    return template
            template = session.scalar(select(Template).where(Template.is_default.is_(True)))
            if template is not None:
                session.expunge(template)
            return template

    def build_preview(
        self,
        product_identifier: str | int,
        account_ref: str,
        overrides: dict[str, Any] | None = None,
    ) -> ListingPreview:
        """Genera la vista previa del anuncio sin tocar Wallapop."""
        product = self._catalog.get_product(product_identifier)
        if product is None:
            raise ValueError(f"No se encuentra el producto '{product_identifier}'.")

        template = self._load_template(product.template_id)
        overrides = overrides or {}

        context = TemplateEngine.build_context(
            product={
                "name": product.name,
                "sku": product.sku,
                "product_type": product.product_type,
                "category": product.category,
                "subcategory": product.subcategory,
                "size": product.size,
                "color": product.color,
                "material": product.material,
                "condition": product.condition,
                "price": overrides.get("price", product.price),
                "stock": product.stock,
                "description": product.description,
                "features": product.features,
            },
            business=self.business,
            extra={**(template.variables if template else {}), **overrides.get("variables", {})},
        )

        if template is not None:
            rendered = self._engine.render(
                title_pattern=product.title_override or template.title_pattern,
                description_pattern=template.description_pattern,
                features_pattern=list(template.features_pattern or []),
                tags_pattern=list(template.tags_pattern or []),
                context=context,
            )
            title = overrides.get("title") or rendered.title
            description = overrides.get("description") or rendered.description
            features = rendered.features
            tags = rendered.tags or product.tags
            missing = rendered.missing_variables
        else:
            title = overrides.get("title") or product.title_override or product.name
            description = overrides.get("description") or product.description or ""
            features = [str(v) for v in product.features.values() if v]
            tags = product.tags
            missing = []

        price = overrides.get("price", product.price)
        image_paths = [image["path"] for image in sorted(product.images, key=lambda i: i["position"])]

        quality = validate_listing_data(
            title=title,
            description=description,
            price=price,
            category=product.category,
            condition=product.condition,
            features=product.features,
            images=product.images,
            subject=f"{product.sku} → {account_ref}",
        )

        return ListingPreview(
            product_id=product.id,
            product_sku=product.sku,
            account_ref=account_ref,
            title=title,
            description=description,
            price=price,
            currency="EUR",
            category=product.category,
            condition=product.condition,
            features=features,
            tags=tags,
            image_paths=image_paths,
            quality=quality,
            missing_variables=missing,
        )

    def build_previews(
        self,
        product_identifier: str | int,
        account_refs: list[str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> list[ListingPreview]:
        """Vista previa para todas las cuentas asignadas al producto."""
        product = self._catalog.get_product(product_identifier)
        if product is None:
            raise ValueError(f"No se encuentra el producto '{product_identifier}'.")
        targets = account_refs or product.accounts
        if not targets:
            raise ValueError(
                f"El producto '{product.sku}' no está asignado a ninguna cuenta. "
                f"Asígnalo primero en Productos → Cuentas."
            )
        return [self.build_preview(product.id, ref, overrides) for ref in targets]

    # ------------------------------------------------------------------
    # Publicacion
    # ------------------------------------------------------------------
    def publish(
        self,
        product_identifier: str | int,
        account_refs: list[str] | None = None,
        *,
        confirmed: bool,
        overrides: dict[str, Any] | None = None,
        actor: str = "usuario",
    ) -> list[PublishOutcome]:
        """Publica un producto en una o varias cuentas.

        `confirmed=True` es obligatorio: sin confirmacion no se publica nada.
        """
        if not confirmed:
            raise ConfirmationRequiredError("publicar anuncio")

        self._wallapop.require(Capability.CREATE_ITEM)
        previews = self.build_previews(product_identifier, account_refs, overrides)
        outcomes: list[PublishOutcome] = []

        for preview in previews:
            if not preview.can_publish:
                reasons = [i.message for i in (preview.quality.errors if preview.quality else [])]
                if preview.missing_variables:
                    reasons.append(
                        "Variables sin rellenar: " + ", ".join(preview.missing_variables)
                    )
                message = "No se publica: " + " ".join(reasons)
                outcomes.append(
                    PublishOutcome(
                        product_sku=preview.product_sku,
                        account_ref=preview.account_ref,
                        success=False,
                        message=message,
                        error_code="CALIDAD_INSUFICIENTE",
                    )
                )
                self._audit.record_error(
                    "Publicación bloqueada por control de calidad",
                    error=message,
                    account_ref=preview.account_ref,
                    target=preview.product_sku,
                    actor=actor,
                )
                continue

            outcomes.append(self._publish_one(preview, actor=actor))

        if any(o.success for o in outcomes):
            self._catalog.set_status(product_identifier, ProductStatus.PUBLISHED)
        return outcomes

    def _publish_one(self, preview: ListingPreview, actor: str) -> PublishOutcome:
        image_urls = self._prepare_images(preview)
        draft = ItemDraft(
            title=preview.title,
            description=preview.description,
            price=float(preview.price or 0),
            currency=preview.currency,
            category=preview.category,
            condition=preview.condition,
            attributes={"caracteristicas": preview.features, "etiquetas": preview.tags},
            image_paths=preview.image_paths,
            image_urls=image_urls,
        )
        try:
            result = self._wallapop.create_item(preview.account_ref, draft)
        except WallapopError as exc:
            logger.warning(
                "Wallapop ha rechazado la publicación de '%s' en '%s': %s",
                preview.product_sku,
                preview.account_ref,
                exc.detail,
            )
            self._audit.record_error(
                "Publicación de anuncio",
                error=f"{type(exc).__name__}: {exc.detail}",
                account_ref=preview.account_ref,
                target=preview.product_sku,
                actor=actor,
            )
            return PublishOutcome(
                product_sku=preview.product_sku,
                account_ref=preview.account_ref,
                success=False,
                message=exc.user_message,
                error_code=type(exc).__name__,
            )

        if result.item_id:
            self._listings.register_published(
                preview.account_ref,
                result.item_id,
                preview.product_id,
                {
                    "title": preview.title,
                    "description": preview.description,
                    "price": preview.price,
                    "currency": preview.currency,
                    "category": preview.category,
                    "condition": preview.condition,
                    "attributes": {"caracteristicas": preview.features},
                    "image_urls": image_urls,
                },
            )
        self._audit.record_success(
            "Publicación de anuncio",
            account_ref=preview.account_ref,
            target=f"{preview.product_sku} · {preview.title}",
            detail=f"Precio {preview.price} €. {result.message}",
            actor=actor,
        )
        return PublishOutcome(
            product_sku=preview.product_sku,
            account_ref=preview.account_ref,
            success=True,
            message=result.message,
            item_id=result.item_id,
        )

    def _prepare_images(self, preview: ListingPreview) -> list[str]:
        """Sube las fotografias si la integracion lo permite."""
        if not preview.image_paths:
            return []
        if not self._wallapop.supports(Capability.UPLOAD_IMAGE):
            # Sin endpoint de subida, se envian las rutas tal cual y sera
            # Wallapop quien acepte o rechace; no se simula una subida.
            return []
        urls: list[str] = []
        for path in preview.image_paths:
            try:
                urls.append(self._wallapop.upload_image(preview.account_ref, path))
            except WallapopError as exc:
                logger.warning("No se ha podido subir '%s': %s", path, exc.detail)
        return urls

    # ------------------------------------------------------------------
    # Modificaciones sobre anuncios ya publicados
    # ------------------------------------------------------------------
    def _with_url(self, listing_id: int, synced: set[str] | None = None):
        """El anuncio con su dirección de Wallapop. Si se publicó por navegador
        y Wallapop no mostró la dirección, se lee la lista de productos de la
        cuenta (una vez) para encontrarla."""
        view = self._listings.get(listing_id)
        if (
            view is None
            or view.url
            or not str(view.wallapop_item_id or "").startswith("navegador-")
            or Capability.LIST_ITEMS not in self._wallapop.capabilities()
        ):
            return view
        synced = synced if synced is not None else set()
        if view.account_ref not in synced:
            synced.add(view.account_ref)
            try:
                self._listings.sync_account(view.account_ref)
            except WallapopError:
                raise
            except Exception as exc:  # se informa en el resultado del anuncio
                logger.warning("No se ha podido leer la lista de productos: %s", exc)
        return self._listings.get(listing_id)

    def update_listing(
        self,
        listing_id: int,
        changes: dict[str, Any],
        *,
        confirmed: bool,
        actor: str = "usuario",
    ) -> PublishOutcome:
        if not confirmed:
            raise ConfirmationRequiredError("modificar anuncio")

        try:
            view = self._with_url(listing_id)
        except WallapopError as exc:
            return PublishOutcome("", "", False, exc.user_message, error_code=type(exc).__name__)
        if view is None:
            raise ValueError(f"No se encuentra el anuncio {listing_id}.")
        if not view.wallapop_item_id:
            raise ValueError("El anuncio no tiene identificador de Wallapop.")

        self._wallapop.require(Capability.UPDATE_ITEM)
        try:
            result = self._wallapop.update_item(
                view.account_ref, view.wallapop_item_id, changes, item_url=view.url
            )
        except WallapopError as exc:
            self._audit.record_error(
                "Modificación de anuncio",
                error=f"{type(exc).__name__}: {exc.detail}",
                account_ref=view.account_ref,
                target=view.title,
                actor=actor,
            )
            return PublishOutcome(
                product_sku=view.product_sku or "",
                account_ref=view.account_ref,
                success=False,
                message=exc.user_message,
                error_code=type(exc).__name__,
            )

        self._listings.apply_local_changes(listing_id, changes)
        self._audit.record_success(
            "Modificación de anuncio",
            account_ref=view.account_ref,
            target=view.title,
            detail=", ".join(f"{k}={v}" for k, v in changes.items())[:500],
            actor=actor,
        )
        return PublishOutcome(
            product_sku=view.product_sku or "",
            account_ref=view.account_ref,
            success=True,
            message=result.message,
            item_id=view.wallapop_item_id,
        )

    def update_prices(
        self,
        listing_ids: list[int],
        price: float,
        *,
        confirmed: bool,
        actor: str = "usuario",
    ) -> list[PublishOutcome]:
        """Cambio de precio en lote (la operacion mas habitual del cliente)."""
        if not confirmed:
            raise ConfirmationRequiredError("cambiar precio")
        if price is None or price <= 0:
            raise ValueError("El precio debe ser mayor que cero.")

        self._wallapop.require(Capability.UPDATE_ITEM_PRICE)
        outcomes: list[PublishOutcome] = []
        synced: set[str] = set()
        for listing_id in listing_ids:
            try:
                view = self._with_url(listing_id, synced)
            except WallapopError as exc:
                outcomes.append(
                    PublishOutcome("", "", False, exc.user_message, error_code=type(exc).__name__)
                )
                break
            if view is None or not view.wallapop_item_id:
                outcomes.append(
                    PublishOutcome("", "", False, f"Anuncio {listing_id} no encontrado.", error_code="NO_ENCONTRADO")
                )
                continue
            try:
                result = self._wallapop.update_item_price(
                    view.account_ref, view.wallapop_item_id, price, item_url=view.url
                )
            except WallapopError as exc:
                self._audit.record_error(
                    "Cambio de precio",
                    error=f"{type(exc).__name__}: {exc.detail}",
                    account_ref=view.account_ref,
                    target=view.title,
                    actor=actor,
                )
                outcomes.append(
                    PublishOutcome(
                        product_sku=view.product_sku or "",
                        account_ref=view.account_ref,
                        success=False,
                        message=exc.user_message,
                        error_code=type(exc).__name__,
                    )
                )
                continue

            self._listings.apply_local_price(listing_id, price)
            self._audit.record_success(
                "Cambio de precio",
                account_ref=view.account_ref,
                target=view.title,
                detail=f"{view.price} € → {price:.2f} €",
                actor=actor,
            )
            outcomes.append(
                PublishOutcome(
                    product_sku=view.product_sku or "",
                    account_ref=view.account_ref,
                    success=True,
                    message=result.message,
                    item_id=view.wallapop_item_id,
                )
            )
        return outcomes

    def delete_listing(
        self,
        listing_id: int,
        *,
        confirmed: bool,
        actor: str = "usuario",
        synced: set[str] | None = None,
    ) -> PublishOutcome:
        """Elimina un anuncio. Accion destructiva: exige confirmacion."""
        if not confirmed:
            raise ConfirmationRequiredError("eliminar anuncio")

        try:
            view = self._with_url(listing_id, synced)
        except WallapopError as exc:
            return PublishOutcome("", "", False, exc.user_message, error_code=type(exc).__name__)
        if view is None or not view.wallapop_item_id:
            raise ValueError(f"No se encuentra el anuncio {listing_id}.")
        if not view.url and view.status == "removed":
            # Al leer la lista de la cuenta ya no estaba: nada que borrar.
            return PublishOutcome(
                product_sku=view.product_sku or "",
                account_ref=view.account_ref,
                success=True,
                message="El anuncio ya no estaba en Wallapop.",
                item_id=view.wallapop_item_id,
            )

        self._wallapop.require(Capability.DELETE_ITEM)
        try:
            result = self._wallapop.delete_item(
                view.account_ref, view.wallapop_item_id, item_url=view.url
            )
        except WallapopError as exc:
            self._audit.record_error(
                "Eliminación de anuncio",
                error=f"{type(exc).__name__}: {exc.detail}",
                account_ref=view.account_ref,
                target=view.title,
                actor=actor,
            )
            return PublishOutcome(
                product_sku=view.product_sku or "",
                account_ref=view.account_ref,
                success=False,
                message=exc.user_message,
                error_code=type(exc).__name__,
            )

        self._listings.mark_removed(listing_id)
        self._audit.record_success(
            "Eliminación de anuncio",
            account_ref=view.account_ref,
            target=view.title,
            actor=actor,
        )
        return PublishOutcome(
            product_sku=view.product_sku or "",
            account_ref=view.account_ref,
            success=True,
            message=result.message,
            item_id=view.wallapop_item_id,
        )

    #: Errores que paran el borrado en lote: hacen falta el usuario o la web cambió.
    STOP_DELETING = {
        "VerificationRequiredError",
        "AuthenticationError",
        "RateLimitError",
        "ProfileInUseError",
        "WindowClosedError",
    }

    def delete_listings(
        self,
        listing_ids: list[int],
        *,
        confirmed: bool,
        actor: str = "usuario",
        interval_seconds: float = 60.0,
        sleep=None,
        on_progress=None,
    ) -> list[PublishOutcome]:
        """Elimina varios anuncios, UNO DETRÁS DE OTRO y con una espera entre
        cada uno (como al publicar). Si Wallapop pide una verificación o la
        sesión, se PARA: los que quedan no se tocan."""
        if not confirmed:
            raise ConfirmationRequiredError("eliminar anuncios")
        import time as _time

        sleep = sleep or _time.sleep
        outcomes: list[PublishOutcome] = []
        synced: set[str] = set()  # cada cuenta se lee una sola vez
        for position, listing_id in enumerate(listing_ids):
            if position:
                sleep(interval_seconds)
            try:
                outcome = self.delete_listing(
                    listing_id, confirmed=True, actor=actor, synced=synced
                )
            except ValueError as exc:
                outcome = PublishOutcome(
                    product_sku="", account_ref="", success=False, message=str(exc),
                    error_code="NO_ENCONTRADO",
                )
            outcomes.append(outcome)
            if on_progress:
                on_progress(position + 1, len(listing_ids), outcome)
            if not outcome.success and outcome.error_code in self.STOP_DELETING:
                break
        return outcomes
