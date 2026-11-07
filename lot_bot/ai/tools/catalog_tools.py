"""Herramientas de catalogo, inventario y calidad."""

from __future__ import annotations

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
from lot_bot.catalog.service import ProductFilter
from lot_bot.database.models import ProductStatus


def _build_filter(context: ToolContext, args: dict[str, Any]) -> ProductFilter:
    status = None
    raw_status = args.get("situacion")
    if raw_status:
        try:
            status = ProductStatus(raw_status)
        except ValueError:
            status = None
    return ProductFilter(
        text=args.get("texto"),
        product_type=args.get("tipo"),
        size=args.get("medida"),
        color=args.get("color"),
        material=args.get("material"),
        category=args.get("categoria"),
        status=status,
        sku=args.get("sku"),
        min_price=args.get("precio_minimo"),
        max_price=args.get("precio_maximo"),
        only_in_stock=bool(args.get("solo_con_stock")),
        account_ref=_resolve_account(context, args.get("cuenta")),
        limit=int(args.get("limite") or 100),
    )


def _resolve_account(context: ToolContext, value: Any) -> str | None:
    if not value:
        return None
    return context.app.accounts.resolve_ref(str(value))


def _search_products(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    criteria = _build_filter(context, args)
    products = context.app.catalog.list_products(criteria)
    return ok(
        f"{len(products)} producto(s) encontrados ({criteria.describe()}).",
        productos=[p.to_dict() for p in products[:60]],
        total=len(products),
    )


def _get_product(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    identifier = args.get("producto") or args.get("sku")
    if not identifier:
        return fail("Indica el SKU, el identificador o el nombre del producto.")
    product = context.app.catalog.get_product(identifier)
    if product is None:
        return fail(f"No se encuentra el producto '{identifier}'.")
    data = product.to_dict()
    data["descripcion"] = product.description
    data["etiquetas"] = product.tags
    data["caracteristicas"] = product.features
    return ok(f"Producto {product.sku}: {product.name}.", producto=data)


def _create_product(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    name = args.get("nombre")
    if not name:
        return fail("Un producto necesita al menos un nombre.")
    if not context.confirmed:
        lines = [f"{k}: {v}" for k, v in args.items() if v not in (None, "")]
        return confirm_first(
            "create_product",
            args,
            "Voy a crear un producto nuevo en el catálogo",
            lines,
            affected=1,
        )
    product = context.app.catalog.create_product(args)
    context.app.audit.record_success(
        "Alta de producto", target=f"{product.sku} · {product.name}", actor=context.actor
    )
    return ok(f"Producto creado: {product.sku} · {product.name}.", producto=product.to_dict())


def _update_product(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    identifier = args.get("producto") or args.get("sku")
    changes = dict(args.get("cambios") or {})
    if not identifier or not changes:
        return fail("Indica el producto y los cambios a aplicar.")
    product = context.app.catalog.get_product(identifier)
    if product is None:
        return fail(f"No se encuentra el producto '{identifier}'.")
    if not context.confirmed:
        lines = [f"{product.sku} · {product.name}"] + [
            f"{k}: {getattr(product, k, '—')} → {v}" for k, v in changes.items()
        ]
        return confirm_first(
            "update_product", args, "Voy a modificar un producto del catálogo", lines, affected=1
        )
    updated = context.app.catalog.update_product(identifier, changes)
    context.app.audit.record_success(
        "Modificación de producto",
        target=updated.sku,
        detail=", ".join(f"{k}={v}" for k, v in changes.items())[:300],
        actor=context.actor,
    )
    return ok(f"Producto {updated.sku} actualizado.", producto=updated.to_dict())


def _get_inventory(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    criteria = _build_filter(context, args)
    products = context.app.catalog.list_products(criteria)
    counts = context.app.catalog.count_products()
    lines = [
        {"sku": p.sku, "nombre": p.name, "stock": p.stock, "situacion": p.status}
        for p in products[:100]
    ]
    sin_stock = [p.sku for p in products if p.stock <= 0]
    return ok(
        f"{len(products)} producto(s). {len(sin_stock)} sin stock. Total en catálogo: {counts['total']}.",
        inventario=lines,
        sin_stock=sin_stock,
        resumen=counts,
    )


def _update_inventory(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    identifier = args.get("producto") or args.get("sku")
    stock = args.get("stock")
    if not identifier or stock is None:
        return fail("Indica el producto y las unidades disponibles.")
    product = context.app.catalog.get_product(identifier)
    if product is None:
        return fail(f"No se encuentra el producto '{identifier}'.")
    if not context.confirmed:
        return confirm_first(
            "update_inventory",
            args,
            "Voy a actualizar el inventario",
            [f"{product.sku} · {product.name}", f"Stock: {product.stock} → {int(stock)}"],
            affected=1,
        )
    updated = context.app.catalog.update_stock(identifier, int(stock))
    context.app.audit.record_success(
        "Actualización de inventario",
        target=updated.sku,
        detail=f"stock {product.stock} → {updated.stock}",
        actor=context.actor,
    )
    return ok(f"Stock de {updated.sku}: {updated.stock} unidades.", producto=updated.to_dict())


def _validate_products(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    criteria = _build_filter(context, args)
    reports = [
        context.app.catalog.validate_product(p.id)
        for p in context.app.catalog.list_products(criteria)
    ]
    incomplete = [r for r in reports if not r.can_publish]
    with_warnings = [r for r in reports if r.can_publish and r.warnings]
    return ok(
        f"{len(reports)} producto(s) revisados: {len(incomplete)} con información incorrecta "
        f"o incompleta y {len(with_warnings)} con avisos.",
        incorrectos=[r.to_dict() for r in incomplete[:30]],
        con_avisos=[{"producto": r.subject, "avisos": [i.message for i in r.warnings]} for r in with_warnings[:30]],
    )


def _detect_duplicates(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    scope = (args.get("ambito") or "todo").lower()
    result: dict[str, Any] = {}
    total = 0
    if scope in {"todo", "productos"}:
        groups = context.app.catalog.find_duplicate_products()
        result["productos"] = [g.to_dict() for g in groups[:30]]
        total += len(groups)
    if scope in {"todo", "anuncios"}:
        from lot_bot.publishing.listings import ListingFilter

        groups = context.app.listings.find_duplicates(
            ListingFilter(text=args.get("texto"), account_ref=_resolve_account(context, args.get("cuenta")))
        )
        result["anuncios"] = [g.to_dict() for g in groups[:30]]
        total += len(groups)
    return ok(
        f"{total} grupo(s) de duplicados. LOT Bot no elimina nada automáticamente: "
        f"revisa cada grupo y decide.",
        **result,
    )


def _assign_to_accounts(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    identifier = args.get("producto") or args.get("sku")
    accounts = args.get("cuentas") or []
    if not identifier or not accounts:
        return fail("Indica el producto y las cuentas a las que asignarlo.")
    refs: list[str] = []
    for value in accounts:
        ref = context.app.accounts.resolve_ref(str(value))
        if ref is None:
            return fail(f"No se encuentra la cuenta '{value}'.")
        refs.append(ref)
    product = context.app.catalog.get_product(identifier)
    if product is None:
        return fail(f"No se encuentra el producto '{identifier}'.")
    if not context.confirmed:
        return confirm_first(
            "assign_product_to_accounts",
            args,
            "Voy a asignar el producto a estas cuentas",
            [f"Producto: {product.sku} · {product.name}"] + [f"Cuenta: {r}" for r in refs],
            affected=len(refs),
        )
    assigned = context.app.catalog.assign_to_accounts(identifier, refs, replace=bool(args.get("reemplazar")))
    context.app.audit.record_success(
        "Asignación de producto a cuentas",
        target=product.sku,
        detail=", ".join(assigned),
        actor=context.actor,
    )
    return ok(f"{product.sku} asignado a {len(assigned)} cuenta(s).", cuentas=assigned)


CATALOG_TOOLS: list[Tool] = [
    Tool(
        name="search_products",
        description=(
            "Busca productos en el catálogo interno por texto, tipo, medida, color, "
            "material, precio, estado o cuenta asignada."
        ),
        parameters={
            "properties": {
                "texto": {"type": "string", "description": "Texto libre a buscar."},
                "tipo": {"type": "string", "description": "Tipo de producto, p. ej. 'canapé'."},
                "medida": {"type": "string", "description": "Medida, p. ej. '135x190'."},
                "color": {"type": "string"},
                "material": {"type": "string"},
                "categoria": {"type": "string"},
                "situacion": {
                    "type": "string",
                    "enum": ["draft", "ready", "published", "paused", "archived"],
                },
                "precio_minimo": {"type": "number"},
                "precio_maximo": {"type": "number"},
                "solo_con_stock": {"type": "boolean"},
                "cuenta": {"type": "string"},
                "limite": {"type": "integer"},
            },
            "required": [],
        },
        handler=_search_products,
        category=ToolCategory.CATALOG,
    ),
    Tool(
        name="get_product",
        description="Obtiene la ficha completa de un producto por SKU, id o nombre.",
        parameters={
            "properties": {"producto": {"type": "string", "description": "SKU, id o nombre."}},
            "required": ["producto"],
        },
        handler=_get_product,
        category=ToolCategory.CATALOG,
    ),
    Tool(
        name="create_product",
        description=(
            "Da de alta un producto nuevo en el catálogo interno (no publica nada en "
            "Wallapop). Requiere confirmación del usuario."
        ),
        parameters={
            "properties": {
                "nombre": {"type": "string"},
                "sku": {"type": "string", "description": "Opcional: se genera si no se indica."},
                "product_type": {"type": "string"},
                "category": {"type": "string"},
                "size": {"type": "string"},
                "color": {"type": "string"},
                "material": {"type": "string"},
                "condition": {"type": "string"},
                "price": {"type": "number"},
                "stock": {"type": "integer"},
                "description": {"type": "string"},
            },
            "required": ["nombre"],
        },
        handler=_create_product,
        category=ToolCategory.CATALOG,
        requires_confirmation=True,
    ),
    Tool(
        name="update_product",
        description="Modifica los datos de un producto del catálogo. Requiere confirmación.",
        parameters={
            "properties": {
                "producto": {"type": "string"},
                "cambios": {
                    "type": "object",
                    "description": "Campos a cambiar, p. ej. {\"price\": 269, \"color\": \"Gris\"}.",
                },
            },
            "required": ["producto", "cambios"],
        },
        handler=_update_product,
        category=ToolCategory.CATALOG,
        requires_confirmation=True,
    ),
    Tool(
        name="get_inventory",
        description="Consulta el inventario: unidades disponibles y productos sin stock.",
        parameters={
            "properties": {
                "texto": {"type": "string"},
                "tipo": {"type": "string"},
                "medida": {"type": "string"},
                "solo_con_stock": {"type": "boolean"},
                "limite": {"type": "integer"},
            },
            "required": [],
        },
        handler=_get_inventory,
        category=ToolCategory.CATALOG,
    ),
    Tool(
        name="update_inventory",
        description="Actualiza las unidades disponibles de un producto. Requiere confirmación.",
        parameters={
            "properties": {"producto": {"type": "string"}, "stock": {"type": "integer"}},
            "required": ["producto", "stock"],
        },
        handler=_update_inventory,
        category=ToolCategory.CATALOG,
        requires_confirmation=True,
    ),
    Tool(
        name="validate_products",
        description=(
            "Revisa qué productos tienen información incorrecta, incompleta o "
            "contradictoria y qué les falta para poder publicarse."
        ),
        parameters={
            "properties": {
                "texto": {"type": "string"},
                "tipo": {"type": "string"},
                "medida": {"type": "string"},
                "limite": {"type": "integer"},
            },
            "required": [],
        },
        handler=_validate_products,
        category=ToolCategory.CATALOG,
    ),
    Tool(
        name="detect_duplicates",
        description=(
            "Detecta productos, anuncios e imágenes duplicados o muy parecidos. "
            "Nunca elimina nada: solo informa."
        ),
        parameters={
            "properties": {
                "ambito": {"type": "string", "enum": ["todo", "productos", "anuncios"]},
                "texto": {"type": "string"},
                "cuenta": {"type": "string"},
            },
            "required": [],
        },
        handler=_detect_duplicates,
        category=ToolCategory.CATALOG,
    ),
    Tool(
        name="assign_product_to_accounts",
        description=(
            "Define en qué cuentas de Wallapop debe publicarse un producto. "
            "Requiere confirmación."
        ),
        parameters={
            "properties": {
                "producto": {"type": "string"},
                "cuentas": {"type": "array", "items": {"type": "string"}},
                "reemplazar": {
                    "type": "boolean",
                    "description": "Si es true, sustituye las asignaciones actuales.",
                },
            },
            "required": ["producto", "cuentas"],
        },
        handler=_assign_to_accounts,
        category=ToolCategory.CATALOG,
        requires_confirmation=True,
    ),
]
