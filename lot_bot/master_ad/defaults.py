"""Datos del anuncio principal del negocio, tal y como los ha dado el cliente.

REGLA: estos valores se conservan EXACTAMENTE. No se corrigen ni se
reinterpretan, aunque parezcan raros (el titulo repetido, «Dormitorio Y
Madera», el precio de 11,44 €...). Son decisiones del cliente y solo cambian
cuando el usuario los edita.

Los precios de las ofertas y el WhatsApp NO van escritos dentro del texto de
la descripcion: son variables ({precio_135x190}, {whatsapp}) que se rellenan
con las ofertas y el contacto configurados. Al renderizarse, la descripcion
queda identica a la original del cliente.
"""

from __future__ import annotations

from typing import Any

#: Clave interna del anuncio principal.
MASTER_KEY = "canape-principal"
MASTER_NAME = "Canapés — Anuncio principal"

#: Descripcion EXACTA indicada por el cliente (una sola linea, tal cual,
#: sin exclamacion final). Las pruebas comprueban que la plantilla produce
#: este texto letra por letra.
CLIENT_DESCRIPTION_RENDERED = (
    "GRAN OFERTA LIMITADA! Renueva tu descanso hoy y paga menos ✨ "
    "Canapé + colchón 90x190 → 230€ ✨ "
    "Canapé + colchón 135x190 → 270€ ✨ "
    "Canapé + colchón 150x190 → 290€ 🚚 "
    "Transporte y montaje GRATUITO 📲 "
    "Pide el tuyo ahora por WhatsApp: 603710542 🕒 "
    "Solo por tiempo limitado. ¡No te quedes sin el tuyo"
)

#: La misma descripcion, con los datos comerciales convertidos en variables.
CLIENT_DESCRIPTION_PATTERN = (
    "GRAN OFERTA LIMITADA! Renueva tu descanso hoy y paga menos ✨ "
    "Canapé + colchón 90x190 → {precio_90x190}€ ✨ "
    "Canapé + colchón 135x190 → {precio_135x190}€ ✨ "
    "Canapé + colchón 150x190 → {precio_150x190}€ 🚚 "
    "Transporte y montaje GRATUITO 📲 "
    "Pide el tuyo ahora por WhatsApp: {whatsapp} 🕒 "
    "Solo por tiempo limitado. ¡No te quedes sin el tuyo"
)

#: Título y precio exactos.
CLIENT_TITLE = "Canapé canapé canapé canapé canapé"
CLIENT_PRICE = 11.44

#: Características estructuradas: van a los campos del formulario de
#: Wallapop, NUNCA a la descripción.
CLIENT_ATTRIBUTES: dict[str, str] = {
    "estado": "Nuevo",
    "uso": "Dormitorio",
    "color": "Gris y Blanco",
    "material": "Madera",
}

CLIENT_MASTER_AD: dict[str, Any] = {
    "key": MASTER_KEY,
    "name": MASTER_NAME,
    "title": CLIENT_TITLE,
    # Mismos valores que CLIENT_ATTRIBUTES, en el orden en que se muestran.
    "features": list(CLIENT_ATTRIBUTES.values()),
    # «ubicacion»: ciudad que se escribe en Wallapop (editable en Anuncio principal).
    "attributes": {**CLIENT_ATTRIBUTES, "ubicacion": "Barcelona"},
    "price": CLIENT_PRICE,
    "description": CLIENT_DESCRIPTION_PATTERN,
    "variants": [
        {"medida": "90x190", "precio": 230},
        {"medida": "135x190", "precio": 270},
        {"medida": "150x190", "precio": 290},
    ],
    "contact_whatsapp": "603710542",
    "delivery_note": "Transporte y montaje GRATUITO",
    # Categoría de Wallapop (la que el cliente elige en el formulario):
    # «Estructura de camas», dentro de Muebles y organización > Camas y
    # accesorios. El bot elige la última parte; si no aparece entre las
    # sugeridas, recorre la ruta completa.
    "category": "Hogar y jardín > Muebles y organización > Camas y accesorios > Estructura de camas",
    "subcategory": "",
    "condition": "Nuevo",
    "tags": [],
    "keywords": [],
    "images": [],
    # Como se refiere el usuario a este anuncio en el chat.
    "aliases": [
        "canape",
        "canapes",
        "anuncio de canape",
        "anuncio del canape",
        "anuncio principal",
    ],
    "variables": {},
    "is_default": True,
    # Plantilla única activa: los anuncios automáticos usan exactamente estos
    # valores (sin cambios por publicación).
    "locked": True,
}
