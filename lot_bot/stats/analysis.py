"""Análisis de las estadísticas guardadas.

Se trabaja solo con mediciones reales almacenadas (o simuladas en DEMO, y se
dice). Cada resultado indica cuántos anuncios lo respaldan y si hay datos
suficientes. Con pocos datos NO se saca ninguna conclusión, y nunca se afirma
causalidad: son correlaciones observadas.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean, median
from typing import Any

from lot_bot.images.generation.prompts import LIGHT_LABELS, ROOM_LABELS, STYLE_LABELS
from lot_bot.stats.service import ListingStats, StatsService

#: Mínimo de anuncios con datos en un grupo para compararlo.
MIN_PER_GROUP = 3
#: Mínimo de grupos comparables para hablar de un patrón.
MIN_GROUPS = 2
#: Días mínimos publicado para juzgar el rendimiento de un anuncio.
MIN_DAYS_FOR_LOW = 3

CAUSALITY_NOTE = (
    "Son correlaciones observadas en tus anuncios, no pruebas de causa: otros factores "
    "(día, cuenta, competencia) también influyen."
)

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

ASPECTS = {
    "habitacion": "Habitación de la imagen",
    "luz": "Iluminación de la imagen",
    "estilo": "Estilo de la imagen",
    "origen_imagen": "Tipo de imagen (generada, propia, plantilla)",
    "dia": "Día de publicación",
    "franja": "Franja horaria de publicación",
    "titulo": "Título",
    "descripcion": "Descripción",
    "cuenta": "Cuenta",
}


def _band(hour: int) -> str:
    if hour < 7:
        return "madrugada (0-7 h)"
    if hour < 12:
        return "mañana (7-12 h)"
    if hour < 16:
        return "mediodía (12-16 h)"
    if hour < 21:
        return "tarde (16-21 h)"
    return "noche (21-24 h)"


def group_key(row: ListingStats, aspect: str) -> str | None:
    image = (row.meta or {}).get("imagen") or {}
    scene = image.get("escena") or {}
    if aspect == "habitacion":
        return ROOM_LABELS.get(scene.get("habitacion")) if scene.get("habitacion") else None
    if aspect == "luz":
        return LIGHT_LABELS.get(scene.get("luz")) if scene.get("luz") else None
    if aspect == "estilo":
        return STYLE_LABELS.get(scene.get("estilo")) if scene.get("estilo") else None
    if aspect == "origen_imagen":
        if image.get("operacion") == "propia":
            return "Foto propia"
        return "Imagen generada" if image.get("id") else "Fotos de la plantilla"
    if aspect == "dia":
        return WEEKDAYS[row.published_at.weekday()] if row.published_at else None
    if aspect == "franja":
        return _band(row.published_at.hour) if row.published_at else None
    if aspect == "titulo":
        return row.title or None
    if aspect == "descripcion":
        return None  # se agrupa aparte (ver analyze)
    if aspect == "cuenta":
        return row.account_alias
    raise ValueError(f"Aspecto desconocido: {aspect}")


class StatsAnalyzer:
    def __init__(self, stats: StatsService) -> None:
        self.stats = stats

    def _rows(self, account_ref: str | None = None) -> list[ListingStats]:
        return self.stats.table(account_ref)

    # ------------------------------------------------------------------
    def top(self, metric: str = "visualizaciones", limit: int = 5, account_ref: str | None = None):
        rows = [r for r in self.stats.table(account_ref, sort_by=metric) if _metric(r, metric) is not None]
        return {
            "metrica": metric,
            "anuncios": [r.to_dict() for r in rows[:limit]],
            "con_datos": len(rows),
            "datos_reales": not self.stats._app.demo_mode,
        }

    def analyze(self, aspect: str, metric: str = "visualizaciones_dia", account_ref: str | None = None):
        """Compara grupos (p. ej. habitación de la imagen) por una métrica."""
        if aspect not in ASPECTS:
            raise ValueError(f"Aspecto desconocido: {aspect}. Opciones: {', '.join(ASPECTS)}")
        rows = [r for r in self._rows(account_ref) if _metric(r, metric) is not None]
        groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            key = _description_key(row, self.stats) if aspect == "descripcion" else group_key(row, aspect)
            if key:
                groups[key].append(float(_metric(row, metric)))
        table = sorted(
            (
                {
                    "grupo": key,
                    "anuncios": len(values),
                    "media": round(mean(values), 2),
                    "mediana": round(median(values), 2),
                    "comparable": len(values) >= MIN_PER_GROUP,
                }
                for key, values in groups.items()
            ),
            key=lambda g: g["media"],
            reverse=True,
        )
        comparable = [g for g in table if g["comparable"]]
        enough = len(comparable) >= MIN_GROUPS
        result: dict[str, Any] = {
            "aspecto": ASPECTS[aspect],
            "metrica": metric,
            "grupos": table,
            "anuncios_con_datos": len(rows),
            "datos_suficientes": enough,
            "datos_reales": not self.stats._app.demo_mode,
        }
        if not enough:
            result["conclusion"] = (
                f"No hay datos suficientes para sacar conclusiones sobre «{ASPECTS[aspect]}»: "
                f"hacen falta al menos {MIN_GROUPS} grupos con {MIN_PER_GROUP} anuncios con "
                f"datos cada uno (ahora hay {len(comparable)})."
            )
        else:
            best, worst = comparable[0], comparable[-1]
            result["conclusion"] = (
                f"Con los datos actuales, «{best['grupo']}» tiene la media más alta "
                f"({best['media']}) y «{worst['grupo']}» la más baja ({worst['media']}), "
                f"sobre {len(rows)} anuncios. {CAUSALITY_NOTE}"
            )
            result["mejor"] = best["grupo"]
        return result

    def low_performers(self, account_ref: str | None = None) -> dict[str, Any]:
        """Anuncios muy por debajo de la mediana de visualizaciones al día."""
        rows = [
            r
            for r in self._rows(account_ref)
            if r.views_per_day is not None and (r.days_online or 0) >= MIN_DAYS_FOR_LOW
        ]
        if len(rows) < MIN_PER_GROUP * MIN_GROUPS:
            return {
                "anuncios": [],
                "datos_suficientes": False,
                "conclusion": "Todavía no hay suficientes anuncios con varios días de datos "
                "para detectar cuáles rinden peor.",
            }
        center = median(r.views_per_day for r in rows)
        low = [r for r in rows if r.views_per_day < center * 0.5]
        return {
            "anuncios": [r.to_dict() for r in low],
            "mediana_visualizaciones_dia": center,
            "datos_suficientes": True,
            "conclusion": f"{len(low)} anuncio(s) con menos de la mitad de la mediana "
            f"({center} visualizaciones/día).",
        }


def _metric(row: ListingStats, metric: str):
    return {
        "visualizaciones": row.views,
        "favoritos": row.favorites,
        "visualizaciones_dia": row.views_per_day,
        "favoritos_100": row.favorites_rate,
    }[metric]


def _description_key(row: ListingStats, stats: StatsService) -> str | None:
    from lot_bot.database.models import Listing

    with stats._db.session_scope() as session:
        listing = session.get(Listing, row.listing_id)
        text = (listing.description or "").strip() if listing else ""
    return (text[:60] + "…") if len(text) > 60 else (text or None)
