"""Plantillas por defecto de LOT Bot.

La plantilla de canape reproduce la estructura de anuncio del cliente, pero
los datos comerciales sensibles (telefono de WhatsApp, precios de oferta) se
dejan como VARIABLES. Se rellenan desde Configuracion -> Negocio, nunca desde
el codigo, para que no haya datos reales del cliente en el repositorio.

REGLA DE NEGOCIO: LOT Bot no modifica por su cuenta precios ni condiciones
comerciales. Los cambios de plantilla y de precio siempre los confirma el
usuario.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from lot_bot.database.engine import Database
from lot_bot.database.models import Template

#: Descripcion comercial base. Los importes y el telefono son variables
#: configurables por el cliente: aqui NO hay datos reales.
BASE_COMMERCIAL_DESCRIPTION = """GRAN OFERTA LIMITADA!
Renueva tu descanso hoy y paga menos ✨
✨ Canapé + colchón 90x190 → {precio_90}€
✨ Canapé + colchón 135x190 → {precio_135}€
✨ Canapé + colchón 150x190 → {precio_150}€
🚚 Transporte y montaje GRATUITO
📲 Pide el tuyo ahora por WhatsApp: {whatsapp}
🕒 Solo por tiempo limitado. ¡No te quedes sin el tuyo!"""

CANAPE_TEMPLATE: dict[str, Any] = {
    "name": "Canapé (plantilla base)",
    "description": (
        "Plantilla principal del cliente para anuncios de canapés. "
        "Los precios de oferta y el WhatsApp se configuran en Ajustes → Negocio."
    ),
    "title_pattern": "{producto} {medida} {color} {material}",
    "description_pattern": BASE_COMMERCIAL_DESCRIPTION,
    "features_pattern": [
        "{estado}",
        "{categoria}",
        "{color}",
        "{material}",
        "Medida: {medida}",
    ],
    "tags_pattern": [
        "canape",
        "{medida}",
        "{color}",
        "{material}",
        "colchon",
        "dormitorio",
    ],
    "variables": {
        "precio_90": "",
        "precio_135": "",
        "precio_150": "",
        "whatsapp": "",
    },
    "is_default": True,
}

GENERIC_TEMPLATE: dict[str, Any] = {
    "name": "Genérica",
    "description": "Plantilla neutra para productos que no son canapés.",
    "title_pattern": "{producto} {medida} {color}",
    "description_pattern": "{descripcion}\n\nEstado: {estado}\nMaterial: {material}",
    "features_pattern": ["{estado}", "{categoria}", "{color}", "{material}"],
    "tags_pattern": ["{producto}", "{color}", "{material}"],
    "variables": {},
    "is_default": False,
}

DEFAULT_TEMPLATES: list[dict[str, Any]] = [CANAPE_TEMPLATE, GENERIC_TEMPLATE]

#: Variables disponibles, para mostrarlas como ayuda en el editor de plantillas.
TEMPLATE_VARIABLES: dict[str, str] = {
    "producto": "Tipo de producto (p. ej. Canapé abatible)",
    "nombre": "Nombre completo del producto",
    "sku": "Código interno del producto",
    "medida": "Medida (p. ej. 135x190)",
    "color": "Color",
    "material": "Material",
    "estado": "Estado (Nuevo, Como nuevo...)",
    "categoria": "Categoría",
    "subcategoria": "Subcategoría",
    "precio": "Precio del producto",
    "stock": "Unidades disponibles",
    "descripcion": "Descripción propia del producto",
    "descripcion_base": "Descripción comercial base del negocio",
    "whatsapp": "WhatsApp del negocio (Ajustes → Negocio)",
    "negocio": "Nombre del negocio",
    "envio": "Condiciones de envío configuradas",
    "precio_90": "Precio oferta 90x190 (Ajustes → Negocio)",
    "precio_135": "Precio oferta 135x190 (Ajustes → Negocio)",
    "precio_150": "Precio oferta 150x190 (Ajustes → Negocio)",
}


def ensure_default_templates(database: Database) -> list[str]:
    """Crea las plantillas por defecto si no existen. Devuelve las creadas."""
    created: list[str] = []
    with database.session_scope() as session:
        for spec in DEFAULT_TEMPLATES:
            exists = session.scalar(select(Template).where(Template.name == spec["name"]))
            if exists is not None:
                continue
            session.add(
                Template(
                    name=spec["name"],
                    description=spec["description"],
                    title_pattern=spec["title_pattern"],
                    description_pattern=spec["description_pattern"],
                    features_pattern=list(spec["features_pattern"]),
                    tags_pattern=list(spec["tags_pattern"]),
                    variables=dict(spec["variables"]),
                    is_default=spec["is_default"],
                )
            )
            created.append(spec["name"])
    return created
