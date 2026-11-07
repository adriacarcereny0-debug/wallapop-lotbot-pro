"""Herramientas de generacion de contenido (titulos, descripciones, etiquetas).

La generacion es LOCAL y determinista: se apoya en las plantillas del cliente y
en los datos reales del producto. Asi el contenido es homogeneo entre anuncios
y no depende de que el modelo improvise.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from lot_bot.ai.tools.base import (
    Tool,
    ToolCategory,
    ToolContext,
    ToolResult,
    confirm_first,
    fail,
    ok,
)
from lot_bot.database.models import Template
from lot_bot.templates_engine.engine import TemplateEngine


def _product_context(context: ToolContext, identifier: str) -> tuple[Any, dict[str, Any]] | None:
    product = context.app.catalog.get_product(identifier)
    if product is None:
        return None
    template = None
    with context.app.db.session_scope() as session:
        if product.template_id:
            template = session.get(Template, product.template_id)
        if template is None:
            template = session.scalar(select(Template).where(Template.is_default.is_(True)))
        if template is not None:
            session.expunge(template)
    variables = dict(template.variables) if template else {}
    ctx = TemplateEngine.build_context(
        product={
            "name": product.name,
            "sku": product.sku,
            "product_type": product.product_type,
            "category": product.category,
            "subcategory": product.subcategory,
            "size": product.size,
            "color": product.color,
            "material": product.material,
            "condition": product.condition,
            "price": product.price,
            "stock": product.stock,
            "description": product.description,
            "features": product.features,
        },
        business=context.app.business_settings,
        extra=variables,
    )
    return (product, {"template": template, "context": ctx})


def _master_content(context: ToolContext, field: str) -> ToolResult:
    """Sin producto indicado, se trabaja sobre el anuncio principal.

    Se devuelve el texto ACTUAL de la plantilla y NO se modifica: cambiar el
    texto del cliente requiere que lo pida expresamente («actualiza la
    plantilla») y lo confirme.
    """
    master = context.app.master_ads.get(context.focus.get("master_key"))
    if master is None:
        return fail("Indica el producto.")
    value = master.title if field == "titulo" else master.description
    natural = context.app.agent.provider.natural_language if hasattr(context.app, "agent") else False
    note = (
        "Este es el texto actual del anuncio principal. Si propones otro, muéstraselo al "
        "usuario y aplícalo SOLO con update_master_ad cuando lo confirme."
        if natural
        else "Este es el texto actual del anuncio principal. En modo de órdenes directas "
        "LOT Bot no redacta textos nuevos por su cuenta: si quieres cambiarlo, escribe el "
        "texto nuevo y di «actualiza la plantilla con la descripción \"…\"»."
    )
    result = ok(
        f"{field.capitalize()} actual de «{master.name}». No se ha modificado nada.",
        **{field: value},
        nota=note,
    )
    result.focus = {"master_key": master.key}
    return result


def _generate_title(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    identifier = args.get("producto")
    if not identifier:
        return _master_content(context, "titulo")
    loaded = _product_context(context, str(identifier))
    if loaded is None:
        return fail(f"No se encuentra el producto '{identifier}'.")
    product, extras = loaded
    template = extras["template"]
    engine = TemplateEngine()
    pattern = args.get("plantilla") or (template.title_pattern if template else "{producto} {medida} {color}")
    title, missing = engine.render_text(pattern, extras["context"])
    if missing:
        return ToolResult(
            ok=False,
            summary=(
                f"No puedo generar un título completo: faltan datos del producto "
                f"({', '.join(missing)}). Complétalos y vuelve a intentarlo."
            ),
            data={"titulo_parcial": title, "faltan": missing},
        )
    return ok(f"Título generado para {product.sku}.", titulo=title, plantilla=pattern)


def _generate_description(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    identifier = args.get("producto")
    if not identifier:
        return _master_content(context, "descripcion")
    loaded = _product_context(context, str(identifier))
    if loaded is None:
        return fail(f"No se encuentra el producto '{identifier}'.")
    product, extras = loaded
    template = extras["template"]
    engine = TemplateEngine()
    pattern = args.get("plantilla") or (
        template.description_pattern if template else "{descripcion}"
    )
    description, missing = engine.render_text(pattern, extras["context"])
    if missing:
        return ToolResult(
            ok=False,
            summary=(
                f"No puedo generar la descripción: faltan variables ({', '.join(missing)}). "
                f"Revisa Ajustes → Negocio o los datos del producto."
            ),
            data={"descripcion_parcial": description, "faltan": missing},
        )
    return ok(f"Descripción generada para {product.sku}.", descripcion=description)


def _generate_tags(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    identifier = args.get("producto")
    if not identifier:
        return fail("Indica el producto.")
    loaded = _product_context(context, str(identifier))
    if loaded is None:
        return fail(f"No se encuentra el producto '{identifier}'.")
    product, extras = loaded
    template = extras["template"]
    engine = TemplateEngine()
    patterns = list(template.tags_pattern or []) if template else []
    tags: list[str] = []
    for pattern in patterns:
        value, missing = engine.render_text(pattern, extras["context"])
        if not missing and value.strip():
            tags.append(value.strip().lower())
    for extra in (product.product_type, product.size, product.color, product.material):
        if extra and str(extra).lower() not in tags:
            tags.append(str(extra).lower())
    return ok(f"{len(tags)} etiqueta(s) generadas para {product.sku}.", etiquetas=tags)


def _categorize_product(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Propone categoria a partir de las categorias reales de Wallapop."""
    identifier = args.get("producto")
    if not identifier:
        return fail("Indica el producto.")
    product = context.app.catalog.get_product(str(identifier))
    if product is None:
        return fail(f"No se encuentra el producto '{identifier}'.")

    from lot_bot.wallapop.capabilities import Capability

    categories: list[dict[str, Any]] = []
    if context.app.wallapop.supports(Capability.LIST_CATEGORIES):
        accounts = [a for a in context.app.accounts.list_accounts() if a.is_connected]
        if accounts:
            categories = context.app.wallapop.list_categories(accounts[0].internal_ref)

    if not categories:
        return ToolResult(
            ok=False,
            summary=(
                "No puedo proponer una categoría porque no dispongo del listado oficial "
                "de categorías de Wallapop (operación 'list_categories' no autorizada). "
                "Indica tú la categoría."
            ),
            data={"categoria_actual": product.category},
            unavailable=True,
        )

    haystack = " ".join(
        str(v).lower() for v in (product.name, product.product_type, product.category, product.material) if v
    )
    best = None
    for category in categories:
        name = str(category.get("name") or category.get("nombre") or "")
        words = [w for w in name.lower().split() if len(w) > 3]
        if any(word in haystack for word in words):
            best = category
            break
    return ok(
        f"Categoría propuesta: {best['name'] if best else categories[0].get('name')}."
        if best or categories
        else "Sin propuesta.",
        propuesta=best or categories[0],
        categorias_disponibles=categories[:20],
        nota="La categoría definitiva la confirmas tú antes de publicar.",
    )


def _analyze_product(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    identifier = args.get("producto")
    if not identifier:
        return fail("Indica el producto.")
    product = context.app.catalog.get_product(str(identifier))
    if product is None:
        return fail(f"No se encuentra el producto '{identifier}'.")
    report = context.app.catalog.validate_product(product.id)
    from lot_bot.publishing.listings import ListingFilter

    published = [
        v.to_dict()
        for v in context.app.listings.search(ListingFilter(product_sku=product.sku, limit=50))
    ]
    return ok(
        f"{product.sku}: calidad {report.score}/100, "
        f"{len(report.errors)} error(es), {len(report.warnings)} aviso(s), "
        f"publicado en {len(published)} anuncio(s).",
        producto=product.to_dict(),
        calidad=report.to_dict(),
        anuncios=published,
    )


def _list_templates(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    with context.app.db.session_scope() as session:
        templates = session.scalars(select(Template)).all()
        data = [
            {
                "id": t.id,
                "nombre": t.name,
                "titulo": t.title_pattern,
                "por_defecto": t.is_default,
                "variables": list(t.variables or {}),
            }
            for t in templates
        ]
    return ok(f"{len(data)} plantilla(s) disponibles.", plantillas=data)


def _apply_generated_content(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Guarda en el catalogo el titulo/descripcion generados. Requiere confirmacion."""
    identifier = args.get("producto")
    title = args.get("titulo")
    description = args.get("descripcion")
    if not identifier or (not title and not description):
        return fail("Indica el producto y el título o la descripción a guardar.")
    product = context.app.catalog.get_product(str(identifier))
    if product is None:
        return fail(f"No se encuentra el producto '{identifier}'.")

    changes: dict[str, Any] = {}
    if title:
        changes["title_override"] = title
    if description:
        changes["description"] = description

    if not context.confirmed:
        lines = [f"Producto: {product.sku} · {product.name}"]
        if title:
            lines.append(f"Título: {product.title_override or product.name} → {title}")
        if description:
            lines.append(f"Descripción: {len(description)} caracteres")
        return confirm_first(
            "apply_generated_content",
            args,
            "Voy a guardar el contenido generado en el catálogo",
            lines,
            affected=1,
        )

    updated = context.app.catalog.update_product(product.id, changes)
    context.app.audit.record_success(
        "Contenido generado aplicado", target=updated.sku, actor=context.actor
    )
    return ok(f"Contenido guardado en {updated.sku}.", producto=updated.to_dict())


CONTENT_TOOLS: list[Tool] = [
    Tool(
        name="generate_title",
        description=(
            "Genera el título de un producto usando su plantilla, para que todos los "
            "anuncios mantengan la misma estructura."
        ),
        parameters={
            "properties": {
                "producto": {
                    "type": "string",
                    "description": "SKU del producto. Omítelo para usar el anuncio principal.",
                },
                "plantilla": {"type": "string", "description": "Patrón alternativo, opcional."},
            },
            "required": [],
        },
        handler=_generate_title,
        category=ToolCategory.CONTENT,
    ),
    Tool(
        name="generate_description",
        description="Genera la descripción comercial de un producto a partir de su plantilla.",
        parameters={
            "properties": {"producto": {"type": "string"}, "plantilla": {"type": "string"}},
            "required": [],
        },
        handler=_generate_description,
        category=ToolCategory.CONTENT,
    ),
    Tool(
        name="generate_tags",
        description="Genera las etiquetas y palabras clave de un producto.",
        parameters={"properties": {"producto": {"type": "string"}}, "required": ["producto"]},
        handler=_generate_tags,
        category=ToolCategory.CONTENT,
    ),
    Tool(
        name="categorize_product",
        description=(
            "Propone la categoría de Wallapop para un producto, usando el listado "
            "oficial de categorías si está disponible."
        ),
        parameters={"properties": {"producto": {"type": "string"}}, "required": ["producto"]},
        handler=_categorize_product,
        category=ToolCategory.CONTENT,
    ),
    Tool(
        name="analyze_product",
        description=(
            "Analiza un producto: calidad de la ficha, qué le falta y en qué anuncios "
            "está publicado."
        ),
        parameters={"properties": {"producto": {"type": "string"}}, "required": ["producto"]},
        handler=_analyze_product,
        category=ToolCategory.CONTENT,
    ),
    Tool(
        name="list_templates",
        description="Lista las plantillas de anuncio configuradas.",
        parameters={"properties": {}, "required": []},
        handler=_list_templates,
        category=ToolCategory.CONTENT,
    ),
    Tool(
        name="apply_generated_content",
        description=(
            "Guarda en el catálogo el título o la descripción generados. "
            "Requiere confirmación."
        ),
        parameters={
            "properties": {
                "producto": {"type": "string"},
                "titulo": {"type": "string"},
                "descripcion": {"type": "string"},
            },
            "required": ["producto"],
        },
        handler=_apply_generated_content,
        category=ToolCategory.CONTENT,
        requires_confirmation=True,
    ),
]
