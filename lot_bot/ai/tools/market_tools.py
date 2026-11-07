"""Herramientas de analisis de mercado y de imagenes."""

from __future__ import annotations

from typing import Any

from lot_bot.ai.tools.base import Tool, ToolCategory, ToolContext, ToolResult, fail, ok
from lot_bot.market.service import MARKET_SOURCE_REQUIRED_MESSAGE


def _get_market_data(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    query = args.get("consulta") or args.get("texto") or ""
    account_ref = None
    if args.get("cuenta"):
        account_ref = context.app.accounts.resolve_ref(str(args["cuenta"]))
    analysis = context.app.market.analyze(
        query, account_ref=account_ref, size=args.get("medida"), limit=int(args.get("limite") or 100)
    )
    if not analysis.sources:
        return ToolResult(
            ok=False,
            summary=MARKET_SOURCE_REQUIRED_MESSAGE,
            data=analysis.to_dict(),
            unavailable=not analysis.has_external_data,
        )
    return ok(analysis.summary(), analisis=analysis.to_dict())


def _analyze_prices(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    from lot_bot.publishing.listings import ListingFilter

    criteria = ListingFilter(
        text=args.get("texto"),
        size=args.get("medida"),
        account_refs=[
            ref
            for ref in (
                context.app.accounts.resolve_ref(str(c)) for c in (args.get("cuentas") or [])
            )
            if ref
        ],
        limit=500,
    )
    listings = context.app.listings.search(criteria)
    if not listings:
        return fail("No hay anuncios que coincidan con esos criterios.")

    from lot_bot.market.service import PriceStats

    stats = PriceStats.from_prices([v.price for v in listings if v.price])
    by_account: dict[str, list[float]] = {}
    for view in listings:
        if view.price:
            by_account.setdefault(view.account_alias, []).append(view.price)
    per_account = {
        alias: PriceStats.from_prices(prices).to_dict()
        for alias, prices in by_account.items()
        if PriceStats.from_prices(prices)
    }
    inconsistent = [
        v.to_dict()
        for v in listings
        if stats and v.price and abs(v.price - stats.median) > max(30.0, stats.median * 0.25)
    ]
    return ok(
        f"{len(listings)} anuncio(s) analizados. "
        + (f"Precio medio {stats.average:.2f} €, mediana {stats.median:.2f} €." if stats else ""),
        estadisticas=stats.to_dict() if stats else None,
        por_cuenta=per_account,
        precios_atipicos=inconsistent[:20],
    )


def _import_price_file(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    path = args.get("fichero")
    if not path:
        return fail("Indica la ruta del fichero CSV de precios.")
    try:
        count = context.app.market.import_price_file(str(path))
    except FileNotFoundError as exc:
        return fail(str(exc))
    return ok(f"{count} precio(s) importados del fichero propio.")


# ---------------------------------------------------------------------------
# Imagenes
# ---------------------------------------------------------------------------
def _validate_images(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    identifier = args.get("producto")
    if not identifier:
        return fail("Indica el producto.")
    product = context.app.catalog.get_product(str(identifier))
    if product is None:
        return fail(f"No se encuentra el producto '{identifier}'.")
    results = []
    for image in product.images:
        valid, errors, warnings = context.app.images.validate_file(image["path"])
        results.append(
            {
                "fichero": image["path"].split("/")[-1],
                "valida": valid,
                "errores": errors,
                "avisos": warnings,
                "principal": image["is_primary"],
            }
        )
    invalid = [r for r in results if not r["valida"]]
    return ok(
        f"{len(results)} fotografía(s) revisadas, {len(invalid)} con problemas.",
        fotografias=results,
    )


def _find_duplicate_images(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    from lot_bot.catalog.service import ProductFilter

    all_images: list[dict[str, Any]] = []
    for product in context.app.catalog.list_products(ProductFilter(limit=2000)):
        for image in product.images:
            all_images.append({**image, "producto": product.sku})
    groups = context.app.images.find_duplicates(all_images)
    return ok(
        f"{len(groups)} grupo(s) de fotografías repetidas entre {len(all_images)} imágenes. "
        f"No se elimina ninguna automáticamente.",
        grupos=[g.to_dict() for g in groups[:30]],
    )


MARKET_TOOLS: list[Tool] = [
    Tool(
        name="get_market_data",
        description=(
            "Analiza precios de mercado con las fuentes disponibles y autorizadas. "
            "Si no hay ninguna fuente externa, lo dice claramente en vez de inventar cifras."
        ),
        parameters={
            "properties": {
                "consulta": {"type": "string"},
                "medida": {"type": "string"},
                "cuenta": {"type": "string"},
                "limite": {"type": "integer"},
            },
            "required": [],
        },
        handler=_get_market_data,
        category=ToolCategory.MARKET,
    ),
    Tool(
        name="analyze_prices",
        description=(
            "Analiza los precios de mis propios anuncios: media, mediana, diferencias "
            "entre cuentas y precios atípicos."
        ),
        parameters={
            "properties": {
                "texto": {"type": "string"},
                "medida": {"type": "string"},
                "cuentas": {"type": "array", "items": {"type": "string"}},
            },
            "required": [],
        },
        handler=_analyze_prices,
        category=ToolCategory.MARKET,
    ),
    Tool(
        name="import_price_file",
        description="Importa un fichero CSV propio con precios (columnas: titulo, precio).",
        parameters={"properties": {"fichero": {"type": "string"}}, "required": ["fichero"]},
        handler=_import_price_file,
        category=ToolCategory.MARKET,
    ),
    Tool(
        name="validate_images",
        description="Comprueba que las fotografías de un producto cumplen los requisitos.",
        parameters={"properties": {"producto": {"type": "string"}}, "required": ["producto"]},
        handler=_validate_images,
        category=ToolCategory.IMAGES,
    ),
    Tool(
        name="find_duplicate_images",
        description="Busca fotografías repetidas en todo el catálogo. No elimina ninguna.",
        parameters={"properties": {}, "required": []},
        handler=_find_duplicate_images,
        category=ToolCategory.IMAGES,
    ),
]
