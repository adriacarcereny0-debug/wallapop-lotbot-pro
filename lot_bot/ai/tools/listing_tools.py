"""Herramientas sobre anuncios de Wallapop (lectura y escritura confirmada)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from lot_bot.ai.tools.base import (
    Tool,
    ToolCategory,
    ToolContext,
    ToolResult,
    confirm_first,
    fail,
    ok,
)
from lot_bot.database.models import ListingStatus
from lot_bot.publishing.listings import ListingFilter


def _resolve_accounts(context: ToolContext, value: Any) -> list[str]:
    """Convierte nombres de cuenta en referencias internas."""
    if not value:
        return []
    values = value if isinstance(value, list) else [value]
    refs: list[str] = []
    for item in values:
        ref = context.app.accounts.resolve_ref(str(item))
        if ref:
            refs.append(ref)
    return refs


def _build_filter(context: ToolContext, args: dict[str, Any]) -> ListingFilter:
    status = None
    if args.get("estado"):
        try:
            status = ListingStatus(args["estado"])
        except ValueError:
            status = None
    return ListingFilter(
        text=args.get("texto"),
        account_refs=_resolve_accounts(context, args.get("cuentas") or args.get("cuenta")),
        size=args.get("medida"),
        color=args.get("color"),
        status=status,
        min_price=args.get("precio_minimo"),
        max_price=args.get("precio_maximo"),
        product_sku=args.get("sku"),
        limit=int(args.get("limite") or 300),
    )


def _search_listings(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    criteria = _build_filter(context, args)
    if (args.get("cuentas") or args.get("cuenta")) and not criteria.account_refs:
        return fail(f"No se encuentra la cuenta «{args.get('cuentas') or args.get('cuenta')}».")
    listings = context.app.listings.search(criteria)
    by_account = Counter(v.account_alias for v in listings)
    result = ok(
        f"{len(listings)} anuncio(s) encontrados ({criteria.describe()}).",
        anuncios=[v.to_dict() for v in listings[:80]],
        total=len(listings),
        por_cuenta=dict(by_account),
    )
    if len(listings) == 1:
        result.focus = {"listing_ids": [listings[0].id]}
    return result


def _get_listing(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    listing_id = args.get("anuncio")
    if listing_id is None:
        return fail("Indica el identificador del anuncio.")
    view = context.app.listings.get(int(listing_id))
    if view is None:
        return fail(f"No se encuentra el anuncio {listing_id}.")
    data = view.to_dict()
    data["descripcion"] = view.description
    result = ok(f"Anuncio {view.id}: {view.title} ({view.account_alias}).", anuncio=data)
    result.focus = {"listing_ids": [view.id]}
    return result


def _get_listings(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    refs = _resolve_accounts(context, args.get("cuenta") or args.get("cuentas"))
    if not refs:
        refs = [a.internal_ref for a in context.app.accounts.list_accounts() if a.is_connected]
    if not refs:
        return fail("No hay ninguna cuenta conectada.")
    results = context.app.listings.sync_all(refs) if args.get("sincronizar") else {}
    listings = context.app.listings.search(ListingFilter(account_refs=refs, limit=500))
    return ok(
        f"{len(listings)} anuncio(s) en {len(refs)} cuenta(s).",
        anuncios=[v.to_dict() for v in listings[:80]],
        sincronizacion=results,
    )


def _sync_listings(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    refs = _resolve_accounts(context, args.get("cuenta") or args.get("cuentas"))
    if not refs:
        refs = [a.internal_ref for a in context.app.accounts.list_accounts() if a.is_connected]
    if not refs:
        return fail("No hay ninguna cuenta conectada que sincronizar.")
    results = context.app.listings.sync_all(refs)
    total = sum(r.get("nuevos", 0) + r.get("actualizados", 0) for r in results.values())
    return ok(f"{total} anuncio(s) sincronizados en {len(refs)} cuenta(s).", detalle=results)


# ---------------------------------------------------------------------------
# Escritura: siempre con confirmacion
# ---------------------------------------------------------------------------
def _price_text(value: float | None) -> str:
    return f"{value:.2f} €".replace(".", ",") if value is not None else "sin precio"


def _update_price(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Cambia precios. NUNCA actúa sin saber exactamente sobre qué anuncios.

    Orden para decidir a qué afecta:
      1. Anuncios indicados expresamente.
      2. Criterios (medida, texto, color, cuentas).
      3. El anuncio del que se está hablando («este anuncio»).
    Si no hay nada de lo anterior, se pide que se concrete: cambiar el precio
    de TODOS los anuncios por una frase ambigua sería peligroso.
    """
    price = args.get("precio")
    if price is None:
        return fail("Indica el nuevo precio.")
    try:
        price = float(price)
    except (TypeError, ValueError):
        return fail(f"'{args.get('precio')}' no es un precio válido.")
    if price <= 0:
        return fail("El precio debe ser mayor que cero.")

    criteria_keys = ("texto", "medida", "color", "cuentas", "cuenta", "sku")
    has_criteria = any(args.get(key) for key in criteria_keys)
    listing_ids = args.get("anuncios")
    source = "criterios"
    if listing_ids:
        source = "indicados"
    elif not has_criteria and context.focus.get("listing_ids"):
        listing_ids = list(context.focus["listing_ids"])
        source = "foco"
        if args.get("solo_uno") and len(listing_ids) > 1:
            # «Este anuncio» en singular pero hay varios en juego: no se adivina.
            candidates = [context.app.listings.get(int(i)) for i in listing_ids]
            listado = "; ".join(
                f"n.º {v.id} ({v.account_alias}, {_price_text(v.price)})"
                for v in candidates
                if v is not None
            )
            return fail(
                f"Hay {len(listing_ids)} anuncios en la conversación: {listado}. "
                f"¿Cuál? Por ejemplo: «cambia el precio del anuncio {listing_ids[0]} a "
                f"{_price_text(price)}»."
            )

    if listing_ids:
        listings = [context.app.listings.get(int(i)) for i in listing_ids]
        listings = [v for v in listings if v is not None]
    elif has_criteria:
        listings = context.app.listings.search(_build_filter(context, args))
    else:
        return fail(
            "¿De qué anuncios quieres cambiar el precio? Indica la medida, la cuenta o "
            "el producto (por ejemplo: «cambia el precio de los canapés de 135x190 a 270 €») "
            "o pide antes que te muestre el anuncio concreto."
        )

    # ¿La orden habla también de la oferta del anuncio principal?
    medida = args.get("medida")
    master = None
    old_offer = None
    locked_note = ""
    # Se decide por la medida pedida, no por cómo se localizaron los anuncios:
    # al confirmar, los argumentos ya traen la lista de anuncios del plan, y lo
    # que se ejecuta tiene que ser EXACTAMENTE lo que se anunció en el plan.
    if medida and source != "foco":
        texto = args.get("texto") or ""
        candidate = context.app.master_ads.get()
        if candidate is not None and (not texto or context.app.master_ads.find_by_text(texto)):
            old_offer = candidate.variant_price(medida)
            if old_offer is not None and candidate.locked:
                locked_note = (
                    "La plantilla única de canapés está activa: su título, precio y "
                    "descripción no se cambian automáticamente. Para cambiar la oferta de "
                    f"{medida}, edita la plantilla en «Anuncio principal»."
                )
            elif old_offer is not None:
                master = candidate

    if not listings and master is None:
        return fail(locked_note or "No se ha encontrado ningún anuncio que coincida con esos criterios.")

    if not context.confirmed:
        lines: list[str] = []
        if len(listings) == 1:
            only = listings[0]
            title = (
                f"Voy a cambiar el precio del anuncio «{only.title}» ({only.account_alias}) "
                f"de {_price_text(only.price)} a {_price_text(price)}"
            )
        elif listings:
            title = f"Voy a cambiar el precio de {len(listings)} anuncios a {_price_text(price)}"
            by_account = Counter(v.account_alias for v in listings)
            lines += [f"{alias}: {count} anuncio(s)" for alias, count in sorted(by_account.items())]
            prices = sorted({v.price for v in listings if v.price is not None})
            if prices:
                lines.append(
                    "Precio actual: "
                    + (
                        _price_text(prices[0])
                        if len(prices) == 1
                        else f"entre {_price_text(prices[0])} y {_price_text(prices[-1])}"
                    )
                )
            lines.append(f"Nuevo precio: {_price_text(price)}")
            sample = ", ".join(v.title for v in listings[:3])
            if sample:
                lines.append(f"Ejemplos: {sample}")
        else:
            title = f"Voy a cambiar la oferta de {medida} del anuncio principal"
        if master is not None:
            lines.append(
                f"Oferta «Canapé + colchón {medida}» en la plantilla «{master.name}»: "
                f"{_price_text(old_offer)} → {_price_text(price)} (cambia el texto de la descripción)"
            )
        if locked_note:
            lines.append(locked_note)
        return confirm_first(
            "update_price",
            {**args, "anuncios": [v.id for v in listings], "precio": price},
            title,
            lines,
            affected=len(listings),
        )

    messages: list[str] = []
    failed: list[dict[str, Any]] = []
    done = 0
    if listings:
        outcomes = context.app.publishing.update_prices(
            [v.id for v in listings], price, confirmed=True, actor=context.actor
        )
        done = sum(1 for o in outcomes if o.success)
        failed = [o.to_dict() for o in outcomes if not o.success]
        messages.append(
            f"Precio actualizado a {_price_text(price)} en {done} de {len(outcomes)} anuncio(s)."
        )
    if master is not None and medida:
        context.app.master_ads.set_variant_price(
            master.key, medida, price, confirmed=True, actor=context.actor
        )
        messages.append(
            f"Oferta de {medida} de la plantilla actualizada a {_price_text(price)}. "
            f"Los anuncios ya publicados conservan su descripción anterior."
        )
    return ToolResult(
        ok=not failed,
        summary=" ".join(messages),
        data={"correctos": done, "fallidos": failed},
        focus={"listing_ids": [v.id for v in listings]} if len(listings) == 1 else {},
    )


def _update_listing_field(field: str, label: str):
    def handler(context: ToolContext, args: dict[str, Any]) -> ToolResult:
        listing_id = args.get("anuncio")
        value = args.get("valor")
        if listing_id is None and len(context.focus.get("listing_ids") or []) == 1:
            listing_id = context.focus["listing_ids"][0]
        if listing_id is None or value in (None, ""):
            return fail(f"Indica el anuncio y el nuevo {label}.")
        view = context.app.listings.get(int(listing_id))
        if view is None:
            return fail(f"No se encuentra el anuncio {listing_id}.")
        if not context.confirmed:
            current = getattr(view, field, None)
            return confirm_first(
                f"update_{field}",
                {**args, "anuncio": int(listing_id)},
                f"Voy a cambiar el {label} del anuncio «{view.title}» ({view.account_alias})",
                [
                    f"{label.capitalize()} actual: {str(current or '(vacío)')[:300]}",
                    f"{label.capitalize()} nuevo: {str(value)[:300]}",
                    "Solo cambia este anuncio; la plantilla y los demás anuncios no se tocan.",
                ],
                affected=1,
            )
        outcome = context.app.publishing.update_listing(
            int(listing_id), {field: value}, confirmed=True, actor=context.actor
        )
        return ToolResult(ok=outcome.success, summary=outcome.message, data=outcome.to_dict())

    return handler


def _update_images(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    listing_id = args.get("anuncio")
    urls = args.get("imagenes") or []
    if listing_id is None or not urls:
        return fail("Indica el anuncio y las fotografías a publicar.")
    view = context.app.listings.get(int(listing_id))
    if view is None:
        return fail(f"No se encuentra el anuncio {listing_id}.")
    if not context.confirmed:
        return confirm_first(
            "update_images",
            args,
            "Voy a actualizar las fotografías de un anuncio",
            [
                f"Cuenta: {view.account_alias}",
                f"Anuncio: {view.title}",
                f"Fotografías: {len(view.image_urls)} → {len(urls)}",
            ],
            affected=1,
        )
    from lot_bot.wallapop.capabilities import Capability

    context.app.wallapop.require(Capability.UPDATE_ITEM_IMAGES)
    result = context.app.wallapop.update_item_images(
        view.account_ref, view.wallapop_item_id or "", list(urls)
    )
    context.app.listings.apply_local_changes(int(listing_id), {"image_urls": list(urls)})
    context.app.audit.record_success(
        "Actualización de fotografías",
        account_ref=view.account_ref,
        target=view.title,
        actor=context.actor,
    )
    return ok(result.message, anuncio=view.id)


def _delete_listing(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    listing_id = args.get("anuncio")
    if listing_id is None:
        return fail("Indica el anuncio a eliminar.")
    view = context.app.listings.get(int(listing_id))
    if view is None:
        return fail(f"No se encuentra el anuncio {listing_id}.")
    if not context.confirmed:
        return confirm_first(
            "delete_listing",
            args,
            "Voy a ELIMINAR un anuncio de Wallapop",
            [
                f"Cuenta: {view.account_alias}",
                f"Anuncio: {view.title}",
                f"Precio: {view.price} €" if view.price else "Precio: —",
            ],
            destructive=True,
            affected=1,
        )
    outcome = context.app.publishing.delete_listing(
        int(listing_id), confirmed=True, actor=context.actor
    )
    return ToolResult(ok=outcome.success, summary=outcome.message, data=outcome.to_dict())


def _create_listing(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    identifier = args.get("producto") or args.get("sku")
    if not identifier:
        return fail("Indica el producto que quieres publicar.")
    refs = _resolve_accounts(context, args.get("cuentas") or args.get("cuenta"))
    try:
        previews = context.app.publishing.build_previews(identifier, refs or None)
    except ValueError as exc:
        return fail(str(exc))

    if not context.confirmed:
        lines = []
        for preview in previews:
            state = "listo" if preview.can_publish else "BLOQUEADO"
            lines.append(f"{preview.account_ref}: {preview.title} — {preview.price} € [{state}]")
            for issue in preview.quality.errors if preview.quality else []:
                lines.append(f"    ✗ {issue.message}")
        return confirm_first(
            "create_listing",
            {**args, "cuentas": [p.account_ref for p in previews]},
            f"Voy a publicar {len(previews)} anuncio(s) en Wallapop",
            lines,
            affected=len(previews),
        )

    outcomes = context.app.publishing.publish(
        identifier, refs or None, confirmed=True, actor=context.actor
    )
    done = sum(1 for o in outcomes if o.success)
    return ToolResult(
        ok=done > 0,
        summary=f"Publicados {done} de {len(outcomes)} anuncio(s).",
        data={"resultados": [o.to_dict() for o in outcomes]},
    )


def _preview_listing(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    identifier = args.get("producto") or args.get("sku")
    if not identifier:
        return fail("Indica el producto del que quieres la vista previa.")
    refs = _resolve_accounts(context, args.get("cuentas") or args.get("cuenta"))
    try:
        previews = context.app.publishing.build_previews(identifier, refs or None)
    except ValueError as exc:
        return fail(str(exc))
    publicables = sum(1 for p in previews if p.can_publish)
    return ok(
        f"{len(previews)} vista(s) previa(s) generadas; {publicables} lista(s) para publicar.",
        vistas_previas=[p.to_dict() for p in previews],
    )


def _validate_listings(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    criteria = _build_filter(context, args)
    reports = context.app.listings.quality_reports(criteria)
    with_errors = [r for r in reports if not r.can_publish]
    with_warnings = [r for r in reports if r.can_publish and r.warnings]
    return ok(
        f"{len(reports)} anuncio(s) revisados: {len(with_errors)} con información "
        f"incompleta o incorrecta y {len(with_warnings)} con avisos.",
        incompletos=[r.to_dict() for r in with_errors[:30]],
        con_avisos=[{"anuncio": r.subject, "avisos": [i.message for i in r.warnings]} for r in with_warnings[:30]],
    )


LISTING_TOOLS: list[Tool] = [
    Tool(
        name="search_listings",
        description=(
            "Busca anuncios publicados en cualquier cuenta por texto, medida, color, "
            "precio o estado. Devuelve también el recuento por cuenta."
        ),
        parameters={
            "properties": {
                "texto": {"type": "string"},
                "medida": {"type": "string", "description": "p. ej. '135x190'"},
                "color": {"type": "string"},
                "cuentas": {"type": "array", "items": {"type": "string"}},
                "estado": {
                    "type": "string",
                    "enum": ["draft", "pending", "active", "inactive", "sold", "removed", "error"],
                },
                "precio_minimo": {"type": "number"},
                "precio_maximo": {"type": "number"},
                "limite": {"type": "integer"},
            },
            "required": [],
        },
        handler=_search_listings,
        category=ToolCategory.LISTINGS,
    ),
    Tool(
        name="get_listing",
        description="Consulta el detalle de un anuncio concreto por su identificador.",
        parameters={"properties": {"anuncio": {"type": "integer"}}, "required": ["anuncio"]},
        handler=_get_listing,
        category=ToolCategory.LISTINGS,
    ),
    Tool(
        name="get_listings",
        description="Lista los anuncios de una o varias cuentas.",
        parameters={
            "properties": {
                "cuentas": {"type": "array", "items": {"type": "string"}},
                "sincronizar": {
                    "type": "boolean",
                    "description": "Si es true, descarga antes el estado actual desde Wallapop.",
                },
            },
            "required": [],
        },
        handler=_get_listings,
        category=ToolCategory.LISTINGS,
        capability="list_items",
    ),
    Tool(
        name="sync_listings",
        description="Descarga desde Wallapop el estado actual de los anuncios.",
        parameters={
            "properties": {"cuentas": {"type": "array", "items": {"type": "string"}}},
            "required": [],
        },
        handler=_sync_listings,
        category=ToolCategory.LISTINGS,
        capability="list_items",
    ),
    Tool(
        name="preview_listing",
        description=(
            "Genera la vista previa de cómo quedaría el anuncio de un producto en cada "
            "cuenta, con su control de calidad. No publica nada."
        ),
        parameters={
            "properties": {
                "producto": {"type": "string"},
                "cuentas": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["producto"],
        },
        handler=_preview_listing,
        category=ToolCategory.LISTINGS,
    ),
    Tool(
        name="create_listing",
        description=(
            "Publica en Wallapop el anuncio de un producto del catálogo, en las cuentas "
            "que tenga asignadas. Requiere confirmación del usuario."
        ),
        parameters={
            "properties": {
                "producto": {"type": "string"},
                "cuentas": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["producto"],
        },
        handler=_create_listing,
        category=ToolCategory.LISTINGS,
        requires_confirmation=True,
        capability="create_item",
    ),
    Tool(
        name="update_price",
        description=(
            "Cambia el precio de uno o varios anuncios. Puedes indicar los anuncios "
            "concretos o los criterios de búsqueda (medida, texto, cuentas). "
            "Requiere confirmación."
        ),
        parameters={
            "properties": {
                "precio": {"type": "number", "description": "Nuevo precio en euros."},
                "anuncios": {"type": "array", "items": {"type": "integer"}},
                "solo_uno": {
                    "type": "boolean",
                    "description": "True si el usuario habla de UN anuncio («este anuncio»).",
                },
                "texto": {"type": "string"},
                "medida": {"type": "string"},
                "color": {"type": "string"},
                "cuentas": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["precio"],
        },
        handler=_update_price,
        category=ToolCategory.LISTINGS,
        requires_confirmation=True,
        capability="update_item_price",
    ),
    Tool(
        name="update_title",
        description="Cambia el título de un anuncio publicado. Requiere confirmación.",
        parameters={
            "properties": {
                "anuncio": {
                    "type": "integer",
                    "description": "Omítelo para usar el anuncio del que se está hablando.",
                },
                "valor": {"type": "string"},
            },
            "required": ["valor"],
        },
        handler=_update_listing_field("title", "título"),
        category=ToolCategory.LISTINGS,
        requires_confirmation=True,
        capability="update_item",
    ),
    Tool(
        name="update_description",
        description="Cambia la descripción de un anuncio publicado. Requiere confirmación.",
        parameters={
            "properties": {
                "anuncio": {
                    "type": "integer",
                    "description": "Omítelo para usar el anuncio del que se está hablando.",
                },
                "valor": {"type": "string"},
            },
            "required": ["valor"],
        },
        handler=_update_listing_field("description", "descripción"),
        category=ToolCategory.LISTINGS,
        requires_confirmation=True,
        capability="update_item",
    ),
    Tool(
        name="update_images",
        description="Sustituye las fotografías de un anuncio publicado. Requiere confirmación.",
        parameters={
            "properties": {
                "anuncio": {"type": "integer"},
                "imagenes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["anuncio", "imagenes"],
        },
        handler=_update_images,
        category=ToolCategory.LISTINGS,
        requires_confirmation=True,
        capability="update_item_images",
    ),
    Tool(
        name="delete_listing",
        description=(
            "ELIMINA un anuncio de Wallapop. Acción destructiva e irreversible: "
            "requiere confirmación explícita del usuario."
        ),
        parameters={"properties": {"anuncio": {"type": "integer"}}, "required": ["anuncio"]},
        handler=_delete_listing,
        category=ToolCategory.LISTINGS,
        requires_confirmation=True,
        destructive=True,
        capability="delete_item",
    ),
    Tool(
        name="validate_listings",
        description=(
            "Revisa los anuncios publicados y dice cuáles tienen información "
            "incompleta, incorrecta o contradictoria."
        ),
        parameters={
            "properties": {
                "texto": {"type": "string"},
                "cuentas": {"type": "array", "items": {"type": "string"}},
                "limite": {"type": "integer"},
            },
            "required": [],
        },
        handler=_validate_listings,
        category=ToolCategory.LISTINGS,
    ),
]
