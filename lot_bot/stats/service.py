"""Estadísticas de los anuncios: mediciones reales y su histórico.

REGLAS
------
* Solo se guarda lo que se ha leído de verdad. Si un dato no se ha podido
  obtener, se guarda `None` y la interfaz muestra «No disponible». Nunca se
  rellena con ceros ni estimaciones.
* Cada lectura queda en el histórico (`ListingStat`) para comparar la
  evolución de los anuncios a lo largo del tiempo.
* En DEMO las cifras son simuladas y se marcan como tales.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

from lot_bot.database.engine import Database
from lot_bot.database.models import Account, Listing, ListingStat, ListingStatus
from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.errors import (
    AuthenticationError,
    NotAvailableWithCurrentAccessError,
    VerificationRequiredError,
    WallapopError,
)

logger = logging.getLogger(__name__)

NOT_AVAILABLE = "No disponible"
SORT_KEYS = {"visualizaciones", "favoritos", "fecha", "rendimiento"}


def show(value: Any) -> str:
    """Texto para la interfaz: el número o «No disponible»."""
    return NOT_AVAILABLE if value is None else str(value)


@dataclass(slots=True)
class ListingStats:
    """Última situación conocida de un anuncio, con su histórico resumido."""

    listing_id: int
    account_ref: str
    account_alias: str
    title: str
    url: str | None
    status: str
    published_at: datetime | None
    views: int | None
    favorites: int | None
    captured_at: datetime | None
    measurements: int
    source: str | None
    is_demo: bool
    meta: dict[str, Any] = field(default_factory=dict)
    views_change: int | None = None
    favorites_change: int | None = None

    @property
    def days_online(self) -> float | None:
        if self.published_at is None:
            return None
        reference = self.captured_at or datetime.now()
        return max((reference - self.published_at).total_seconds() / 86400, 0.25)

    @property
    def views_per_day(self) -> float | None:
        days = self.days_online
        if self.views is None or days is None:
            return None
        return round(self.views / days, 2)

    @property
    def favorites_rate(self) -> float | None:
        """Favoritos por cada 100 visualizaciones."""
        if self.views is None or self.favorites is None or self.views == 0:
            return None
        return round(100 * self.favorites / self.views, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "anuncio": self.listing_id,
            "cuenta": self.account_alias,
            "cuenta_ref": self.account_ref,
            "titulo": self.title,
            "url": self.url,
            "estado": self.status,
            "publicado": self.published_at.strftime("%d/%m/%Y %H:%M") if self.published_at else None,
            "visualizaciones": self.views,
            "favoritos": self.favorites,
            "visualizaciones_dia": self.views_per_day,
            "favoritos_por_100_visitas": self.favorites_rate,
            "mediciones": self.measurements,
            "ultima_medicion": self.captured_at.strftime("%d/%m/%Y %H:%M")
            if self.captured_at
            else None,
            "origen_datos": "simulado (DEMO)" if self.is_demo or self.source == "demo" else self.source,
            "imagen": (self.meta or {}).get("imagen"),
        }


class StatsService:
    def __init__(self, database: Database, app: Any) -> None:
        self._db = database
        self._app = app

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._app.wallapop.supports(Capability.ITEM_STATS)

    def availability_note(self) -> str:
        if self._app.demo_mode:
            return "MODO DEMO: las estadísticas son simuladas, no son datos de Wallapop."
        if not self.available:
            return (
                "La integración actual no puede leer estadísticas de Wallapop: se muestran "
                "como «No disponible»."
            )
        return (
            "Se leen de la página pública de cada anuncio. Si Wallapop no muestra un "
            "dato, aparece «No disponible»."
        )

    # ------------------------------------------------------------------
    # Lectura (captura) de estadísticas
    # ------------------------------------------------------------------
    def capture(self, account_refs: list[str] | None = None) -> dict[str, Any]:
        """Lee las estadísticas actuales y las añade al histórico."""
        summary = {"leidos": 0, "sin_datos": 0, "omitidos": 0, "errores": []}
        if not self.available:
            summary["errores"].append(self.availability_note())
            return summary
        wallapop = self._app.wallapop
        is_mock = bool(getattr(wallapop, "is_mock", False))
        blocked: set[str] = set()
        for listing in self._listings(account_refs):
            ref = listing["account_ref"]
            if ref in blocked:
                summary["omitidos"] += 1
                continue
            target = listing["item_id"] if is_mock else listing["url"]
            if not target:
                summary["omitidos"] += 1  # sin dirección no se puede leer
                continue
            try:
                values = wallapop.get_item_stats(ref, target)
            except (AuthenticationError, VerificationRequiredError) as exc:
                blocked.add(ref)
                summary["errores"].append(f"{ref}: {exc.user_message}")
                continue
            except (NotAvailableWithCurrentAccessError, WallapopError) as exc:
                summary["errores"].append(f"{listing['title']}: {exc.user_message}")
                continue
            views, favorites = values.get("views"), values.get("favorites")
            self.record(
                listing["id"],
                ref,
                views=views,
                favorites=favorites,
                status=listing["status"],
                source=values.get("source") or ("demo" if is_mock else "navegador"),
            )
            if views is None and favorites is None:
                summary["sin_datos"] += 1
            else:
                summary["leidos"] += 1
        return summary

    def record(
        self,
        listing_id: int,
        account_ref: str,
        *,
        views: int | None,
        favorites: int | None,
        status: str | None,
        source: str,
        captured_at: datetime | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        with self._db.session_scope() as session:
            session.add(
                ListingStat(
                    listing_id=listing_id,
                    account_ref=account_ref,
                    captured_at=captured_at or datetime.now(),
                    views=views,
                    favorites=favorites,
                    status=status,
                    source=source,
                    extra=dict(extra or {}),
                )
            )
            listing = session.get(Listing, listing_id)
            if listing is not None:
                # Solo se actualiza el dato que se ha leído de verdad.
                if views is not None:
                    listing.views = views
                if favorites is not None:
                    listing.favorites = favorites

    def _listings(self, account_refs: list[str] | None) -> list[dict[str, Any]]:
        with self._db.session_scope() as session:
            query = (
                select(Listing, Account)
                .join(Account, Listing.account_id == Account.id)
                .where(Listing.status.in_([ListingStatus.ACTIVE, ListingStatus.INACTIVE]))
            )
            if account_refs:
                query = query.where(Account.internal_ref.in_(account_refs))
            if not self._app.demo_mode:
                query = query.where(Account.is_demo.is_(False))
            return [
                {
                    "id": listing.id,
                    "account_ref": account.internal_ref,
                    "item_id": listing.wallapop_item_id,
                    "url": listing.url,
                    "title": listing.title,
                    "status": listing.status.value,
                }
                for listing, account in session.execute(query).all()
            ]

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------
    def table(
        self,
        account_ref: str | None = None,
        sort_by: str = "visualizaciones",
        descending: bool = True,
    ) -> list[ListingStats]:
        """Cuenta → anuncio → visualizaciones → favoritos → rendimiento."""
        with self._db.session_scope() as session:
            query = select(Listing, Account).join(Account, Listing.account_id == Account.id)
            if account_ref:
                query = query.where(Account.internal_ref == account_ref)
            if not self._app.demo_mode:
                query = query.where(Account.is_demo.is_(False))
            rows: list[ListingStats] = []
            for listing, account in session.execute(query).all():
                history = session.scalars(
                    select(ListingStat)
                    .where(ListingStat.listing_id == listing.id)
                    .order_by(ListingStat.captured_at)
                ).all()
                last_views = next((h for h in reversed(history) if h.views is not None), None)
                last_favs = next((h for h in reversed(history) if h.favorites is not None), None)
                first_views = next((h for h in history if h.views is not None), None)
                first_favs = next((h for h in history if h.favorites is not None), None)
                latest = history[-1] if history else None
                rows.append(
                    ListingStats(
                        listing_id=listing.id,
                        account_ref=account.internal_ref,
                        account_alias=account.alias,
                        title=listing.title,
                        url=listing.url,
                        status=listing.status.value,
                        published_at=listing.published_at,
                        views=last_views.views if last_views else None,
                        favorites=last_favs.favorites if last_favs else None,
                        captured_at=latest.captured_at if latest else None,
                        measurements=len(history),
                        source=latest.source if latest else None,
                        is_demo=account.is_demo,
                        meta=dict(listing.meta or {}),
                        views_change=(last_views.views - first_views.views)
                        if last_views and first_views and last_views is not first_views
                        else None,
                        favorites_change=(last_favs.favorites - first_favs.favorites)
                        if last_favs and first_favs and last_favs is not first_favs
                        else None,
                    )
                )
        return sort_stats(rows, sort_by, descending)

    def history(self, listing_id: int) -> list[dict[str, Any]]:
        with self._db.session_scope() as session:
            rows = session.scalars(
                select(ListingStat)
                .where(ListingStat.listing_id == listing_id)
                .order_by(ListingStat.captured_at)
            ).all()
            return [
                {
                    "fecha": r.captured_at,
                    "visualizaciones": r.views,
                    "favoritos": r.favorites,
                    "estado": r.status,
                    "origen": r.source,
                }
                for r in rows
            ]


def sort_stats(rows: list[ListingStats], sort_by: str, descending: bool = True) -> list[ListingStats]:
    """Ordena; los «No disponible» van siempre al final."""
    if sort_by not in SORT_KEYS:
        raise ValueError(f"No se puede ordenar por «{sort_by}».")
    key = {
        "visualizaciones": lambda r: r.views,
        "favoritos": lambda r: r.favorites,
        "fecha": lambda r: r.published_at,
        "rendimiento": lambda r: r.views_per_day,
    }[sort_by]
    known = [r for r in rows if key(r) is not None]
    unknown = [r for r in rows if key(r) is None]
    return sorted(known, key=key, reverse=descending) + unknown
