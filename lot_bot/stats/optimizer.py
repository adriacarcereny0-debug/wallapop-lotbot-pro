"""Optimización sencilla basada en el histórico de estadísticas.

No es aprendizaje automático: compara grupos con datos suficientes y, cuando
uno destaca, lo recomienda. Cuantos más anuncios y mediciones haya, más
recomendaciones aparecen. Sin datos suficientes, lo dice.

Cada recomendación indica en qué datos se basa. Las recomendaciones NO
cambian nada por su cuenta: la plantilla única (título, precio, descripción)
nunca se toca. Lo único que se aplica solo es la preferencia de habitaciones
al generar imágenes, y siempre dejando una parte de variaciones nuevas para
seguir aprendiendo.
"""

from __future__ import annotations

from typing import Any

from lot_bot.images.generation.prompts import ROOM_LABELS
from lot_bot.stats.analysis import CAUSALITY_NOTE, StatsAnalyzer

_ROOM_BY_LABEL = {label: key for key, label in ROOM_LABELS.items()}


class Optimizer:
    def __init__(self, analyzer: StatsAnalyzer) -> None:
        self.analyzer = analyzer

    def preferred_rooms(self) -> list[str]:
        """Habitaciones con mejor rendimiento, si hay datos suficientes."""
        result = self.analyzer.analyze("habitacion")
        if not result["datos_suficientes"]:
            return []
        comparable = [g for g in result["grupos"] if g["comparable"]]
        best = comparable[: max(1, len(comparable) // 2)]
        return [_ROOM_BY_LABEL[g["grupo"]] for g in best if g["grupo"] in _ROOM_BY_LABEL]

    def recommendations(self, account_ref: str | None = None) -> dict[str, Any]:
        """Recomendaciones separadas de los datos reales en que se basan."""
        items: list[dict[str, Any]] = []
        for aspect, template in (
            ("habitacion", "Genera más imágenes con «{best}» y sigue probando otras habitaciones."),
            ("luz", "Prueba más imágenes con «{best}»."),
            ("franja", "Programa las publicaciones preferentemente por la {best}."),
            ("dia", "Los anuncios publicados en {best} han rendido mejor: tenlo en cuenta al programar."),
            ("origen_imagen", "El tipo de imagen «{best}» ha funcionado mejor."),
        ):
            analysis = self.analyzer.analyze(aspect, account_ref=account_ref)
            if analysis["datos_suficientes"]:
                items.append(
                    {
                        "tipo": aspect,
                        "recomendacion": template.format(best=analysis["mejor"]),
                        "basado_en": analysis["conclusion"],
                        "anuncios": analysis["anuncios_con_datos"],
                    }
                )
        low = self.analyzer.low_performers(account_ref)
        if low["datos_suficientes"] and low["anuncios"]:
            items.append(
                {
                    "tipo": "bajo_rendimiento",
                    "recomendacion": "Para los anuncios con bajo rendimiento, prueba a publicar "
                    "una nueva versión con otra imagen (otra habitación o ángulo). El título, "
                    "el precio y la descripción de la plantilla única no se cambian.",
                    "basado_en": low["conclusion"],
                    "anuncios": len(low["anuncios"]),
                }
            )
        pending = [] if items else [
            "Todavía no hay datos suficientes para recomendar nada con fundamento. Publica "
            "más anuncios y actualiza las estadísticas durante varios días."
        ]
        return {
            "recomendaciones": items,
            "sin_datos_suficientes": pending,
            "nota": CAUSALITY_NOTE,
            "datos_reales": not self.analyzer.stats._app.demo_mode,
        }
