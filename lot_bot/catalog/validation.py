"""Control de calidad previo a la publicacion.

Dos niveles:
  * ERROR   -> bloquea la publicacion (falta un dato obligatorio).
  * AVISO   -> no bloquea, pero baja la puntuacion de calidad.

La puntuacion es orientativa; lo que impide publicar son los errores.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Formatos de imagen que LOT Bot acepta para preparar una publicacion.
#: OJO: esto es lo que acepta LOT Bot, no una afirmacion sobre lo que acepta
#: Wallapop. Antes de publicar se valida tambien contra los requisitos
#: oficiales de Wallapop (ver `images/service.py`).
ALLOWED_IMAGE_FORMATS = {"JPG", "JPEG", "PNG", "WEBP"}

MIN_TITLE_LENGTH = 10
MAX_TITLE_LENGTH = 60
MIN_DESCRIPTION_LENGTH = 40
MIN_IMAGES = 1
RECOMMENDED_IMAGES = 3
MAX_PRICE = 100_000.0


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "aviso"


@dataclass(slots=True)
class Issue:
    field: str
    severity: Severity
    message: str
    suggestion: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.ERROR


@dataclass(slots=True)
class QualityReport:
    """Resultado de validar un producto o un anuncio."""

    subject: str
    issues: list[Issue] = field(default_factory=list)
    score: int = 100

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.blocking]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if not i.blocking]

    @property
    def can_publish(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.can_publish and not self.warnings:
            return f"{self.subject}: correcto (calidad {self.score}/100)."
        parts = []
        if self.errors:
            parts.append(f"{len(self.errors)} error(es)")
        if self.warnings:
            parts.append(f"{len(self.warnings)} aviso(s)")
        return f"{self.subject}: {', '.join(parts)} (calidad {self.score}/100)."

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "score": self.score,
            "can_publish": self.can_publish,
            "errors": [
                {"campo": i.field, "mensaje": i.message, "sugerencia": i.suggestion}
                for i in self.errors
            ],
            "warnings": [
                {"campo": i.field, "mensaje": i.message, "sugerencia": i.suggestion}
                for i in self.warnings
            ],
        }


_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")

#: Penalizacion por tipo de incidencia.
_ERROR_PENALTY = 25
_WARNING_PENALTY = 8


def validate_listing_data(
    *,
    title: str | None,
    description: str | None,
    price: float | None,
    category: str | None = None,
    condition: str | None = None,
    features: dict[str, Any] | None = None,
    images: list[dict[str, Any]] | None = None,
    subject: str = "Anuncio",
    required_features: tuple[str, ...] = ("color", "material"),
) -> QualityReport:
    """Valida los datos de un anuncio antes de publicarlo."""
    issues: list[Issue] = []
    features = features or {}
    images = images or []

    # --- Titulo ---
    title_text = (title or "").strip()
    if not title_text:
        issues.append(Issue("titulo", Severity.ERROR, "El título está vacío.", "Genera el título con el asistente IA."))
    else:
        if _PLACEHOLDER_RE.search(title_text):
            issues.append(
                Issue(
                    "titulo",
                    Severity.ERROR,
                    "El título contiene variables sin rellenar.",
                    "Completa los datos del producto o edita la plantilla.",
                )
            )
        if len(title_text) < MIN_TITLE_LENGTH:
            issues.append(Issue("titulo", Severity.WARNING, f"El título es muy corto ({len(title_text)} caracteres)."))
        if len(title_text) > MAX_TITLE_LENGTH:
            issues.append(
                Issue(
                    "titulo",
                    Severity.WARNING,
                    f"El título supera los {MAX_TITLE_LENGTH} caracteres recomendados.",
                    "Acórtalo para que no se corte en el listado.",
                )
            )
        words = [w for w in re.findall(r"\w+", title_text.lower()) if len(w) > 2]
        if words and len(set(words)) < len(words) / 2:
            issues.append(
                Issue("titulo", Severity.WARNING, "El título repite la misma palabra varias veces.")
            )

    # --- Descripcion ---
    description_text = (description or "").strip()
    if not description_text:
        issues.append(Issue("descripcion", Severity.ERROR, "La descripción está vacía."))
    else:
        if _PLACEHOLDER_RE.search(description_text):
            issues.append(
                Issue(
                    "descripcion",
                    Severity.ERROR,
                    "La descripción contiene variables sin rellenar.",
                    "Revisa Ajustes → Negocio (precios de oferta y WhatsApp).",
                )
            )
        if len(description_text) < MIN_DESCRIPTION_LENGTH:
            issues.append(
                Issue("descripcion", Severity.WARNING, "La descripción es muy breve.")
            )

    # --- Precio ---
    if price is None:
        issues.append(Issue("precio", Severity.ERROR, "El anuncio no tiene precio."))
    elif price <= 0:
        issues.append(Issue("precio", Severity.ERROR, "El precio debe ser mayor que cero."))
    elif price > MAX_PRICE:
        issues.append(
            Issue("precio", Severity.ERROR, f"El precio ({price:.2f} €) parece incorrecto.")
        )
    elif price < 5:
        issues.append(
            Issue(
                "precio",
                Severity.WARNING,
                f"El precio ({price:.2f} €) es muy bajo. Comprueba que no sea un error.",
            )
        )

    # --- Categoria y estado ---
    if not (category or "").strip():
        issues.append(Issue("categoria", Severity.ERROR, "Falta la categoría."))
    if not (condition or "").strip():
        issues.append(Issue("estado", Severity.WARNING, "Falta el estado del producto."))

    # --- Caracteristicas ---
    for key in required_features:
        if not str(features.get(key) or "").strip():
            issues.append(
                Issue(
                    f"caracteristica:{key}",
                    Severity.WARNING,
                    f"Falta la característica '{key}'.",
                )
            )

    # --- Imagenes ---
    if len(images) < MIN_IMAGES:
        issues.append(
            Issue("fotografias", Severity.ERROR, "El anuncio no tiene ninguna fotografía.")
        )
    elif len(images) < RECOMMENDED_IMAGES:
        issues.append(
            Issue(
                "fotografias",
                Severity.WARNING,
                f"Solo hay {len(images)} fotografía(s). Se recomiendan {RECOMMENDED_IMAGES} o más.",
            )
        )
    for image in images:
        fmt = str(image.get("file_format") or "").upper().lstrip(".")
        if fmt and fmt not in ALLOWED_IMAGE_FORMATS:
            issues.append(
                Issue(
                    "fotografias",
                    Severity.ERROR,
                    f"Formato de imagen no permitido: {fmt}.",
                    f"Formatos admitidos: {', '.join(sorted(ALLOWED_IMAGE_FORMATS))}.",
                )
            )
    if images and not any(image.get("is_primary") for image in images):
        issues.append(
            Issue("fotografias", Severity.WARNING, "No hay ninguna fotografía marcada como principal.")
        )

    # --- Coherencia ---
    issues.extend(_contradiction_checks(title_text, description_text, features, price))

    score = 100 - _ERROR_PENALTY * sum(1 for i in issues if i.blocking)
    score -= _WARNING_PENALTY * sum(1 for i in issues if not i.blocking)
    return QualityReport(subject=subject, issues=issues, score=max(0, min(100, score)))


def _contradiction_checks(
    title: str, description: str, features: dict[str, Any], price: float | None
) -> list[Issue]:
    """Detecta datos contradictorios entre titulo, descripcion y caracteristicas."""
    issues: list[Issue] = []
    blob = f"{title} {description}".lower()

    size = str(features.get("medida") or features.get("size") or "").lower().replace(" ", "")
    if size:
        sizes_in_title = {s.replace(" ", "") for s in re.findall(r"\d{2,3}\s?x\s?\d{2,3}", title.lower())}
        if sizes_in_title and size not in sizes_in_title:
            issues.append(
                Issue(
                    "coherencia",
                    Severity.ERROR,
                    f"La medida de la ficha ({size}) no coincide con la del título ({', '.join(sizes_in_title)}).",
                    "Corrige la medida del producto o regenera el título.",
                )
            )

    condition = str(features.get("estado") or features.get("condition") or "").lower()
    if condition == "nuevo" and re.search(r"\b(usado|segunda mano|con uso)\b", blob):
        issues.append(
            Issue(
                "coherencia",
                Severity.WARNING,
                "El estado es 'Nuevo' pero el texto menciona uso previo.",
            )
        )

    if price is not None:
        prices_in_text = {
            float(p.replace(",", ".")) for p in re.findall(r"(\d{2,5}(?:[.,]\d{1,2})?)\s*€", description)
        }
        # Solo avisamos si el texto NO menciona el precio del anuncio en absoluto
        # y ademas lista precios concretos (tabla de ofertas).
        if prices_in_text and price not in prices_in_text:
            issues.append(
                Issue(
                    "coherencia",
                    Severity.WARNING,
                    f"El precio del anuncio ({price:.2f} €) no aparece entre los precios de la descripción.",
                    "Comprueba que la oferta de la descripción corresponde a esta medida.",
                )
            )
    return issues
