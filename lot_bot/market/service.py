"""Analisis de precios y de mercado.

FUENTES PERMITIDAS
------------------
1. Datos propios: los anuncios del propio cliente ya sincronizados (siempre
   disponibles, tambien en DEMO).
2. Datos de mercado de la API autorizada, si la integracion concede la
   operacion `market_data`.
3. Ficheros de datos que aporte el cliente (CSV con precios de proveedor).

LOT Bot NO hace scraping de Wallapop ni de ninguna otra plataforma. Si una
consulta necesita una fuente que no tenemos, se responde con
MARKET_SOURCE_REQUIRED_MESSAGE en lugar de inventar cifras.
"""

from __future__ import annotations

import csv
import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lot_bot.publishing.listings import ListingFilter, ListingService
from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.errors import WallapopError
from lot_bot.wallapop.service import WallapopService

logger = logging.getLogger(__name__)

MARKET_SOURCE_REQUIRED_MESSAGE = (
    "Esta función requiere una fuente/API autorizada. "
    "LOT Bot no extrae datos de Wallapop ni de otras plataformas sin permiso expreso. "
    "Puedes activarla concediendo la operación 'market_data' en la integración "
    "o importando un fichero de precios propio."
)


@dataclass(slots=True)
class PriceStats:
    """Estadisticas de un conjunto de precios."""

    count: int
    minimum: float
    maximum: float
    average: float
    median: float
    p25: float
    p75: float

    @classmethod
    def from_prices(cls, prices: list[float]) -> PriceStats | None:
        clean = sorted(p for p in prices if p is not None and p > 0)
        if not clean:
            return None
        quantiles = (
            statistics.quantiles(clean, n=4) if len(clean) >= 4 else [clean[0], statistics.median(clean), clean[-1]]
        )
        return cls(
            count=len(clean),
            minimum=clean[0],
            maximum=clean[-1],
            average=round(statistics.fmean(clean), 2),
            median=round(statistics.median(clean), 2),
            p25=round(quantiles[0], 2),
            p75=round(quantiles[2] if len(quantiles) > 2 else quantiles[-1], 2),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "muestras": self.count,
            "minimo": self.minimum,
            "maximo": self.maximum,
            "medio": self.average,
            "mediana": self.median,
            "percentil_25": self.p25,
            "percentil_75": self.p75,
        }


@dataclass(slots=True)
class MarketAnalysis:
    """Resultado de un analisis de mercado."""

    query: str
    sources: list[str] = field(default_factory=list)
    own_stats: PriceStats | None = None
    market_stats: PriceStats | None = None
    positioning: str = ""
    recommendations: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_external_data(self) -> bool:
        return self.market_stats is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "consulta": self.query,
            "fuentes": self.sources,
            "mis_precios": self.own_stats.to_dict() if self.own_stats else None,
            "mercado": self.market_stats.to_dict() if self.market_stats else None,
            "posicionamiento": self.positioning,
            "recomendaciones": self.recommendations,
            "limitaciones": self.limitations,
        }

    def summary(self) -> str:
        lines = [f"Análisis de mercado: {self.query}"]
        if self.own_stats:
            lines.append(
                f"Mis anuncios ({self.own_stats.count}): "
                f"{self.own_stats.minimum:.2f} € – {self.own_stats.maximum:.2f} € "
                f"(medio {self.own_stats.average:.2f} €)"
            )
        if self.market_stats:
            lines.append(
                f"Mercado ({self.market_stats.count}): "
                f"{self.market_stats.minimum:.2f} € – {self.market_stats.maximum:.2f} € "
                f"(mediana {self.market_stats.median:.2f} €)"
            )
        if self.positioning:
            lines.append(f"Posicionamiento: {self.positioning}")
        for recommendation in self.recommendations:
            lines.append(f"• {recommendation}")
        for limitation in self.limitations:
            lines.append(f"⚠ {limitation}")
        return "\n".join(lines)


class MarketService:
    """Analiza precios combinando las fuentes disponibles y autorizadas."""

    def __init__(self, wallapop: WallapopService, listings: ListingService) -> None:
        self._wallapop = wallapop
        self._listings = listings
        self._imported: list[dict[str, Any]] = []

    def set_backend(self, wallapop: WallapopService) -> None:
        self._wallapop = wallapop

    @property
    def external_data_available(self) -> bool:
        return self._wallapop.supports(Capability.MARKET_DATA)

    # ------------------------------------------------------------------
    def import_price_file(self, path: str | Path) -> int:
        """Importa un CSV propio con columnas: titulo, precio[, categoria]."""
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"No se encuentra el fichero '{file_path}'.")
        imported: list[dict[str, Any]] = []
        with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                keys = {k.lower().strip(): v for k, v in row.items() if k}
                price = _parse_price(keys.get("precio") or keys.get("price"))
                if price is None:
                    continue
                imported.append(
                    {
                        "titulo": keys.get("titulo") or keys.get("title") or "",
                        "precio": price,
                        "categoria": keys.get("categoria") or keys.get("category"),
                        "fuente": file_path.name,
                    }
                )
        self._imported.extend(imported)
        logger.info("Importados %d precios de '%s'.", len(imported), file_path.name)
        return len(imported)

    def clear_imported(self) -> None:
        self._imported.clear()

    # ------------------------------------------------------------------
    def analyze(
        self,
        query: str,
        *,
        account_ref: str | None = None,
        size: str | None = None,
        limit: int = 100,
    ) -> MarketAnalysis:
        """Analiza el mercado para una consulta con las fuentes disponibles."""
        analysis = MarketAnalysis(query=query)

        # --- Fuente 1: mis propios anuncios ---
        own = self._listings.search(
            ListingFilter(text=query or None, size=size, account_ref=account_ref, limit=limit)
        )
        own_prices = [v.price for v in own if v.price]
        analysis.own_stats = PriceStats.from_prices(own_prices)
        if analysis.own_stats:
            analysis.sources.append(f"Mis anuncios ({analysis.own_stats.count})")

        # --- Fuente 2: datos de mercado autorizados ---
        market_prices: list[float] = []
        if self.external_data_available:
            try:
                points = self._wallapop.get_market_data(
                    account_ref or (own[0].account_ref if own else ""), query, limit=limit
                )
                market_prices = [p.price for p in points if p.price]
                analysis.samples = [
                    {"titulo": p.title, "precio": p.price, "fuente": p.source} for p in points[:20]
                ]
                if market_prices:
                    analysis.sources.append(f"Datos de mercado autorizados ({len(market_prices)})")
            except WallapopError as exc:
                analysis.limitations.append(
                    f"No se han podido obtener datos de mercado: {exc.user_message}"
                )
        else:
            analysis.limitations.append(MARKET_SOURCE_REQUIRED_MESSAGE)

        # --- Fuente 3: fichero importado por el cliente ---
        imported_prices = [
            row["precio"]
            for row in self._imported
            if not query or query.lower() in str(row.get("titulo", "")).lower()
        ]
        if imported_prices:
            market_prices.extend(imported_prices)
            analysis.sources.append(f"Fichero propio ({len(imported_prices)})")

        analysis.market_stats = PriceStats.from_prices(market_prices)
        analysis.positioning, analysis.recommendations = self._position(
            analysis.own_stats, analysis.market_stats
        )
        if not analysis.sources:
            analysis.limitations.insert(
                0, "No hay ningún dato disponible para esta consulta."
            )
        return analysis

    @staticmethod
    def _position(
        own: PriceStats | None, market: PriceStats | None
    ) -> tuple[str, list[str]]:
        if own is None:
            return ("Sin anuncios propios que comparar.", [])
        if market is None:
            return (
                "Solo hay datos propios: no se puede comparar con el mercado.",
                [
                    "Para comparar con el mercado hace falta una fuente autorizada "
                    "o importar un fichero de precios propio."
                ],
            )
        recommendations: list[str] = []
        difference = own.average - market.median
        ratio = (difference / market.median * 100) if market.median else 0.0

        if ratio > 15:
            positioning = f"Tus precios están un {ratio:.0f}% por encima de la mediana del mercado."
            recommendations.append(
                f"Valora ajustar hacia la horquilla {market.p25:.0f} € – {market.p75:.0f} €."
            )
        elif ratio < -15:
            positioning = f"Tus precios están un {abs(ratio):.0f}% por debajo de la mediana del mercado."
            recommendations.append(
                f"Hay margen para subir: la mediana del mercado es {market.median:.0f} €."
            )
        else:
            positioning = "Tus precios están alineados con el mercado."
        recommendations.append(
            "LOT Bot no cambia ningún precio por su cuenta: revisa y confirma cada cambio."
        )
        return positioning, recommendations


def _parse_price(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("€", "").replace(".", "").replace(",", ".").strip())
    except ValueError:
        return None
