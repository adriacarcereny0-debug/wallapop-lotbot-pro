"""Consulta y sincronizacion de anuncios locales.

Los anuncios se guardan en local como espejo de lo que hay en Wallapop, para
poder buscar, analizar y detectar duplicados sin castigar la API.

AISLAMIENTO: toda consulta lleva `account_id`. Nunca se devuelven anuncios de
una cuenta cuando se pregunta por otra.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from lot_bot.catalog.duplicates import DuplicateGroup, find_duplicates
from lot_bot.catalog.validation import QualityReport, validate_listing_data
from lot_bot.core.text import contains, fold
from lot_bot.database.engine import Database
from lot_bot.database.models import Account, Listing, ListingStatus
from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.dto import Item
from lot_bot.wallapop.service import WallapopService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ListingView:
    """Anuncio en formato plano para la interfaz y el agente IA."""

    id: int
    account_ref: str
    account_alias: str
    wallapop_item_id: str | None
    title: str
    description: str | None
    price: float | None
    currency: str
    category: str | None
    status: str
    views: int
    favorites: int
    product_sku: str | None
    attributes: dict[str, Any] = field(default_factory=dict)
    image_urls: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    last_synced_at: datetime | None = None
    #: Anuncio principal del que procede (si procede de uno).
    master_ad_id: int | None = None
    master_ad_name: str | None = None
    #: Cambios propios de esta publicación (no afectan a la plantilla).
    overrides: dict[str, Any] = field(default_factory=dict)
    #: True si pertenece a una cuenta de demostración.
    is_demo: bool = False
    url: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cuenta": self.account_alias,
            "demo": self.is_demo,
            "plantilla": self.master_ad_name,
            "cuenta_ref": self.account_ref,
            "anuncio_wallapop": self.wallapop_item_id,
            "titulo": self.title,
            "precio": self.price,
            "moneda": self.currency,
            "estado": self.status,
            "visitas": self.views,
            "favoritos": self.favorites,
            "sku": self.product_sku,
            "caracteristicas": self.attributes,
            "fotografias": len(self.image_urls),
        }


@dataclass(slots=True)
class ListingFilter:
    text: str | None = None
    account_ref: str | None = None
    account_refs: list[str] = field(default_factory=list)
    size: str | None = None
    color: str | None = None
    status: ListingStatus | None = None
    min_price: float | None = None
    max_price: float | None = None
    product_sku: str | None = None
    limit: int = 500

    def describe(self) -> str:
        parts: list[str] = []
        if self.text:
            parts.append(f"texto '{self.text}'")
        if self.size:
            parts.append(f"medida {self.size}")
        if self.color:
            parts.append(f"color {self.color}")
        if self.min_price is not None:
            parts.append(f"precio ≥ {self.min_price:.2f} €")
        if self.max_price is not None:
            parts.append(f"precio ≤ {self.max_price:.2f} €")
        if self.account_ref:
            parts.append(f"cuenta {self.account_ref}")
        if self.account_refs:
            parts.append(f"cuentas {', '.join(self.account_refs)}")
        if self.status:
            parts.append(f"estado {self.status.value}")
        return ", ".join(parts) or "todos los anuncios"


class ListingService:
    """Espejo local de los anuncios de Wallapop."""

    def __init__(self, database: Database, wallapop: WallapopService) -> None:
        self._db = database
        self._wallapop = wallapop

    def set_backend(self, wallapop: WallapopService) -> None:
        self._wallapop = wallapop

    # ------------------------------------------------------------------
    # Consulta local
    # ------------------------------------------------------------------
    def search(self, criteria: ListingFilter | None = None) -> list[ListingView]:
        criteria = criteria or ListingFilter()
        with self._db.session_scope() as session:
            stmt = select(Listing, Account).join(Account, Account.id == Listing.account_id)

            refs = list(criteria.account_refs)
            if criteria.account_ref:
                refs.append(criteria.account_ref)
            if refs:
                stmt = stmt.where(Account.internal_ref.in_(refs))
            if criteria.status:
                stmt = stmt.where(Listing.status == criteria.status)
            if criteria.min_price is not None:
                stmt = stmt.where(Listing.price >= criteria.min_price)
            if criteria.max_price is not None:
                stmt = stmt.where(Listing.price <= criteria.max_price)

            python_filters = any(
                (criteria.text, criteria.size, criteria.color, criteria.product_sku)
            )
            stmt = stmt.order_by(Account.id, Listing.title)
            if not python_filters:
                stmt = stmt.limit(criteria.limit)
            rows = session.execute(stmt).all()
            views = [self._to_view(listing, account) for listing, account in rows]

        # El filtro de texto se aplica en Python para que sea insensible a los
        # acentos: «canapé» debe encontrar «canape» y viceversa.
        if criteria.text:
            views = [
                v
                for v in views
                if contains(v.title, criteria.text)
                or contains(v.description, criteria.text)
                or contains(str(v.attributes), criteria.text)
            ]

        # Filtros sobre atributos JSON (mas comodo en Python que en SQL).
        if criteria.size:
            target = fold(criteria.size).replace(" ", "")
            views = [
                v
                for v in views
                if target in fold(v.attributes.get("medida", "")).replace(" ", "")
                or target in fold(v.title).replace(" ", "")
            ]
        if criteria.color:
            target = fold(criteria.color)
            views = [
                v
                for v in views
                if target in fold(v.attributes.get("color", "")) or target in fold(v.title)
            ]
        if criteria.product_sku:
            views = [v for v in views if (v.product_sku or "").lower() == criteria.product_sku.lower()]
        return views[: criteria.limit]

    def get(self, listing_id: int) -> ListingView | None:
        with self._db.session_scope() as session:
            row = session.execute(
                select(Listing, Account)
                .join(Account, Account.id == Listing.account_id)
                .where(Listing.id == listing_id)
            ).first()
            if row is None:
                return None
            return self._to_view(row[0], row[1])

    def count_by_account(self) -> dict[str, int]:
        with self._db.session_scope() as session:
            rows = session.execute(
                select(Account.internal_ref, func.count(Listing.id))
                .join(Listing, Listing.account_id == Account.id, isouter=True)
                .group_by(Account.internal_ref)
            ).all()
            return {ref: count for ref, count in rows}

    def stats(self) -> dict[str, Any]:
        with self._db.session_scope() as session:
            total = session.scalar(select(func.count(Listing.id))) or 0
            active = session.scalar(
                select(func.count(Listing.id)).where(Listing.status == ListingStatus.ACTIVE)
            ) or 0
            avg_price = session.scalar(select(func.avg(Listing.price))) or 0.0
            views = session.scalar(select(func.sum(Listing.views))) or 0
            return {
                "total": total,
                "activos": active,
                "precio_medio": round(float(avg_price), 2),
                "visitas": int(views),
            }

    @staticmethod
    def _to_view(listing: Listing, account: Account) -> ListingView:
        alias = account.alias
        # Una cuenta de demostración siempre se ve como tal, se llame como se
        # llame: nunca debe parecer una cuenta real de Wallapop.
        if account.is_demo and "demo" not in alias.lower():
            alias = f"{alias} (DEMO)"
        return ListingView(
            id=listing.id,
            account_ref=account.internal_ref,
            account_alias=alias,
            wallapop_item_id=listing.wallapop_item_id,
            title=listing.title,
            description=listing.description,
            price=listing.price,
            currency=listing.currency,
            category=listing.category,
            status=listing.status.value,
            views=listing.views,
            favorites=listing.favorites,
            product_sku=listing.product.sku if listing.product else None,
            attributes=dict(listing.attributes or {}),
            image_urls=list(listing.image_urls or []),
            published_at=listing.published_at,
            last_synced_at=listing.last_synced_at,
            master_ad_id=listing.master_ad_id,
            master_ad_name=listing.master_ad.name if listing.master_ad else None,
            overrides=dict(listing.overrides or {}),
            is_demo=account.is_demo,
            url=listing.url,
            meta=dict(listing.meta or {}),
        )

    # ------------------------------------------------------------------
    # Sincronizacion con Wallapop
    # ------------------------------------------------------------------
    def sync_account(self, account_ref: str, limit: int = 200) -> dict[str, int]:
        """Descarga los anuncios de UNA cuenta y actualiza el espejo local."""
        self._wallapop.require(Capability.LIST_ITEMS)
        items = self._wallapop.list_items(account_ref, limit=limit)
        created = updated = 0
        now = datetime.now(UTC).replace(tzinfo=None)

        with self._db.session_scope() as session:
            account = session.scalar(select(Account).where(Account.internal_ref == account_ref))
            if account is None:
                raise ValueError(f"Cuenta '{account_ref}' no encontrada.")

            seen: set[str] = set()
            # Anuncios publicados por navegador cuya dirección Wallapop no
            # mostró: se emparejan por título (y precio más cercano).
            pending = list(
                session.scalars(
                    select(Listing)
                    .where(Listing.account_id == account.id)
                    .where(Listing.wallapop_item_id.like("navegador-%"))
                    .where(Listing.status != ListingStatus.REMOVED)
                    .order_by(Listing.id)
                ).all()
            )
            for item in items:
                seen.add(item.item_id)
                listing = session.scalar(
                    select(Listing)
                    .where(Listing.account_id == account.id)
                    .where(Listing.wallapop_item_id == item.item_id)
                )
                if listing is None:
                    listing = _match_pending(pending, item)
                    if listing is not None:
                        pending.remove(listing)
                        listing.wallapop_item_id = item.item_id
                if listing is None:
                    listing = Listing(account_id=account.id, wallapop_item_id=item.item_id)
                    session.add(listing)
                    created += 1
                else:
                    updated += 1
                _apply_item(listing, item)
                listing.last_synced_at = now

            # Los anuncios que ya no aparecen se marcan como retirados,
            # nunca se borran del historial local sin avisar.
            # Por navegador, una lista vacía puede ser un fallo al leerla: no
            # se da nada por retirado sin haber visto al menos un anuncio.
            browser = getattr(self._wallapop, "backend_name", "").endswith("(NAVEGADOR)")
            missing = [] if (browser and not items) else session.scalars(
                select(Listing)
                .where(Listing.account_id == account.id)
                .where(Listing.wallapop_item_id.notin_(seen or {"__none__"}))
                .where(Listing.status != ListingStatus.REMOVED)
            ).all()
            for listing in missing:
                listing.status = ListingStatus.REMOVED
                listing.status_detail = "Ya no aparece en Wallapop."

            account.last_sync_at = now

        logger.info(
            "Sincronizacion de '%s': %d nuevos, %d actualizados, %d retirados.",
            account_ref,
            created,
            updated,
            len(missing),
        )
        return {"nuevos": created, "actualizados": updated, "retirados": len(missing)}

    def sync_all(self, account_refs: list[str]) -> dict[str, dict[str, int]]:
        results: dict[str, dict[str, int]] = {}
        for ref in account_refs:
            try:
                results[ref] = self.sync_account(ref)
            except Exception as exc:  # se registra y se continua con el resto
                logger.warning("Fallo al sincronizar '%s': %s", ref, exc)
                results[ref] = {"error": 1}
        return results

    def register_published(
        self,
        account_ref: str,
        item_id: str,
        product_id: int | None,
        data: dict[str, Any],
        master_ad_id: int | None = None,
        overrides: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int:
        """Guarda en local un anuncio recien publicado."""
        with self._db.session_scope() as session:
            account = session.scalar(select(Account).where(Account.internal_ref == account_ref))
            if account is None:
                raise ValueError(f"Cuenta '{account_ref}' no encontrada.")
            listing = session.scalar(
                select(Listing)
                .where(Listing.account_id == account.id)
                .where(Listing.wallapop_item_id == item_id)
            )
            if listing is None:
                listing = Listing(account_id=account.id, wallapop_item_id=item_id)
                session.add(listing)
            listing.product_id = product_id
            listing.master_ad_id = master_ad_id
            listing.overrides = dict(overrides or {})
            listing.title = data.get("title", "")
            listing.description = data.get("description")
            listing.price = data.get("price")
            listing.currency = data.get("currency", "EUR")
            listing.category = data.get("category")
            listing.condition = data.get("condition")
            listing.attributes = dict(data.get("attributes") or {})
            listing.image_urls = list(data.get("image_urls") or [])
            listing.status = ListingStatus.ACTIVE
            listing.url = data.get("url") or listing.url
            listing.meta = dict(meta or {})
            listing.published_at = datetime.now(UTC).replace(tzinfo=None)
            listing.last_synced_at = listing.published_at
            session.flush()
            return listing.id

    def apply_local_price(self, listing_id: int, price: float) -> None:
        self.apply_local_changes(listing_id, {"price": price})

    def apply_local_changes(self, listing_id: int, changes: dict[str, Any]) -> None:
        allowed = {"title", "description", "price", "category", "condition", "attributes", "image_urls"}
        with self._db.session_scope() as session:
            listing = session.get(Listing, listing_id)
            if listing is None:
                return
            applied = {}
            for key, value in changes.items():
                if key in allowed:
                    setattr(listing, key, value)
                    applied[key] = value
            if listing.master_ad_id and applied:
                # Queda constancia de que esta publicación se aparta de la
                # plantilla, sin tocar la plantilla.
                overrides = dict(listing.overrides or {})
                overrides.update({k: v for k, v in applied.items() if k != "image_urls"})
                listing.overrides = overrides

    def mark_removed(self, listing_id: int) -> None:
        with self._db.session_scope() as session:
            listing = session.get(Listing, listing_id)
            if listing is not None:
                listing.status = ListingStatus.REMOVED
                listing.status_detail = "Eliminado desde LOT Bot."

    # ------------------------------------------------------------------
    # Calidad y duplicados sobre anuncios ya publicados
    # ------------------------------------------------------------------
    def quality_reports(self, criteria: ListingFilter | None = None) -> list[QualityReport]:
        reports: list[QualityReport] = []
        for view in self.search(criteria):
            reports.append(
                validate_listing_data(
                    title=view.title,
                    description=view.description,
                    price=view.price,
                    category=view.category,
                    condition=view.attributes.get("estado"),
                    features=view.attributes,
                    images=[{"file_format": "JPEG", "is_primary": i == 0} for i in range(len(view.image_urls))],
                    subject=f"{view.account_alias} · {view.title}",
                )
            )
        return reports

    def find_duplicates(self, criteria: ListingFilter | None = None) -> list[DuplicateGroup]:
        records = [
            {
                "id": view.id,
                "titulo": view.title,
                "sku": view.product_sku,
                "wallapop_item_id": None,  # el mismo id en cuentas distintas no aplica
                "caracteristicas": view.attributes,
                "cuenta": view.account_alias,
                "cuenta_ref": view.account_ref,
                "precio": view.price,
                "plantilla": view.master_ad_name,
            }
            for view in self.search(criteria)
        ]
        return find_duplicates(records)


def _norm_text(text: str) -> str:
    import unicodedata

    plain = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return " ".join(plain.lower().split())


def _match_pending(pending: list[Listing], item: Item) -> Listing | None:
    """El anuncio local sin dirección que corresponde a esta ficha: mismo
    título (dentro del texto de la ficha) y, entre varios, el precio más cercano."""
    card = _norm_text(item.raw.get("texto") or item.title)
    candidates = [lst for lst in pending if lst.title and _norm_text(lst.title) in card]
    if not candidates:
        return None
    if item.price is None:
        return candidates[0]
    return min(candidates, key=lambda lst: abs((lst.price or 0) - item.price))


def _apply_item(listing: Listing, item: Item) -> None:
    if item.raw.get("parcial"):
        # Leído de la lista del navegador: solo lo que se ve (dirección,
        # precio y que sigue a la venta). El resto se conserva.
        listing.url = item.raw.get("url") or listing.url
        if item.price is not None:
            listing.price = item.price
        if not listing.title:
            listing.title = item.title
        listing.status = ListingStatus.ACTIVE
        return
    listing.title = item.title
    listing.description = item.description
    listing.price = item.price
    listing.currency = item.currency
    listing.category = item.category
    listing.condition = item.condition
    listing.attributes = dict(item.attributes)
    listing.image_urls = item.image_urls
    listing.views = item.views
    listing.favorites = item.favorites
    try:
        listing.status = ListingStatus(item.status)
    except ValueError:
        listing.status = ListingStatus.ACTIVE
