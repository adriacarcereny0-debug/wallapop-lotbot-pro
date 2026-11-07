"""Motor de plantillas de anuncios.

Usa marcadores simples `{variable}` para que el cliente pueda editarlas sin
conocimientos tecnicos. Una variable sin valor NO se sustituye por texto
inventado: se marca como ausente para que el control de calidad lo detecte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_VARIABLE_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class TemplateError(ValueError):
    """Plantilla mal formada."""


def extract_variables(text: str) -> list[str]:
    """Devuelve las variables usadas en una plantilla, sin repetir."""
    seen: list[str] = []
    for name in _VARIABLE_RE.findall(text or ""):
        if name not in seen:
            seen.append(name)
    return seen


@dataclass(slots=True)
class RenderedListing:
    """Resultado de aplicar una plantilla a un producto."""

    title: str
    description: str
    features: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    missing_variables: list[str] = field(default_factory=list)
    used_variables: dict[str, str] = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        return not self.missing_variables


class TemplateEngine:
    """Rellena plantillas con los datos del producto y del negocio."""

    def __init__(self, strict: bool = False) -> None:
        #: strict=True lanza error si falta una variable; False la deja marcada.
        self.strict = strict

    # ------------------------------------------------------------------
    def render_text(self, pattern: str, context: dict[str, Any]) -> tuple[str, list[str]]:
        """Sustituye variables. Devuelve (texto, variables_ausentes)."""
        missing: list[str] = []

        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            value = context.get(name)
            if value is None or str(value).strip() == "":
                if name not in missing:
                    missing.append(name)
                # Se deja el marcador visible para que nadie publique un hueco
                # sin darse cuenta; el control de calidad lo bloquea.
                return f"{{{name}}}"
            return str(value)

        rendered = _VARIABLE_RE.sub(_replace, pattern or "")
        if missing and self.strict:
            raise TemplateError(f"Faltan variables en la plantilla: {', '.join(missing)}")
        return _tidy(rendered), missing

    def render(
        self,
        *,
        title_pattern: str,
        description_pattern: str,
        features_pattern: list[str] | None = None,
        tags_pattern: list[str] | None = None,
        context: dict[str, Any],
    ) -> RenderedListing:
        missing: list[str] = []

        title, missing_title = self.render_text(title_pattern, context)
        missing.extend(m for m in missing_title if m not in missing)

        description, missing_desc = self.render_text(description_pattern, context)
        missing.extend(m for m in missing_desc if m not in missing)

        features: list[str] = []
        for pattern in features_pattern or []:
            value, missing_feature = self.render_text(pattern, context)
            # Una caracteristica incompleta simplemente no se incluye.
            if not missing_feature and value.strip():
                features.append(value.strip())
            else:
                missing.extend(m for m in missing_feature if m not in missing)

        tags: list[str] = []
        for pattern in tags_pattern or []:
            value, missing_tag = self.render_text(pattern, context)
            if not missing_tag and value.strip():
                tags.append(value.strip().lower())

        used = {
            name: str(context.get(name))
            for name in extract_variables(f"{title_pattern} {description_pattern}")
            if context.get(name) not in (None, "")
        }
        return RenderedListing(
            title=title,
            description=description,
            features=features,
            tags=tags,
            missing_variables=missing,
            used_variables=used,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def build_context(
        product: dict[str, Any],
        business: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Construye el contexto de variables a partir de un producto.

        Se aceptan nombres en espanol y en ingles para comodidad del cliente.
        """
        business = business or {}
        context: dict[str, Any] = {
            # Producto
            "producto": product.get("product_type") or product.get("name"),
            "product_type": product.get("product_type") or product.get("name"),
            "nombre": product.get("name"),
            "name": product.get("name"),
            "sku": product.get("sku"),
            "medida": product.get("size"),
            "size": product.get("size"),
            "color": product.get("color"),
            "material": product.get("material"),
            "estado": product.get("condition"),
            "condition": product.get("condition"),
            "categoria": product.get("category"),
            "category": product.get("category"),
            "subcategoria": product.get("subcategory"),
            "precio": _format_price(product.get("price")),
            "price": _format_price(product.get("price")),
            "stock": product.get("stock"),
            "descripcion": product.get("description"),
            "description": product.get("description"),
            # Negocio
            "whatsapp": business.get("whatsapp"),
            "negocio": business.get("name"),
            "business": business.get("name"),
            "envio": business.get("delivery"),
            "delivery": business.get("delivery"),
            "descripcion_base": business.get("base_description"),
        }
        for key, value in (product.get("features") or {}).items():
            context.setdefault(key, value)

        # Cualquier otro dato del negocio (precios de oferta, textos propios)
        # queda disponible como variable con su mismo nombre.
        for key, value in business.items():
            if value not in (None, ""):
                context.setdefault(key, value)

        # `extra` tiene la ultima palabra, pero un valor vacio nunca debe
        # tapar un dato real ya presente.
        for key, value in (extra or {}).items():
            if value in (None, "") and context.get(key) not in (None, ""):
                continue
            context[key] = value
        return context


def _format_price(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"{float(value):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return str(value)


def _tidy(text: str) -> str:
    """Limpia espacios dobles y lineas en blanco de mas."""
    lines = [re.sub(r"[ \t]{2,}", " ", line).rstrip() for line in text.splitlines()]
    cleaned: list[str] = []
    blank = 0
    for line in lines:
        if not line.strip():
            blank += 1
            if blank > 2:
                continue
        else:
            blank = 0
        cleaned.append(line)
    return "\n".join(cleaned).strip()
