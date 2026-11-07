"""Servicio del anuncio principal (plantilla maestra).

Reglas de funcionamiento:

  * La plantilla maestra NUNCA cambia al publicar. Publicar crea anuncios
    nuevos a partir de una copia de sus datos.
  * Los cambios de una publicación concreta («para este anuncio pon 12 €»)
    se guardan en esa publicación, no en la plantilla.
  * La plantilla solo cambia con una actualización explícita (la pantalla
    «Anuncio principal» o la orden «actualiza la plantilla»), siempre con
    confirmación previa.
  * En DEMO, si la plantilla no tiene categoría ni fotografías, se usan una
    categoría y unas imágenes de demostración, claramente marcadas, que NO
    se guardan en la plantilla ni se usan nunca en modo real.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from lot_bot.catalog.validation import validate_listing_data
from lot_bot.core.audit import AuditService
from lot_bot.core.text import fold
from lot_bot.database.engine import Database
from lot_bot.database.models import Listing, MasterAd
from lot_bot.master_ad.defaults import CLIENT_MASTER_AD, MASTER_KEY
from lot_bot.publishing.listings import ListingService, ListingView
from lot_bot.publishing.service import (
    ConfirmationRequiredError,
    ListingPreview,
    PublishOutcome,
)
from lot_bot.templates_engine.engine import TemplateEngine
from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.dto import ItemDraft
from lot_bot.wallapop.errors import WallapopError
from lot_bot.wallapop.service import WallapopService

logger = logging.getLogger(__name__)

#: Categoría usada SOLO en DEMO cuando la plantilla no tiene ninguna. Es una
#: de las categorías que devuelve el servicio simulado.
DEMO_CATEGORY = "Hogar y jardín"
DEMO_IMAGE_COUNT = 3

#: Campos que el usuario puede editar en la plantilla.
#: Datos del Anuncio principal guardados en «attributes» que NO son
#: características del producto.
NON_FEATURE_ATTRIBUTES = {"ubicacion", "activo", "envio"}

EDITABLE_FIELDS = {
    "name",
    "title",
    "features",
    "price",
    "description",
    "variants",
    "contact_whatsapp",
    "delivery_note",
    "category",
    "subcategory",
    "condition",
    "tags",
    "keywords",
    "aliases",
    "variables",
    "attributes",
    "locked",
}

#: Campos que se pueden cambiar en UNA publicación sin tocar la plantilla.
OVERRIDABLE_FIELDS = {"title", "description", "price", "category", "condition"}


class TemplateLockedError(ValueError):
    """La plantilla única está activa y no admite cambios automáticos."""


def format_price_value(value: Any) -> str:
    """230 -> «230»; 12.5 -> «12,50». Así queda igual que el texto del cliente."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".replace(".", ",")


def variant_key(medida: str) -> str:
    return "precio_" + str(medida).lower().replace(" ", "")


@dataclass(slots=True)
class MasterAdView:
    """Plantilla maestra en formato plano, para la interfaz y la IA."""

    id: int
    key: str
    name: str
    title: str
    features: list[str]
    price: float | None
    description_pattern: str
    description: str
    variants: list[dict[str, Any]]
    contact_whatsapp: str | None
    delivery_note: str | None
    category: str | None
    subcategory: str | None
    condition: str | None
    tags: list[str]
    keywords: list[str]
    images: list[dict[str, Any]]
    aliases: list[str]
    variables: dict[str, Any]
    is_default: bool
    missing_variables: list[str] = field(default_factory=list)
    #: Características estructuradas (estado, uso, color, material).
    attributes: dict[str, Any] = field(default_factory=dict)
    #: Plantilla única activa: sin cambios por publicación.
    locked: bool = True

    @property
    def features_line(self) -> str:
        return " · ".join(self.features)

    def variant_price(self, medida: str) -> float | None:
        target = str(medida).lower().replace(" ", "")
        for variant in self.variants:
            if str(variant.get("medida", "")).lower().replace(" ", "") == target:
                try:
                    return float(variant.get("precio"))
                except (TypeError, ValueError):
                    return None
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "clave": self.key,
            "nombre": self.name,
            "titulo": self.title,
            "caracteristicas": self.features_line,
            "precio": self.price,
            "descripcion": self.description,
            "ofertas": [
                {"medida": v.get("medida"), "precio": v.get("precio")} for v in self.variants
            ],
            "whatsapp": self.contact_whatsapp,
            "categoria": self.category or "(sin categoría)",
            "estado": self.condition,
            "etiquetas": self.tags,
            "palabras_clave": self.keywords,
            "fotografias": len(self.images),
        }


class MasterAdService:
    """Gestión y publicación del anuncio principal."""

    def __init__(
        self,
        database: Database,
        wallapop: WallapopService,
        listings: ListingService,
        audit: AuditService,
        images_root: Path,
        demo_mode: bool = True,
    ) -> None:
        self._db = database
        self._wallapop = wallapop
        self._listings = listings
        self._audit = audit
        self._images_root = Path(images_root)
        self.demo_mode = demo_mode
        self._engine = TemplateEngine()

    def set_backend(self, wallapop: WallapopService, demo_mode: bool) -> None:
        self._wallapop = wallapop
        self.demo_mode = demo_mode

    # ------------------------------------------------------------------
    # Alta inicial
    # ------------------------------------------------------------------
    def ensure_default(self) -> MasterAdView:
        """Crea el anuncio principal SOLO si no existe (punto de partida).

        Una vez creado, lo único que cuenta es lo que el usuario tenga en
        «Anuncio principal»: título, precio, estado, categoría, descripción,
        características, envío y fotos. Ninguna versión nueva del programa lo
        modifica.
        """
        with self._db.session_scope() as session:
            row = session.scalar(select(MasterAd).where(MasterAd.key == MASTER_KEY))
            if row is None:
                data = copy.deepcopy(CLIENT_MASTER_AD)
                row = MasterAd(**data, original=copy.deepcopy(CLIENT_MASTER_AD))
                session.add(row)
                session.flush()
                logger.info("Anuncio principal creado: %s", row.name)
            elif "ubicacion" not in (row.attributes or {}):
                # Campo nuevo: se añade UNA vez (si el usuario lo vacía, se respeta).
                row.attributes = {**(row.attributes or {}), "ubicacion": "Barcelona"}
            return self._to_view(row)

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------
    def get(self, key: str | None = None) -> MasterAdView | None:
        with self._db.session_scope() as session:
            row = self._find(session, key)
            return self._to_view(row) if row else None

    def list_all(self) -> list[MasterAdView]:
        with self._db.session_scope() as session:
            rows = session.scalars(select(MasterAd).order_by(MasterAd.id)).all()
            return [self._to_view(r) for r in rows]

    def find_by_text(self, text: str) -> MasterAdView | None:
        """¿El usuario se refiere a un anuncio principal? («sube el canapé»)."""
        folded = fold(text)
        if not folded:
            return None
        for view in self.list_all():
            for alias in view.aliases:
                alias_folded = fold(alias)
                if alias_folded and alias_folded in folded:
                    return view
        return None

    @staticmethod
    def _find(session, key: str | None) -> MasterAd | None:
        if key:
            row = session.scalar(select(MasterAd).where(MasterAd.key == key))
            if row is not None:
                return row
            if str(key).isdigit():
                return session.get(MasterAd, int(key))
            return None
        row = session.scalar(select(MasterAd).where(MasterAd.is_default.is_(True)))
        return row or session.scalar(select(MasterAd).order_by(MasterAd.id))

    def _to_view(self, row: MasterAd) -> MasterAdView:
        description, missing = self._render_description(
            row.description or "", row.variants or [], row.contact_whatsapp, row.variables or {}
        )
        return MasterAdView(
            id=row.id,
            key=row.key,
            name=row.name,
            title=row.title or "",
            features=list(row.features or []),
            price=row.price,
            description_pattern=row.description or "",
            description=description,
            variants=[dict(v) for v in (row.variants or [])],
            contact_whatsapp=row.contact_whatsapp,
            delivery_note=row.delivery_note,
            category=row.category or None,
            subcategory=row.subcategory or None,
            condition=row.condition,
            tags=list(row.tags or []),
            keywords=list(row.keywords or []),
            images=sorted(
                [dict(i) for i in (row.images or [])], key=lambda i: i.get("position", 0)
            ),
            aliases=list(row.aliases or []),
            variables=dict(row.variables or {}),
            is_default=row.is_default,
            missing_variables=missing,
            attributes=dict(row.attributes or {}),
            locked=bool(row.locked) if row.locked is not None else True,
        )

    def _render_description(
        self,
        pattern: str,
        variants: list[dict[str, Any]],
        whatsapp: str | None,
        variables: dict[str, Any],
    ) -> tuple[str, list[str]]:
        context: dict[str, Any] = {"whatsapp": whatsapp}
        for variant in variants:
            if variant.get("medida"):
                context[variant_key(variant["medida"])] = format_price_value(variant.get("precio"))
        context.update({k: v for k, v in variables.items() if v not in (None, "")})
        # render_text ordena espacios y lineas; la descripcion del cliente no
        # tiene espacios dobles, asi que el resultado es identico.
        return self._engine.render_text(pattern, context)

    # ------------------------------------------------------------------
    # Edición explícita de la plantilla
    # ------------------------------------------------------------------
    def update(
        self, key: str | None, changes: dict[str, Any], *, confirmed: bool, actor: str = "usuario"
    ) -> MasterAdView:
        """Actualiza la plantilla maestra. Solo con confirmación explícita."""
        if not confirmed:
            raise ConfirmationRequiredError("actualizar la plantilla del anuncio principal")
        unknown = set(changes) - EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"Campos no editables: {', '.join(sorted(unknown))}")
        with self._db.session_scope() as session:
            row = self._find(session, key)
            if row is None:
                raise ValueError("No existe el anuncio principal.")
            if "attributes" in changes and "features" not in changes:
                # Características = estado, uso, color, material (no la
                # ubicación ni «anuncio activo»).
                changes = {
                    **changes,
                    "features": [
                        v for k, v in (changes["attributes"] or {}).items()
                        if k not in NON_FEATURE_ATTRIBUTES and v
                    ],
                }
            before = {name: getattr(row, name) for name in changes}
            for name, value in changes.items():
                if name == "price" and value is not None:
                    value = float(value)
                setattr(row, name, copy.deepcopy(value))
            session.flush()
            view = self._to_view(row)
        self._audit.record_success(
            "Actualización de la plantilla del anuncio principal",
            target=view.name,
            detail="; ".join(
                f"{name}: {str(before[name])[:60]} → {str(changes[name])[:60]}" for name in changes
            )[:900],
            actor=actor,
        )
        return view

    def set_variant_price(
        self, key: str | None, medida: str, precio: float, *, confirmed: bool, actor: str = "usuario"
    ) -> MasterAdView:
        """Cambia el precio de una oferta por medida (p. ej. 135x190)."""
        view = self.get(key)
        if view is None:
            raise ValueError("No existe el anuncio principal.")
        if view.locked:
            raise TemplateLockedError(
                "La plantilla única está activa: sus precios no se cambian automáticamente. "
                "Edítala en «Anuncio principal» si quieres cambiarlos."
            )
        target = str(medida).lower().replace(" ", "")
        variants = [dict(v) for v in view.variants]
        found = False
        for variant in variants:
            if str(variant.get("medida", "")).lower().replace(" ", "") == target:
                variant["precio"] = float(precio) if not float(precio).is_integer() else int(precio)
                found = True
        if not found:
            raise ValueError(f"La plantilla no tiene ninguna oferta de {medida}.")
        return self.update(view.key, {"variants": variants}, confirmed=confirmed, actor=actor)

    def restore_original(self, key: str | None, *, confirmed: bool) -> MasterAdView:
        """Vuelve a los datos que proporcionó el cliente."""
        if not confirmed:
            raise ConfirmationRequiredError("restaurar la plantilla original")
        with self._db.session_scope() as session:
            row = self._find(session, key)
            if row is None or not row.original:
                raise ValueError("No hay datos originales guardados.")
            original = copy.deepcopy(row.original)
            images = row.images  # las fotografías del usuario se conservan
            for name in EDITABLE_FIELDS:
                if name in original:
                    setattr(row, name, original[name])
            row.images = images
            view = self._to_view(row)
        self._audit.record_success("Restauración de la plantilla original", target=view.name)
        return view

    # ------------------------------------------------------------------
    # Fotografías de la plantilla
    # ------------------------------------------------------------------
    def _save_images(self, key: str | None, images: list[dict[str, Any]]) -> MasterAdView:
        for position, image in enumerate(images):
            image["position"] = position
        if images and not any(i.get("is_primary") for i in images):
            images[0]["is_primary"] = True
        with self._db.session_scope() as session:
            row = self._find(session, key)
            if row is None:
                raise ValueError("No existe el anuncio principal.")
            row.images = images
            return self._to_view(row)

    def add_images(self, key: str | None, infos: list[dict[str, Any]]) -> MasterAdView:
        view = self.get(key)
        if view is None:
            raise ValueError("No existe el anuncio principal.")
        images = view.images + [
            {
                "path": info["path"],
                "original_name": info.get("original_name"),
                "file_format": info.get("file_format"),
                "content_hash": info.get("content_hash"),
                "perceptual_hash": info.get("perceptual_hash"),
                "is_primary": False,
                "source": "import",
            }
            for info in infos
            # Repetida = mismo contenido. Sin huella conocida no se descarta
            # (antes, dos fotos sin huella contaban como la misma).
            if not info.get("content_hash")
            or info.get("content_hash") not in {i.get("content_hash") for i in view.images}
        ]
        return self._save_images(view.key, images)

    def remove_image(self, key: str | None, index: int) -> MasterAdView:
        view = self.get(key)
        images = list(view.images)
        if 0 <= index < len(images):
            removed = images.pop(index)
            if removed.get("is_primary") and images:
                images[0]["is_primary"] = True
        return self._save_images(view.key, images)

    def move_image(self, key: str | None, index: int, delta: int) -> MasterAdView:
        view = self.get(key)
        images = list(view.images)
        target = index + delta
        if 0 <= index < len(images) and 0 <= target < len(images):
            images[index], images[target] = images[target], images[index]
        return self._save_images(view.key, images)

    def set_primary_image(self, key: str | None, index: int) -> MasterAdView:
        view = self.get(key)
        images = list(view.images)
        for position, image in enumerate(images):
            image["is_primary"] = position == index
        return self._save_images(view.key, images)

    def demo_images(self) -> list[dict[str, Any]]:
        """Imágenes de demostración (solo DEMO). Llevan el texto
        «IMAGEN DE DEMOSTRACIÓN» para que nadie las confunda con fotos reales."""
        from PIL import Image, ImageDraw

        folder = self._images_root / "demo-anuncio-principal"
        folder.mkdir(parents=True, exist_ok=True)
        images: list[dict[str, Any]] = []
        colours = [(96, 110, 124), (150, 150, 145), (120, 92, 70)]
        for index in range(DEMO_IMAGE_COUNT):
            path = folder / f"demo-{index + 1}.png"
            if not path.exists():
                canvas = Image.new("RGB", (900, 700), colours[index % len(colours)])
                draw = ImageDraw.Draw(canvas)
                draw.rectangle([40, 300, 860, 400], fill=(20, 20, 20))
                draw.text((70, 335), f"IMAGEN DE DEMOSTRACION {index + 1}", fill=(255, 200, 60))
                canvas.save(path)
            images.append(
                {
                    "path": str(path),
                    "file_format": "PNG",
                    "is_primary": index == 0,
                    "position": index,
                    "source": "demo",
                }
            )
        return images

    # ------------------------------------------------------------------
    # Publicación
    # ------------------------------------------------------------------
    @staticmethod
    def is_active(view: MasterAdView) -> bool:
        """«Anuncio activo» del Anuncio principal (por defecto, sí)."""
        return str((view.attributes or {}).get("activo") or "sí").strip().lower() != "no"

    def set_image_enabled(self, key: str | None, index: int, enabled: bool) -> MasterAdView:
        """Marca si una foto se usa al publicar (las no marcadas se guardan
        pero no se suben)."""
        view = self.get(key)
        images = list(view.images)
        if 0 <= index < len(images):
            images[index]["enabled"] = bool(enabled)
        return self._save_images(view.key, images)

    def effective_images(self, view: MasterAdView) -> list[dict[str, Any]]:
        """Fotos que se suben: las del usuario MARCADAS para usar."""
        real = [
            i for i in view.images if i.get("source") != "demo" and i.get("enabled", True)
        ]
        if real:
            return real
        return self.demo_images() if self.demo_mode else []

    def render(self, view: MasterAdView, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Datos listos para una publicación. `overrides` NO toca la plantilla."""
        overrides = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}
        if overrides and view.locked:
            raise TemplateLockedError(
                "La plantilla única está activa: todos los anuncios usan exactamente su "
                "título, precio y descripción. Para cambiarlos, edita la plantilla o "
                "desactívala en «Anuncio principal»."
            )
        unknown = set(overrides) - OVERRIDABLE_FIELDS
        if unknown:
            raise ValueError(
                f"En una publicación concreta solo se puede cambiar: "
                f"{', '.join(sorted(OVERRIDABLE_FIELDS))}."
            )
        category = overrides.get("category") or view.category
        demo_category = False
        if not category and self.demo_mode:
            category = DEMO_CATEGORY
            demo_category = True
        price = overrides.get("price", view.price)
        return {
            "title": overrides.get("title", view.title),
            "description": overrides.get("description", view.description),
            "price": float(price) if price is not None else None,
            "category": category,
            "demo_category": demo_category,
            "condition": overrides.get("condition", view.condition),
            "features": list(view.features),
            "attributes": dict(view.attributes),
            "tags": list(view.tags),
            "keywords": list(view.keywords),
            "images": self.effective_images(view),
        }

    def distribute(self, account_refs: list[str], copies: int | None) -> list[str]:
        """Reparte las publicaciones entre cuentas por turnos (rotación).

        Sin número de copias: una publicación en cada cuenta.
        """
        if not account_refs:
            return []
        if not copies:
            return list(account_refs)
        return [account_refs[i % len(account_refs)] for i in range(int(copies))]

    def build_previews(
        self,
        key: str | None,
        account_refs: list[str],
        copies: int | None = None,
        overrides: dict[str, Any] | None = None,
        *,
        extra_images: list[str] | None = None,
        only_images: list[str] | None = None,
    ) -> list[ListingPreview]:
        view = self.get(key)
        if view is None:
            raise ValueError("No existe el anuncio principal.")
        data = self.render(view, overrides)
        if only_images:
            # Rotación: SOLO la foto elegida para este anuncio.
            data["images"] = [
                {"path": p, "is_primary": i == 0, "source": "rotacion"}
                for i, p in enumerate(only_images)
            ]
        if extra_images:
            # SOLO la imagen generada para este anuncio: sin añadir las fotos
            # marcadas, que se repetirían en todos los anuncios.
            data["images"] = [
                {"path": p, "is_primary": i == 0, "source": "generada"}
                for i, p in enumerate(extra_images)
            ]
        previews: list[ListingPreview] = []
        for ref in self.distribute(account_refs, copies):
            quality = validate_listing_data(
                title=data["title"],
                description=data["description"],
                price=data["price"],
                category=data["category"],
                condition=data["condition"],
                features={"estado": data["condition"]},
                images=data["images"],
                subject=f"{view.name} → {ref}",
                # Las características del cliente son texto libre: no se
                # adivina cuál es el color o el material.
                required_features=(),
            )
            previews.append(
                ListingPreview(
                    product_id=0,
                    product_sku=f"PLANTILLA:{view.key}",
                    account_ref=ref,
                    title=data["title"],
                    description=data["description"],
                    price=data["price"],
                    currency="EUR",
                    category=data["category"],
                    condition=data["condition"],
                    features=data["features"],
                    tags=data["tags"] + data["keywords"],
                    image_paths=[i["path"] for i in data["images"]],
                    quality=quality,
                    missing_variables=list(view.missing_variables),
                )
            )
        return previews

    def publish(
        self,
        key: str | None,
        account_refs: list[str],
        copies: int | None = None,
        overrides: dict[str, Any] | None = None,
        *,
        confirmed: bool,
        actor: str = "usuario",
    ) -> list[PublishOutcome]:
        """Publica copias del anuncio principal. La plantilla no cambia."""
        if not confirmed:
            raise ConfirmationRequiredError("publicar el anuncio principal")
        self._wallapop.require(Capability.CREATE_ITEM)
        view = self.get(key)
        if view is None:
            raise ValueError("No existe el anuncio principal.")
        previews = self.build_previews(view.key, account_refs, copies, overrides)
        data = self.render(view, overrides)
        clean_overrides = {
            k: v for k, v in (overrides or {}).items() if k in OVERRIDABLE_FIELDS and v not in (None, "")
        }

        if len(previews) > 1 and not getattr(self._wallapop, "is_mock", False):
            # Con Wallapop real se publica siempre a través de la cola, que
            # respeta el intervalo mínimo entre publicaciones.
            raise ValueError(
                "Para publicar varios anuncios en Wallapop se usa la cola de publicación "
                "(intervalo mínimo entre anuncios)."
            )
        return [
            self._publish_preview(view, data, preview, clean_overrides, actor)
            for preview in previews
        ]

    def publish_single(
        self,
        key: str | None,
        account_ref: str,
        overrides: dict[str, Any] | None = None,
        *,
        extra_images: list[str] | None = None,
        only_images: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        confirmed: bool,
        actor: str = "usuario",
    ) -> PublishOutcome:
        """Publica UNA copia (la usa la cola de publicación)."""
        if not confirmed:
            raise ConfirmationRequiredError("publicar el anuncio principal")
        current = self.get(key)
        if current is not None and not self.is_active(current):
            raise ValueError(
                "El Anuncio principal está DESACTIVADO: márcalo como «Anuncio activo» en "
                "«Anuncio principal» para que el bot lo publique."
            )
        self._wallapop.require(Capability.CREATE_ITEM)
        view = self.get(key)
        if view is None:
            raise ValueError("No existe el anuncio principal.")
        previews = self.build_previews(
            view.key, [account_ref], None, overrides, extra_images=extra_images,
            only_images=only_images,
        )
        data = self.render(view, overrides)
        clean_overrides = {
            k: v for k, v in (overrides or {}).items() if k in OVERRIDABLE_FIELDS and v not in (None, "")
        }
        return self._publish_preview(view, data, previews[0], clean_overrides, actor, meta=meta)

    def _publish_preview(
        self,
        view: MasterAdView,
        data: dict[str, Any],
        preview: ListingPreview,
        clean_overrides: dict[str, Any],
        actor: str,
        *,
        meta: dict[str, Any] | None = None,
    ) -> PublishOutcome:
        if not preview.can_publish:
            reasons = [i.message for i in (preview.quality.errors if preview.quality else [])]
            if preview.missing_variables:
                reasons.append("Variables sin rellenar: " + ", ".join(preview.missing_variables))
            message = "No se publica: " + " ".join(reasons)
            self._audit.record_error(
                "Publicación del anuncio principal bloqueada",
                error=message,
                account_ref=preview.account_ref,
                target=view.name,
                actor=actor,
            )
            return PublishOutcome(
                product_sku=preview.product_sku,
                account_ref=preview.account_ref,
                success=False,
                message=message,
                error_code="CALIDAD_INSUFICIENTE",
            )

        image_urls: list[str] = []
        if self._wallapop.supports(Capability.UPLOAD_IMAGE):
            for path in preview.image_paths:
                try:
                    image_urls.append(self._wallapop.upload_image(preview.account_ref, path))
                except WallapopError as exc:
                    logger.warning("No se ha podido subir '%s': %s", path, exc.detail)

        draft = ItemDraft(
            title=preview.title,
            description=preview.description,
            price=float(preview.price or 0),
            category=preview.category,
            condition=preview.condition,
            attributes={
                "caracteristicas": list(view.features),
                **{k: v for k, v in (view.attributes or {}).items() if v},
            },
            image_paths=preview.image_paths,
            image_urls=image_urls,
        )
        try:
            result = self._wallapop.create_item(preview.account_ref, draft)
        except WallapopError as exc:
            self._audit.record_error(
                "Publicación del anuncio principal",
                error=f"{type(exc).__name__}: {exc.detail}",
                account_ref=preview.account_ref,
                target=view.name,
                actor=actor,
            )
            return PublishOutcome(
                product_sku=preview.product_sku,
                account_ref=preview.account_ref,
                success=False,
                message=exc.user_message,
                error_code=type(exc).__name__,
            )

        if not result.success:
            # Se pulsó «Publicar» pero Wallapop no lo ha confirmado: NO se
            # registra como publicado.
            code = (result.data or {}).get("codigo") or "ERROR"
            self._audit.record_error(
                "Publicación del anuncio principal: resultado no confirmado",
                error=result.message,
                account_ref=preview.account_ref,
                target=view.name,
                actor=actor,
            )
            return PublishOutcome(
                product_sku=preview.product_sku,
                account_ref=preview.account_ref,
                success=False,
                message=result.message,
                error_code=code,
            )

        if result.item_id:
            self._listings.register_published(
                preview.account_ref,
                result.item_id,
                None,
                {
                    "title": preview.title,
                    "description": preview.description,
                    "price": preview.price,
                    "category": preview.category,
                    "condition": preview.condition,
                    "attributes": {
                        "caracteristicas": " · ".join(view.features),
                        "estado": view.condition,
                    },
                    "image_urls": image_urls or preview.image_paths,
                    "url": (result.data or {}).get("url"),
                },
                master_ad_id=view.id,
                overrides=clean_overrides,
                meta=meta,
            )
        self._audit.record_success(
            "Publicación del anuncio principal",
            account_ref=preview.account_ref,
            target=view.name,
            detail=f"{preview.price} €. {result.message}"
            + (" Categoría de demostración." if data.get("demo_category") else ""),
            actor=actor,
        )
        url = (result.data or {}).get("url")
        return PublishOutcome(
            product_sku=preview.product_sku,
            account_ref=preview.account_ref,
            success=True,
            message=result.message,
            item_id=result.item_id,
            url=url,
        )

    # ------------------------------------------------------------------
    # Publicaciones y estadísticas
    # ------------------------------------------------------------------
    def publications(self, key: str | None = None) -> list[ListingView]:
        view = self.get(key)
        if view is None:
            return []
        with self._db.session_scope() as session:
            ids = [
                listing_id
                for (listing_id,) in session.execute(
                    select(Listing.id).where(Listing.master_ad_id == view.id)
                ).all()
            ]
        views = [self._listings.get(i) for i in ids]
        return [v for v in views if v is not None]

    def stats(self, key: str | None = None) -> dict[str, Any]:
        publications = self.publications(key)
        by_account: dict[str, int] = {}
        for item in publications:
            by_account[item.account_alias] = by_account.get(item.account_alias, 0) + 1
        active = [p for p in publications if p.status == "active"]
        return {
            "publicaciones": len(publications),
            "activas": len(active),
            "por_cuenta": by_account,
            "visitas": sum(p.views for p in publications),
            "favoritos": sum(p.favorites for p in publications),
        }
