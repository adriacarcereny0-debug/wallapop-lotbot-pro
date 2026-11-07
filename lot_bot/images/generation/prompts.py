"""Construcción del prompt a partir de los datos REALES del anuncio.

Regla: solo se describe lo que figura en el anuncio (tipo de producto, color,
material, medida...). Lo que varía entre imágenes es el ENCUADRE y la ESCENA
(ángulo, luz, decoración neutra del dormitorio), nunca el producto: así cada
anuncio tiene una foto distinta sin atribuir al producto características que
no tiene.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

#: Traducciones de palabras frecuentes. Traducir no es inventar; lo que no
#: está aquí se pasa literal, entre comillas.
_COLORS = {
    "gris": "grey",
    "blanco": "white",
    "negro": "black",
    "beige": "beige",
    "crema": "cream",
    "marron": "brown",
    "azul": "blue",
    "verde": "green",
    "rojo": "red",
    "roble": "oak-coloured",
    "nogal": "walnut-coloured",
    "antracita": "anthracite",
    "cerezo": "cherry-coloured",
}
_MATERIALS = {
    "madera": "wood",
    "tela": "fabric",
    "polipiel": "faux leather",
    "piel": "leather",
    "metal": "metal",
    "aglomerado": "particle board",
    "mdf": "MDF",
    "terciopelo": "velvet",
}
_CONDITIONS = {"nuevo", "como nuevo", "en buen estado", "en condiciones aceptables"}

#: Variaciones de escena (no del producto). Cada una con nombre en español
#: para mostrarla y analizar después qué funciona mejor.
ANGLES: dict[str, str] = {
    "tres_cuartos_pie": "three-quarter view from the foot of the bed",
    "frontal": "front view at eye level",
    "desde_la_puerta": "slightly elevated view from the bedroom door",
    "lateral": "side view showing the full length",
    "esquina_izquierda": "three-quarter view from the left corner of the room",
    "bajo_lateral": "low angle view from the side",
}
LIGHTS: dict[str, str] = {
    "manana": "soft natural morning light from a window",
    "dia_luminoso": "bright diffuse daylight",
    "tarde_calida": "warm late-afternoon sunlight through sheer curtains",
    "nublado": "overcast daylight, even soft shadows",
}
ROOMS: dict[str, str] = {
    "dormitorio_blanco": "a tidy bedroom with white walls and light wooden floor",
    "dormitorio_beige": "a small apartment bedroom with neutral beige walls",
    "dormitorio_alfombra": "a bright bedroom with a plain rug and a simple nightstand",
    "dormitorio_minimalista": "a minimalist bedroom with light grey walls",
    "dormitorio_acogedor": "a cosy bedroom with a window and plain curtains",
}
#: Estilos de decoración para «Cambiar estilo».
STYLES: dict[str, str] = {
    "natural": "natural, realistic home photo",
    "nordico": "Scandinavian decor, light wood and white tones",
    "moderno": "modern contemporary decor, clean lines",
    "calido": "warm, cosy decor with soft textiles",
    "minimalista": "minimalist decor, very few objects",
}
ROOM_LABELS = {
    "dormitorio_blanco": "Dormitorio blanco",
    "dormitorio_beige": "Dormitorio beige",
    "dormitorio_alfombra": "Dormitorio con alfombra",
    "dormitorio_minimalista": "Dormitorio minimalista",
    "dormitorio_acogedor": "Dormitorio acogedor",
}
STYLE_LABELS = {
    "natural": "Natural",
    "nordico": "Nórdico",
    "moderno": "Moderno",
    "calido": "Cálido",
    "minimalista": "Minimalista",
}
LIGHT_LABELS = {
    "manana": "Luz de mañana",
    "dia_luminoso": "Día luminoso",
    "tarde_calida": "Tarde cálida",
    "nublado": "Día nublado",
}

_ANGLES = list(ANGLES.values())
_LIGHT = list(LIGHTS.values())
_ROOMS = list(ROOMS.values())

BASE_STYLE = (
    "Photorealistic product photograph taken with a camera in a real home, "
    "natural colours, realistic materials and proportions, sharp focus on the product, "
    "the product clearly visible and centred."
)
NEGATIVE = (
    "No people. No text, no letters, no numbers, no watermarks, no logos, no brand names, "
    "no price tags, no labels. Not an illustration, not a 3D render, not a drawing."
)


def _plain(text: str) -> str:
    normalised = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in normalised if not unicodedata.combining(c)).strip()


@dataclass(slots=True)
class ProductImageSpec:
    """Lo que se sabe del producto, sacado del anuncio."""

    product_type: str
    colors: list[str] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)
    size: str | None = None
    with_mattress: bool = False
    #: Características que no se sabe clasificar: se pasan literalmente.
    other_features: list[str] = field(default_factory=list)

    def product_sentence(self) -> str:
        parts = [f'a "{self.product_type}"']
        if self.product_type and _plain(self.product_type).startswith("canape"):
            parts.append("(Spanish storage bed base)")
        if self.colors:
            parts.append("in " + " and ".join(self.colors))
        if self.materials:
            parts.append("made of " + " and ".join(self.materials))
        sentence = " ".join(parts)
        if self.size:
            sentence += f", size {self.size} cm"
        if self.with_mattress:
            sentence += ", with a mattress on top"
        if self.other_features:
            sentence += ". Listing features: " + ", ".join(f'"{f}"' for f in self.other_features)
        return sentence


def _translate_words(text: str, table: dict[str, str]) -> list[str]:
    """Traduce colores/materiales reconocidos, en masculino, femenino o plural
    («blanca», «grises», «negras»...)."""
    found = []
    for word in re.split(r"[\s,/·]+|\by\b", _plain(text)):
        candidates = [word, word.rstrip("s"), re.sub(r"es$", "", word)]
        candidates += [c[:-1] + "o" for c in list(candidates) if c.endswith("a")]
        for candidate in candidates:
            if candidate in table and table[candidate] not in found:
                found.append(table[candidate])
                break
    return found


def spec_from_text(text: str) -> ProductImageSpec:
    """Producto indicado por el usuario («canapé abatible gris de madera»).

    Solo se usa lo que el usuario escribe: el tipo va literal y el color y el
    material se traducen si se reconocen. Nada se añade.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Indica qué producto quieres representar.")
    return ProductImageSpec(
        product_type=text,
        colors=_translate_words(text, _COLORS),
        materials=_translate_words(text, _MATERIALS),
    )


def spec_from_master(master: Any, size: str | None = None) -> ProductImageSpec:
    """Especificación a partir del anuncio principal (solo sus datos)."""
    title_words = [w for w in re.split(r"\s+", (master.title or "").strip()) if w]
    product_type = title_words[0] if title_words else (master.name or "producto")
    colors: list[str] = []
    materials: list[str] = []
    others: list[str] = []
    for feature in master.features or []:
        plain = _plain(feature)
        if plain in _CONDITIONS:
            continue
        feature_colors = _translate_words(feature, _COLORS)
        feature_materials = _translate_words(feature, _MATERIALS)
        if feature_colors and not feature_materials:
            colors += [c for c in feature_colors if c not in colors]
        elif feature_materials and not feature_colors and len(plain.split()) == 1:
            materials += [m for m in feature_materials if m not in materials]
        else:
            others.append(feature)
    description = _plain(master.description or "")
    return ProductImageSpec(
        product_type=product_type,
        colors=colors,
        materials=materials,
        size=size,
        with_mattress="colchon" in description,
        other_features=others,
    )


def scene_for(variation: int, preferred_rooms: list[str] | None = None) -> dict[str, str]:
    """Escena (habitación, luz, ángulo) de una variación.

    `preferred_rooms` (habitaciones que mejor han funcionado, según el
    optimizador) se usan en 2 de cada 3 variaciones; la tercera sigue
    probando otras para no dejar de aprender.
    """
    angle = list(ANGLES)[variation % len(ANGLES)]
    light = list(LIGHTS)[(variation // len(ANGLES)) % len(LIGHTS)]
    rooms = list(ROOMS)
    room = rooms[(variation * 7 + 3) % len(rooms)]
    preferred = [r for r in (preferred_rooms or []) if r in ROOMS]
    if preferred and variation % 3 != 2:
        room = preferred[variation % len(preferred)]
    return {"habitacion": room, "luz": light, "angulo": angle, "estilo": "natural"}


def build_scene_prompt(spec: ProductImageSpec, scene: dict[str, str]) -> str:
    room = ROOMS.get(scene.get("habitacion", ""), _ROOMS[0])
    light = LIGHTS.get(scene.get("luz", ""), _LIGHT[0])
    angle = ANGLES.get(scene.get("angulo", ""), _ANGLES[0])
    style = STYLES.get(scene.get("estilo", "natural"), STYLES["natural"])
    return (
        f"{BASE_STYLE} The photo shows {spec.product_sentence()}, "
        f"placed in {room}, {style}, {light}, {angle}. {NEGATIVE}"
    )


def build_prompt(spec: ProductImageSpec, variation: int) -> str:
    """Prompt completo. `variation` cambia solo encuadre, luz y escena."""
    return build_scene_prompt(spec, scene_for(variation))


KEEP_PRODUCT = (
    "Keep the product from the reference image exactly as it is: same shape, "
    "colours, materials, proportions and details. Do not add, remove or change "
    "any feature of the product."
)

#: Operaciones sobre una imagen existente.
OPERATIONS = {
    "generar": "Generar",
    "mejorar": "Mejorar",
    "estilo": "Cambiar estilo",
    "habitacion": "Cambiar habitación",
    "referencia": "Usar como referencia",
}


def build_edit_prompt(
    operation: str, scene: dict[str, str], spec: ProductImageSpec | None = None
) -> str:
    """Prompt para editar a partir de una imagen de referencia (FLUX.2)."""
    product = f" The product is {spec.product_sentence()}." if spec else ""
    room = ROOMS.get(scene.get("habitacion", ""), "")
    light = LIGHTS.get(scene.get("luz", ""), "")
    angle = ANGLES.get(scene.get("angulo", ""), "")
    style = STYLES.get(scene.get("estilo", ""), "")
    if operation == "estilo":
        change = f"Change only the decor and photographic style of the room to: {style}."
    elif operation == "habitacion":
        change = f"Place the same product in {room}, {light}."
    elif operation == "referencia":
        change = (
            f"Create a new photograph of this same product placed in {room}, {style}, "
            f"{light}, {angle}."
        )
    else:
        raise ValueError(f"Operación desconocida: {operation}")
    return f"{BASE_STYLE}{product} {KEEP_PRODUCT} {change} {NEGATIVE}"
