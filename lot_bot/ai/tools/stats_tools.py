"""Herramientas de estadísticas, análisis y recomendaciones.

La IA solo ve estadísticas guardadas (reales, o simuladas en DEMO, y se dice).
Los datos ausentes llegan como null / «No disponible». Las respuestas separan
siempre los DATOS de las RECOMENDACIONES y no afirman causalidad.
"""

from __future__ import annotations

from typing import Any

from lot_bot.ai.tools.base import Tool, ToolCategory, ToolContext, ToolResult, fail, ok
from lot_bot.stats import ASPECTS, show


def _account(context: ToolContext, args: dict[str, Any]) -> tuple[str | None, str]:
    raw = args.get("cuenta")
    if not raw:
        return None, ""
    ref = context.app.accounts.resolve_ref(str(raw))
    return (ref, "") if ref else (None, f"No encuentro la cuenta «{raw}».")


def _get_statistics(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    ref, error = _account(context, args)
    if error:
        return fail(error)
    order = args.get("ordenar_por") or "visualizaciones"
    try:
        rows = context.app.stats.table(ref, sort_by=order)
    except ValueError as exc:
        return fail(str(exc))
    limit = int(args.get("limite") or 20)
    if not rows:
        return ok("No hay anuncios publicados todavía.")
    with_data = [r for r in rows if r.views is not None or r.favorites is not None]
    lines = [
        f"{r.account_alias} · {r.title[:40]} · visualizaciones: {show(r.views)} · "
        f"favoritos: {show(r.favorites)}"
        for r in rows[:limit]
    ]
    return ToolResult(
        ok=True,
        summary=(
            f"{len(rows)} anuncio(s), {len(with_data)} con datos. "
            f"{context.app.stats.availability_note()}\n" + "\n".join(lines)
        ),
        data={"anuncios": [r.to_dict() for r in rows[:limit]], "datos_reales": not context.app.demo_mode},
    )


def _refresh_statistics(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    ref, error = _account(context, args)
    if error:
        return fail(error)
    result = context.app.stats.capture([ref] if ref else None)
    text = (
        f"Estadísticas leídas: {result['leidos']} anuncio(s) con datos, "
        f"{result['sin_datos']} sin datos disponibles, {result['omitidos']} omitido(s)."
    )
    if result["errores"]:
        text += " Avisos: " + "; ".join(result["errores"][:5])
    return ToolResult(ok=True, summary=text, data=result)


def _analyze(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    ref, error = _account(context, args)
    if error:
        return fail(error)
    aspect = args.get("aspecto")
    analyzer = context.app.analyzer
    if not aspect:
        top_views = analyzer.top("visualizaciones", 5, ref)
        top_favs = analyzer.top("favoritos", 5, ref)
        return ToolResult(
            ok=True,
            summary=(
                "DATOS: anuncios con más visualizaciones y más favoritos (solo los que tienen "
                "datos). Pregúntame por un aspecto concreto para compararlo: "
                + ", ".join(ASPECTS)
            ),
            data={"mas_visualizaciones": top_views, "mas_favoritos": top_favs},
        )
    try:
        result = analyzer.analyze(aspect, args.get("metrica") or "visualizaciones_dia", ref)
    except (ValueError, KeyError) as exc:
        return fail(str(exc))
    return ToolResult(ok=True, summary=f"DATOS: {result['conclusion']}", data=result)


def _recommendations(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    ref, error = _account(context, args)
    if error:
        return fail(error)
    result = context.app.optimizer.recommendations(ref)
    if not result["recomendaciones"]:
        return ToolResult(ok=True, summary=" ".join(result["sin_datos_suficientes"]), data=result)
    lines = [
        f"RECOMENDACIÓN: {r['recomendacion']} (DATOS: {r['basado_en']})"
        for r in result["recomendaciones"]
    ]
    return ToolResult(ok=True, summary="\n".join(lines) + f"\n{result['nota']}", data=result)


def _history(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    listing_id = args.get("anuncio") or (context.focus.get("listing_ids") or [None])[0]
    if listing_id is None:
        return fail("Indica el número del anuncio.")
    rows = context.app.stats.history(int(listing_id))
    if not rows:
        return ok("Ese anuncio todavía no tiene mediciones guardadas.")
    lines = [
        f"{r['fecha']:%d/%m/%Y %H:%M} · visualizaciones: {show(r['visualizaciones'])} · "
        f"favoritos: {show(r['favoritos'])}"
        for r in rows
    ]
    return ToolResult(ok=True, summary="\n".join(lines), data={"historico": rows})


def _low(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    result = context.app.analyzer.low_performers()
    return ToolResult(ok=True, summary=result["conclusion"], data=result)


_ACCOUNT = {"type": "string", "description": "Cuenta (nombre o número). Opcional."}

STATS_TOOLS: list[Tool] = [
    Tool(
        name="get_statistics",
        description=(
            "Estadísticas por cuenta y anuncio: visualizaciones, favoritos, fecha, estado, URL "
            "y rendimiento. Los datos que Wallapop no permite obtener aparecen como «No disponible»."
        ),
        parameters={
            "properties": {
                "cuenta": _ACCOUNT,
                "ordenar_por": {
                    "type": "string",
                    "enum": ["visualizaciones", "favoritos", "fecha", "rendimiento"],
                },
                "limite": {"type": "integer"},
            },
            "required": [],
        },
        handler=_get_statistics,
        category=ToolCategory.LISTINGS,
    ),
    Tool(
        name="refresh_statistics",
        description="Lee ahora las estadísticas de los anuncios (solo lectura) y las guarda en el histórico.",
        parameters={"properties": {"cuenta": _ACCOUNT}, "required": []},
        handler=_refresh_statistics,
        category=ToolCategory.LISTINGS,
    ),
    Tool(
        name="analyze_statistics",
        description=(
            "Analiza las estadísticas guardadas. Sin aspecto: anuncios con más visualizaciones y "
            "favoritos. Con aspecto (habitacion, luz, estilo, origen_imagen, dia, franja, "
            "titulo, descripcion, cuenta): compara grupos si hay datos suficientes."
        ),
        parameters={
            "properties": {
                "aspecto": {"type": "string", "enum": list(ASPECTS)},
                "metrica": {
                    "type": "string",
                    "enum": ["visualizaciones", "favoritos", "visualizaciones_dia", "favoritos_100"],
                },
                "cuenta": _ACCOUNT,
            },
            "required": [],
        },
        handler=_analyze,
        category=ToolCategory.LISTINGS,
    ),
    Tool(
        name="get_recommendations",
        description="Recomendaciones basadas en el histórico (imágenes, horarios, anuncios flojos).",
        parameters={"properties": {"cuenta": _ACCOUNT}, "required": []},
        handler=_recommendations,
        category=ToolCategory.LISTINGS,
    ),
    Tool(
        name="get_listing_history",
        description="Histórico de mediciones de un anuncio.",
        parameters={"properties": {"anuncio": {"type": "integer"}}, "required": []},
        handler=_history,
        category=ToolCategory.LISTINGS,
    ),
    Tool(
        name="get_low_performers",
        description="Anuncios con bajo rendimiento respecto al resto (si hay datos suficientes).",
        parameters={"properties": {}, "required": []},
        handler=_low,
        category=ToolCategory.LISTINGS,
    ),
]
