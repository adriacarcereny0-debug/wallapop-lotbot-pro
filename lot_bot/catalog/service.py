"""Servicio de catalogo: productos, inventario, asignaciones y calidad."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from lot_bot.catalog.duplicates import DuplicateGroup, find_duplicates
from lot_bot.catalog.validation import QualityReport, validate_listing_data
from lot_bot.core.text import contains
from lot_bot.database.engine import Database
from lot_bot.database.models import (
    Account,
    Product,
    ProductAssignment,
    ProductImage,
    ProductStatus,
)

logger = logging.getLogger(__name__)

_SIZE_RE = re.compile(r"(\d{2,3})\s*[x×]\s*(\d{2,3})", re.IGNORECASE)


def normalize_size(text: str | None) -> str | None:
    """Convierte '135 x 190', '135X190' o '135x190 cm' en '135x190'."""
    if not text:
        return None
    match = _SIZE_RE.search(str(text))
    if not match:
        return str(text).strip() or None
    return f"{match.group(1)}x{match.group(2)}"


@dataclass(slots=True)
class ProductFilter:
    """Criterios de busqueda en el catalogo."""

    text: str | None = None
    product_type: str | None = None
    size: str | None = None
    color: str | None = None
    material: str | None = None
    category: str | None = None
    status: ProductStatus | None = None
    sku: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    only_in_stock: bool = False
    account_ref: str | None = None
    limit: int = 200

    def describe(self) -> str:
        parts = []
        if self.text:
            parts.append(f"texto '{self.text}'")
        if self.product_type:
            parts.append(f"tipo '{self.product_type}'")
        if self.size:
            parts.append(f"medida {self.size}")
        if self.color:
            parts.append(f"color {self.color}")
        if self.material:
            parts.append(f"material {self.material}")
        if self.status:
            parts.append(f"estado {self.status.value}")
        if self.account_ref:
            parts.append(f"cuenta {self.account_ref}")
        return ", ".join(parts) or "sin filtros"


@dataclass(slots=True)
class ProductView:
    """Producto en formato plano, apto para la interfaz y para el agente IA."""

    id: int
    sku: str
    name: str
    product_type: str | None
    category: str | None
    subcategory: str | None
    size: str | None
    color: str | None
    material: str | None
    condition: str | None
    price: float | None
    stock: int
    status: str
    quality_score: int
    description: str | None
    title_override: str | None
    features: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    image_count: int = 0
    images: list[dict[str, Any]] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    template_id: int | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sku": self.sku,
            "nombre": self.name,
            "tipo": self.product_type,
            "categoria": self.category,
            "medida": self.size,
            "color": self.color,
            "material": self.material,
            "estado": self.condition,
            "precio": self.price,
            "stock": self.stock,
            "situacion": self.status,
            "calidad": self.quality_score,
            "fotografias": self.image_count,
            "cuentas": self.accounts,
        }


class CatalogService:
    """Operaciones sobre el catalogo local de productos."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------
    def list_products(self, criteria: ProductFilter | None = None) -> list[ProductView]:
        criteria = criteria or ProductFilter()
        with self._db.session_scope() as session:
            stmt = select(Product)

            if criteria.sku:
                stmt = stmt.where(func.lower(Product.sku) == criteria.sku.lower())
            if criteria.product_type:
                stmt = stmt.where(
                    func.lower(func.coalesce(Product.product_type, "")).like(
                        f"%{criteria.product_type.lower()}%"
                    )
                )
            if criteria.size:
                normalized = normalize_size(criteria.size) or criteria.size
                stmt = stmt.where(Product.size == normalized)
            if criteria.color:
                stmt = stmt.where(func.lower(func.coalesce(Product.color, "")) == criteria.color.lower())
            if criteria.material:
                stmt = stmt.where(
                    func.lower(func.coalesce(Product.material, "")) == criteria.material.lower()
                )
            if criteria.category:
                stmt = stmt.where(
                    func.lower(func.coalesce(Product.category, "")) == criteria.category.lower()
                )
            if criteria.status:
                stmt = stmt.where(Product.status == criteria.status)
            if criteria.min_price is not None:
                stmt = stmt.where(Product.price >= criteria.min_price)
            if criteria.max_price is not None:
                stmt = stmt.where(Product.price <= criteria.max_price)
            if criteria.only_in_stock:
                stmt = stmt.where(Product.stock > 0)
            if criteria.account_ref:
                stmt = (
                    stmt.join(ProductAssignment, ProductAssignment.product_id == Product.id)
                    .join(Account, Account.id == ProductAssignment.account_id)
                    .where(Account.internal_ref == criteria.account_ref)
                    .where(ProductAssignment.enabled.is_(True))
                )

            # Sin filtro de texto podemos limitar en SQL; con el, filtramos
            # despues en Python para ignorar acentos.
            stmt = stmt.order_by(Product.sku)
            if not criteria.text:
                stmt = stmt.limit(criteria.limit)
            products = session.scalars(stmt).unique().all()
            views = [self._to_view(session, product) for product in products]

        if criteria.text:
            views = [
                v
                for v in views
                if contains(v.name, criteria.text)
                or contains(v.sku, criteria.text)
                or contains(v.description, criteria.text)
                or contains(v.product_type, criteria.text)
                or contains(v.size, criteria.text)
                or contains(v.color, criteria.text)
            ][: criteria.limit]
        return views

    def get_product(self, identifier: str | int) -> ProductView | None:
        with self._db.session_scope() as session:
            product = self._find(session, identifier)
            return self._to_view(session, product) if product else None

    def count_products(self) -> dict[str, int]:
        with self._db.session_scope() as session:
            total = session.scalar(select(func.count(Product.id))) or 0
            counts = {
                status.value: session.scalar(
                    select(func.count(Product.id)).where(Product.status == status)
                )
                or 0
                for status in ProductStatus
            }
            counts["total"] = total
            counts["sin_stock"] = session.scalar(
                select(func.count(Product.id)).where(Product.stock <= 0)
            ) or 0
            return counts

    @staticmethod
    def _find(session, identifier: str | int) -> Product | None:
        if isinstance(identifier, int) or str(identifier).isdigit():
            product = session.get(Product, int(identifier))
            if product:
                return product
        text = str(identifier).strip()
        product = session.scalar(select(Product).where(func.lower(Product.sku) == text.lower()))
        if product:
            return product
        return session.scalar(select(Product).where(func.lower(Product.name) == text.lower()))

    def _to_view(self, session, product: Product) -> ProductView:
        account_refs = [
            ref
            for (ref,) in session.execute(
                select(Account.internal_ref)
                .join(ProductAssignment, ProductAssignment.account_id == Account.id)
                .where(ProductAssignment.product_id == product.id)
                .where(ProductAssignment.enabled.is_(True))
            ).all()
        ]
        images = [
            {
                "id": image.id,
                "path": image.path,
                "position": image.position,
                "is_primary": image.is_primary,
                "file_format": image.file_format,
                "content_hash": image.content_hash,
                "perceptual_hash": image.perceptual_hash,
            }
            for image in product.images
        ]
        return ProductView(
            id=product.id,
            sku=product.sku,
            name=product.name,
            product_type=product.product_type,
            category=product.category,
            subcategory=product.subcategory,
            size=product.size,
            color=product.color,
            material=product.material,
            condition=product.condition,
            price=product.price,
            stock=product.stock,
            status=product.status.value,
            quality_score=product.quality_score,
            description=product.description,
            title_override=product.title_override,
            features=dict(product.features or {}),
            tags=list(product.tags or []),
            image_count=len(images),
            images=images,
            accounts=account_refs,
            template_id=product.template_id,
            published_at=product.published_at,
            updated_at=product.updated_at,
        )

    # ------------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------------
    def create_product(self, data: dict[str, Any]) -> ProductView:
        sku = str(data.get("sku") or "").strip()
        name = str(data.get("name") or data.get("nombre") or "").strip()
        if not name:
            raise ValueError("El producto necesita un nombre.")
        with self._db.session_scope() as session:
            if not sku:
                sku = self._generate_sku(session, data)
            if session.scalar(select(Product).where(func.lower(Product.sku) == sku.lower())):
                raise ValueError(f"Ya existe un producto con el SKU '{sku}'.")
            product = Product(
                sku=sku,
                name=name,
                product_type=_clean(data.get("product_type") or data.get("tipo")),
                category=_clean(data.get("category") or data.get("categoria")),
                subcategory=_clean(data.get("subcategory") or data.get("subcategoria")),
                size=normalize_size(data.get("size") or data.get("medida")),
                color=_clean(data.get("color")),
                material=_clean(data.get("material")),
                condition=_clean(data.get("condition") or data.get("estado")) or "Nuevo",
                price=_as_float(data.get("price") if "price" in data else data.get("precio")),
                stock=int(data.get("stock") or 0),
                description=_clean(data.get("description") or data.get("descripcion")),
                title_override=_clean(data.get("title_override")),
                features=dict(data.get("features") or data.get("caracteristicas") or {}),
                tags=list(data.get("tags") or data.get("etiquetas") or []),
                template_id=data.get("template_id"),
                status=_status(data.get("status")) or ProductStatus.DRAFT,
            )
            _sync_feature_fields(product)
            session.add(product)
            session.flush()
            logger.info("Producto creado: %s (%s)", product.name, product.sku)
            return self._to_view(session, product)

    def update_product(self, identifier: str | int, changes: dict[str, Any]) -> ProductView:
        field_map = {
            "nombre": "name",
            "tipo": "product_type",
            "categoria": "category",
            "subcategoria": "subcategory",
            "medida": "size",
            "estado": "condition",
            "precio": "price",
            "descripcion": "description",
            "caracteristicas": "features",
            "etiquetas": "tags",
        }
        with self._db.session_scope() as session:
            product = self._find(session, identifier)
            if product is None:
                raise ValueError(f"No se encuentra el producto '{identifier}'.")
            for raw_key, value in changes.items():
                key = field_map.get(raw_key, raw_key)
                if key == "size":
                    value = normalize_size(value)
                elif key == "price":
                    value = _as_float(value)
                elif key == "stock":
                    value = int(value or 0)
                elif key == "status" and isinstance(value, str):
                    value = ProductStatus(value)
                if hasattr(product, key) and key not in {"id", "sku", "created_at"}:
                    setattr(product, key, value)
            _sync_feature_fields(product)
            session.flush()
            logger.info("Producto actualizado: %s", product.sku)
            return self._to_view(session, product)

    def update_stock(self, identifier: str | int, stock: int) -> ProductView:
        return self.update_product(identifier, {"stock": max(0, int(stock))})

    def set_status(self, identifier: str | int, status: ProductStatus) -> ProductView:
        changes: dict[str, Any] = {"status": status}
        if status is ProductStatus.PUBLISHED:
            changes["published_at"] = datetime.now(UTC).replace(tzinfo=None)
        return self.update_product(identifier, changes)

    def delete_product(self, identifier: str | int) -> bool:
        with self._db.session_scope() as session:
            product = self._find(session, identifier)
            if product is None:
                return False
            session.delete(product)
            logger.info("Producto eliminado: %s", identifier)
            return True

    # ------------------------------------------------------------------
    # Asignacion a cuentas
    # ------------------------------------------------------------------
    def assign_to_accounts(
        self, identifier: str | int, account_refs: list[str], replace: bool = False
    ) -> list[str]:
        """Asigna un producto a una o varias cuentas. Devuelve las cuentas activas."""
        with self._db.session_scope() as session:
            product = self._find(session, identifier)
            if product is None:
                raise ValueError(f"No se encuentra el producto '{identifier}'.")
            accounts = session.scalars(
                select(Account).where(Account.internal_ref.in_(account_refs))
            ).all()
            found_refs = {a.internal_ref for a in accounts}
            unknown = set(account_refs) - found_refs
            if unknown:
                raise ValueError(f"Cuentas desconocidas: {', '.join(sorted(unknown))}")

            if replace:
                for assignment in list(product.assignments):
                    session.delete(assignment)
                session.flush()

            existing = {a.account_id for a in product.assignments}
            for account in accounts:
                if account.id in existing:
                    continue
                session.add(
                    ProductAssignment(product_id=product.id, account_id=account.id, enabled=True)
                )
            session.flush()
            session.refresh(product)
            return [
                ref
                for (ref,) in session.execute(
                    select(Account.internal_ref)
                    .join(ProductAssignment, ProductAssignment.account_id == Account.id)
                    .where(ProductAssignment.product_id == product.id)
                    .where(ProductAssignment.enabled.is_(True))
                ).all()
            ]

    def unassign_from_account(self, identifier: str | int, account_ref: str) -> bool:
        with self._db.session_scope() as session:
            product = self._find(session, identifier)
            account = session.scalar(select(Account).where(Account.internal_ref == account_ref))
            if product is None or account is None:
                return False
            assignment = session.scalar(
                select(ProductAssignment)
                .where(ProductAssignment.product_id == product.id)
                .where(ProductAssignment.account_id == account.id)
            )
            if assignment is None:
                return False
            session.delete(assignment)
            return True

    # ------------------------------------------------------------------
    # Imagenes
    # ------------------------------------------------------------------
    def add_image(self, identifier: str | int, image_data: dict[str, Any]) -> int:
        with self._db.session_scope() as session:
            product = self._find(session, identifier)
            if product is None:
                raise ValueError(f"No se encuentra el producto '{identifier}'.")
            position = len(product.images)
            image = ProductImage(
                product_id=product.id,
                path=str(image_data["path"]),
                original_name=image_data.get("original_name"),
                position=image_data.get("position", position),
                is_primary=bool(image_data.get("is_primary", position == 0)),
                width=image_data.get("width"),
                height=image_data.get("height"),
                file_format=image_data.get("file_format"),
                size_bytes=image_data.get("size_bytes"),
                content_hash=image_data.get("content_hash"),
                perceptual_hash=image_data.get("perceptual_hash"),
                source=image_data.get("source", "import"),
            )
            session.add(image)
            session.flush()
            return image.id

    def reorder_images(self, identifier: str | int, ordered_image_ids: list[int]) -> bool:
        with self._db.session_scope() as session:
            product = self._find(session, identifier)
            if product is None:
                return False
            by_id = {image.id: image for image in product.images}
            for position, image_id in enumerate(ordered_image_ids):
                image = by_id.get(image_id)
                if image is not None:
                    image.position = position
            return True

    def set_primary_image(self, identifier: str | int, image_id: int) -> bool:
        with self._db.session_scope() as session:
            product = self._find(session, identifier)
            if product is None:
                return False
            found = False
            for image in product.images:
                image.is_primary = image.id == image_id
                found = found or image.is_primary
            return found

    def remove_image(self, image_id: int) -> bool:
        with self._db.session_scope() as session:
            image = session.get(ProductImage, image_id)
            if image is None:
                return False
            session.delete(image)
            return True

    # ------------------------------------------------------------------
    # Calidad y duplicados
    # ------------------------------------------------------------------
    def validate_product(self, identifier: str | int, rendered: dict[str, Any] | None = None) -> QualityReport:
        """Valida un producto. `rendered` permite validar el texto ya generado."""
        view = self.get_product(identifier)
        if view is None:
            raise ValueError(f"No se encuentra el producto '{identifier}'.")
        features = dict(view.features)
        features.setdefault("medida", view.size)
        features.setdefault("color", view.color)
        features.setdefault("material", view.material)
        features.setdefault("estado", view.condition)
        report = validate_listing_data(
            title=(rendered or {}).get("title") or view.title_override or view.name,
            description=(rendered or {}).get("description") or view.description,
            price=view.price,
            category=view.category,
            condition=view.condition,
            features=features,
            images=view.images,
            subject=f"{view.sku} · {view.name}",
        )
        self._store_quality(view.id, report.score)
        return report

    def validate_all(self, criteria: ProductFilter | None = None) -> list[QualityReport]:
        return [self.validate_product(view.id) for view in self.list_products(criteria)]

    def _store_quality(self, product_id: int, score: int) -> None:
        with self._db.session_scope() as session:
            product = session.get(Product, product_id)
            if product is not None:
                product.quality_score = score

    def find_duplicate_products(self) -> list[DuplicateGroup]:
        records: list[dict[str, Any]] = []
        for view in self.list_products(ProductFilter(limit=5000)):
            records.append(
                {
                    "id": view.id,
                    "titulo": view.title_override or view.name,
                    "sku": view.sku,
                    "caracteristicas": {
                        "medida": view.size,
                        "color": view.color,
                        "material": view.material,
                    },
                    "image_hashes": [
                        image.get("content_hash") for image in view.images if image.get("content_hash")
                    ],
                    "cuentas": view.accounts,
                }
            )
        return find_duplicates(records)

    # ------------------------------------------------------------------
    @staticmethod
    def _generate_sku(session, data: dict[str, Any]) -> str:
        base_parts = [
            str(data.get("product_type") or data.get("tipo") or data.get("name") or "PROD")[:4],
            normalize_size(data.get("size") or data.get("medida")) or "",
            str(data.get("color") or "")[:3],
        ]
        base = "-".join(p.upper().replace(" ", "") for p in base_parts if p)
        base = re.sub(r"[^A-Z0-9\-]", "", base) or "PROD"
        candidate = base
        counter = 1
        while session.scalar(select(Product).where(func.lower(Product.sku) == candidate.lower())):
            counter += 1
            candidate = f"{base}-{counter}"
        return candidate


def _status(value: Any) -> ProductStatus | None:
    """Acepta el estado como enumerado o como texto («ready»)."""
    if isinstance(value, ProductStatus):
        return value
    try:
        return ProductStatus(str(value)) if value else None
    except ValueError:
        return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("€", "").replace(",", ".").strip())
    except ValueError:
        return None


def _sync_feature_fields(product: Product) -> None:
    """Mantiene `features` alineado con los campos estructurados."""
    features = dict(product.features or {})
    for key, value in (
        ("medida", product.size),
        ("color", product.color),
        ("material", product.material),
        ("estado", product.condition),
    ):
        if value:
            features[key] = value
    product.features = features
