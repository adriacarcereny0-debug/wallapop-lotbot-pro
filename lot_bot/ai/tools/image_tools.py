"""Herramientas de imágenes: generar, mejorar, cambiar estilo o habitación y
usar una foto como referencia.

Reglas: se representan solo las características reales del producto (las del
anuncio principal o las que indique el usuario); nunca precios, teléfonos,
logos, marcas ni texto dentro de la imagen; todas las imágenes pasan por la
detección de repetidas. Lo que consume créditos de FLUX pide confirmación.
Las imágenes se identifican por su número: la IA no puede leer ficheros
arbitrarios del ordenador.
"""

from __future__ import annotations

import random
from pathlib import Path
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
from lot_bot.images.generation import GenerationError, spec_from_master
from lot_bot.images.generation.prompts import (
    LIGHT_LABELS,
    LIGHTS,
    ROOM_LABELS,
    ROOMS,
    STYLE_LABELS,
    STYLES,
    spec_from_text,
)

_OPERATION_TITLES = {
    "generar": "generar una imagen nueva",
    "estilo": "crear una versión con otro estilo",
    "habitacion": "cambiar la habitación",
    "referencia": "crear una imagen nueva a partir de la foto de referencia",
}


def _pick(value: Any, options: dict[str, str], labels: dict[str, str]) -> str | None:
    """Acepta la clave («dormitorio_blanco») o la etiqueta («Dormitorio blanco»)."""
    if not value:
        return None
    text = str(value).strip().lower()
    for key in options:
        if text == key or text == labels.get(key, "").lower():
            return key
    for key in options:
        if text in labels.get(key, "").lower() or text in key:
            return key
    raise ValueError(
        f"«{value}» no es una opción válida. Opciones: " + ", ".join(labels.values())
    )


def _cost_line(context: ToolContext) -> str:
    generator = context.app.image_generation.generator
    if generator.is_demo:
        return "MODO DEMO: imagen de prueba local, sin FLUX ni créditos."
    return f"Se usará {generator.name}: consume créditos de Black Forest Labs."


def _image(context: ToolContext, args: dict[str, Any]) -> tuple[dict | None, str]:
    raw = args.get("imagen")
    if raw is None:
        return None, "Indica el número de la imagen (pídeme «lista las imágenes»)."
    try:
        info = context.app.image_generation.get_image(int(raw))
    except (TypeError, ValueError):
        return None, f"«{raw}» no es un número de imagen."
    if info is None or not info["existe"]:
        return None, f"No encuentro la imagen {raw}."
    return info, ""


def _scene(args: dict[str, Any]) -> dict[str, str]:
    scene: dict[str, str] = {}
    room = _pick(args.get("habitacion"), ROOMS, ROOM_LABELS)
    light = _pick(args.get("luz"), LIGHTS, LIGHT_LABELS)
    style = _pick(args.get("estilo"), STYLES, STYLE_LABELS)
    if room:
        scene["habitacion"] = room
    if light:
        scene["luz"] = light
    if style:
        scene["estilo"] = style
    return scene


def _spec(context: ToolContext, args: dict[str, Any]):
    if args.get("producto"):
        return spec_from_text(str(args["producto"]))
    master = context.app.master_ads.get(None)
    return spec_from_master(master) if master else None


def _describe(result) -> str:
    scene = result.scene or {}
    parts = [
        ROOM_LABELS.get(scene.get("habitacion", ""), ""),
        LIGHT_LABELS.get(scene.get("luz", ""), ""),
        STYLE_LABELS.get(scene.get("estilo", ""), ""),
    ]
    detail = ", ".join(p for p in parts if p)
    return f"Imagen {result.image_id} creada ({result.provider}{'; ' + detail if detail else ''})."


# ---------------------------------------------------------------------------
def _list_images(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    images = [i for i in context.app.image_generation.history(int(args.get("limite") or 30)) if i["existe"]]
    if not images:
        return ok("Todavía no hay imágenes. Puedo generar una nueva o puedes subir una foto propia.")
    rows = [
        {
            "imagen": i["id"],
            "tipo": i["operacion"],
            "habitacion": ROOM_LABELS.get(i["escena"].get("habitacion", ""), None),
            "fecha": i["fecha"].strftime("%d/%m/%Y %H:%M") if i["fecha"] else None,
            "origen": i["proveedor"],
        }
        for i in images
    ]
    return ToolResult(ok=True, summary=f"{len(rows)} imagen(es) disponibles.", data={"imagenes": rows})


def _generate(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    try:
        spec = _spec(context, args)
        scene = _scene(args)
    except ValueError as exc:
        return fail(str(exc))
    if spec is None:
        return fail("Indica qué producto quieres representar.")
    if not context.confirmed:
        lines = [f"Producto: {spec.product_sentence()}"]
        if scene:
            lines.append(
                "Escena: "
                + ", ".join(
                    filter(
                        None,
                        [
                            ROOM_LABELS.get(scene.get("habitacion", "")),
                            LIGHT_LABELS.get(scene.get("luz", "")),
                            STYLE_LABELS.get(scene.get("estilo", "")),
                        ],
                    )
                )
            )
        lines += [
            "Fotografía realista, sin personas, sin texto, precios, teléfonos, logos ni marcas.",
            _cost_line(context),
        ]
        return confirm_first("generate_image", args, "Voy a generar una imagen nueva", lines)
    try:
        result = context.app.image_generation.generate_unique(
            spec,
            variation=random.randint(0, 10_000),
            subject=f"Imagen suelta: {spec.product_type}",
            scene={"luz": "dia_luminoso", "angulo": "tres_cuartos_pie",
                   "habitacion": "dormitorio_blanco", "estilo": "natural", **scene} if scene else None,
            preferred_rooms=context.app.optimizer.preferred_rooms() if context.app.optimizer else None,
        )
    except GenerationError as exc:
        return fail(exc.user_message)
    return ToolResult(ok=True, summary=_describe(result), data={"imagen": result.image_id, "ruta": str(result.path)})


def _edit(operation: str):
    def handler(context: ToolContext, args: dict[str, Any]) -> ToolResult:
        info, error = _image(context, args)
        if info is None:
            return fail(error)
        try:
            scene = _scene(args)
            spec = spec_from_text(str(args["producto"])) if args.get("producto") else None
        except ValueError as exc:
            return fail(str(exc))
        if operation == "estilo" and "estilo" not in scene:
            return fail("Indica el estilo: " + ", ".join(STYLE_LABELS.values()) + ".")
        if operation == "habitacion" and "habitacion" not in scene:
            return fail("Indica la habitación: " + ", ".join(ROOM_LABELS.values()) + ".")
        if not context.confirmed:
            return confirm_first(
                {"estilo": "change_image_style", "habitacion": "change_image_room",
                 "referencia": "image_from_reference"}[operation],
                args,
                f"Voy a {_OPERATION_TITLES[operation]} (imagen {info['id']})",
                [
                    "Se mantiene el producto tal cual: forma, colores, materiales y proporciones.",
                    "Sin texto, precios, teléfonos, logos ni marcas.",
                    "La imagen original no se modifica: se crea una nueva.",
                    _cost_line(context),
                ],
            )
        try:
            result = context.app.image_generation.edit(
                operation, Path(info["ruta"]), scene=scene, spec=spec,
                subject=f"{_OPERATION_TITLES[operation]} (imagen {info['id']})",
            )
        except GenerationError as exc:
            return fail(exc.user_message)
        return ToolResult(ok=True, summary=_describe(result), data={"imagen": result.image_id, "ruta": str(result.path)})

    return handler


def _enhance(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    info, error = _image(context, args)
    if info is None:
        return fail(error)
    try:
        result = context.app.image_generation.enhance(Path(info["ruta"]))
    except GenerationError as exc:
        return fail(exc.user_message)
    return ToolResult(
        ok=True,
        summary=f"Imagen {result.image_id} creada: versión mejorada de la {info['id']} (más "
        "resolución, nitidez y contraste; mejora local, sin IA y sin inventar detalles).",
        data={"imagen": result.image_id, "ruta": str(result.path)},
    )


_SCENE_PARAMS = {
    "habitacion": {"type": "string", "description": "Habitación: " + ", ".join(ROOM_LABELS.values())},
    "luz": {"type": "string", "description": "Luz: " + ", ".join(LIGHT_LABELS.values())},
    "estilo": {"type": "string", "description": "Estilo: " + ", ".join(STYLE_LABELS.values())},
}
_PRODUCT = {
    "type": "string",
    "description": "Qué producto representar (p. ej. «canapé abatible gris de madera»). Sin él, "
    "se usan los datos del anuncio principal. Solo características reales.",
}

IMAGE_TOOLS: list[Tool] = [
    Tool(
        name="list_images",
        description="Lista las imágenes guardadas (generadas, mejoradas o propias) con su número.",
        parameters={"properties": {"limite": {"type": "integer"}}, "required": []},
        handler=_list_images,
        category=ToolCategory.IMAGES,
    ),
    Tool(
        name="generate_image",
        description="Genera desde cero una foto realista del producto. Requiere confirmación.",
        parameters={"properties": {"producto": _PRODUCT, **_SCENE_PARAMS}, "required": []},
        handler=_generate,
        category=ToolCategory.IMAGES,
        requires_confirmation=True,
    ),
    Tool(
        name="enhance_image",
        description="Mejora la calidad/resolución de una imagen (local, sin IA). Crea una copia.",
        parameters={"properties": {"imagen": {"type": "integer"}}, "required": ["imagen"]},
        handler=_enhance,
        category=ToolCategory.IMAGES,
    ),
    Tool(
        name="change_image_style",
        description="Nueva versión de una imagen con otro estilo, manteniendo el producto. Requiere confirmación.",
        parameters={"properties": {"imagen": {"type": "integer"}, "estilo": _SCENE_PARAMS["estilo"], "producto": _PRODUCT}, "required": ["imagen", "estilo"]},
        handler=_edit("estilo"),
        category=ToolCategory.IMAGES,
        requires_confirmation=True,
    ),
    Tool(
        name="change_image_room",
        description="Coloca el mismo producto en otra habitación. Requiere confirmación.",
        parameters={"properties": {"imagen": {"type": "integer"}, "producto": _PRODUCT, **_SCENE_PARAMS}, "required": ["imagen", "habitacion"]},
        handler=_edit("habitacion"),
        category=ToolCategory.IMAGES,
        requires_confirmation=True,
    ),
    Tool(
        name="image_from_reference",
        description="Usa una foto como referencia del producto para crear una imagen nueva. Requiere confirmación.",
        parameters={"properties": {"imagen": {"type": "integer"}, "producto": _PRODUCT, **_SCENE_PARAMS}, "required": ["imagen"]},
        handler=_edit("referencia"),
        category=ToolCategory.IMAGES,
        requires_confirmation=True,
    ),
]
