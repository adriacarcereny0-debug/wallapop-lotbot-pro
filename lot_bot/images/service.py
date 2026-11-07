"""Importacion, validacion y deduplicacion de fotografias.

Formatos que LOT Bot acepta: JPG, JPEG, PNG, WEBP.

AVISO IMPORTANTE
----------------
Los requisitos reales de imagen de Wallapop (tamano maximo, resolucion minima,
formatos admitidos) NO estan codificados aqui como si fueran ciertos: se leen
de `WALLAPOP_IMAGE_REQUIREMENTS`, que se completa con la documentacion oficial.
Mientras no se completen, LOT Bot avisa de que la validacion contra Wallapop
esta pendiente en vez de dar por supuesto que la imagen sera aceptada.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from lot_bot.catalog.duplicates import DuplicateGroup, find_duplicate_images
from lot_bot.catalog.validation import ALLOWED_IMAGE_FORMATS
from lot_bot.config.paths import get_paths

logger = logging.getLogger(__name__)

#: Requisitos oficiales de Wallapop. Se rellena desde la documentacion oficial;
#: `None` significa "no documentado todavia" y LOT Bot no lo da por valido.
WALLAPOP_IMAGE_REQUIREMENTS: dict[str, Any] = {
    "max_size_bytes": None,
    "min_width": None,
    "min_height": None,
    "max_width": None,
    "max_height": None,
    "allowed_formats": None,  # p.ej. {"JPG", "JPEG", "PNG"}
    "max_images_per_listing": None,
    "source": "pendiente de la documentacion oficial de Wallapop",
}

#: Limites propios de LOT Bot (prudentes, aplicables siempre).
LOT_BOT_MIN_DIMENSION = 400
LOT_BOT_MAX_SIZE_BYTES = 12 * 1024 * 1024


class ImageValidationError(ValueError):
    """La imagen no cumple los requisitos."""


@dataclass(slots=True)
class ImageInfo:
    """Metadatos de una fotografia importada."""

    path: str
    original_name: str
    file_format: str
    width: int
    height: int
    size_bytes: int
    content_hash: str
    perceptual_hash: str
    is_primary: bool = False
    position: int = 0
    warnings: list[str] = field(default_factory=list)
    source: str = "import"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImageService:
    """Importa, valida, ordena y deduplica fotografias."""

    def __init__(self, storage_root: Path | None = None) -> None:
        self.root = storage_root or get_paths().images
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Validacion
    # ------------------------------------------------------------------
    @staticmethod
    def detect_format(path: Path) -> str:
        suffix = path.suffix.upper().lstrip(".")
        return "JPEG" if suffix == "JPG" else suffix

    def validate_file(self, path: str | Path) -> tuple[bool, list[str], list[str]]:
        """Valida una imagen. Devuelve (valida, errores, avisos)."""
        file_path = Path(path)
        errors: list[str] = []
        warnings: list[str] = []

        if not file_path.is_file():
            return False, [f"El fichero '{file_path}' no existe."], []

        suffix = file_path.suffix.upper().lstrip(".")
        if suffix not in ALLOWED_IMAGE_FORMATS:
            errors.append(
                f"Formato '{suffix or 'desconocido'}' no admitido. "
                f"Formatos válidos: {', '.join(sorted(ALLOWED_IMAGE_FORMATS))}."
            )

        size_bytes = file_path.stat().st_size
        if size_bytes == 0:
            errors.append("El fichero está vacío.")
        elif size_bytes > LOT_BOT_MAX_SIZE_BYTES:
            errors.append(
                f"La imagen pesa {size_bytes / 1_048_576:.1f} MB y supera el límite de "
                f"{LOT_BOT_MAX_SIZE_BYTES / 1_048_576:.0f} MB."
            )

        try:
            with Image.open(file_path) as img:
                width, height = img.size
                real_format = (img.format or "").upper()
        except (UnidentifiedImageError, OSError) as exc:
            return False, errors + [f"No se puede leer la imagen: {exc}"], warnings

        if real_format and real_format not in {"JPEG", "PNG", "WEBP"}:
            errors.append(f"El contenido real del fichero es '{real_format}', no admitido.")
        if suffix and real_format and self.detect_format(file_path) != real_format:
            warnings.append(
                f"La extensión ({suffix}) no coincide con el contenido real ({real_format})."
            )
        if min(width, height) < LOT_BOT_MIN_DIMENSION:
            warnings.append(
                f"Resolución baja ({width}x{height}). Recomendado: al menos "
                f"{LOT_BOT_MIN_DIMENSION} px en el lado corto."
            )

        errors.extend(self._check_wallapop_requirements(real_format, width, height, size_bytes))
        if WALLAPOP_IMAGE_REQUIREMENTS.get("allowed_formats") is None:
            warnings.append(
                "Los requisitos oficiales de imagen de Wallapop no están configurados: "
                "la validación final la hará Wallapop al publicar."
            )
        return not errors, errors, warnings

    @staticmethod
    def _check_wallapop_requirements(
        image_format: str, width: int, height: int, size_bytes: int
    ) -> list[str]:
        """Comprueba los requisitos oficiales SOLO si estan configurados."""
        errors: list[str] = []
        requirements = WALLAPOP_IMAGE_REQUIREMENTS

        allowed = requirements.get("allowed_formats")
        if allowed and image_format and image_format not in {f.upper() for f in allowed}:
            errors.append(f"Wallapop no admite el formato '{image_format}'.")

        max_bytes = requirements.get("max_size_bytes")
        if max_bytes and size_bytes > max_bytes:
            errors.append(
                f"La imagen supera el tamaño máximo admitido por Wallapop "
                f"({max_bytes / 1_048_576:.1f} MB)."
            )
        for key, value, label in (
            ("min_width", width, "ancho mínimo"),
            ("min_height", height, "alto mínimo"),
        ):
            limit = requirements.get(key)
            if limit and value < limit:
                errors.append(f"La imagen no alcanza el {label} exigido por Wallapop ({limit} px).")
        for key, value, label in (
            ("max_width", width, "ancho máximo"),
            ("max_height", height, "alto máximo"),
        ):
            limit = requirements.get(key)
            if limit and value > limit:
                errors.append(f"La imagen supera el {label} admitido por Wallapop ({limit} px).")
        return errors

    # ------------------------------------------------------------------
    # Hashes
    # ------------------------------------------------------------------
    @staticmethod
    def content_hash(path: str | Path) -> str:
        """SHA-256 del contenido: detecta ficheros byte a byte identicos."""
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def perceptual_hash(path: str | Path, size: int = 8) -> str:
        """Hash perceptual (average hash): detecta imagenes visualmente iguales
        aunque cambien de tamano, formato o compresion."""
        try:
            with Image.open(path) as img:
                grayscale = img.convert("L").resize((size, size), Image.Resampling.LANCZOS)
                pixels = list(grayscale.getdata())
        except (UnidentifiedImageError, OSError):
            return ""
        average = sum(pixels) / len(pixels)
        bits = "".join("1" if pixel >= average else "0" for pixel in pixels)
        return f"{int(bits, 2):0{size * size // 4}x}"

    # ------------------------------------------------------------------
    # Importacion
    # ------------------------------------------------------------------
    def import_image(
        self, source_path: str | Path, product_sku: str, position: int = 0, is_primary: bool = False
    ) -> ImageInfo:
        """Copia la imagen al almacen local y devuelve sus metadatos."""
        source = Path(source_path)
        valid, errors, warnings = self.validate_file(source)
        if not valid:
            raise ImageValidationError(
                f"'{source.name}' no es válida: " + " ".join(errors)
            )

        destination_dir = self.root / _safe_name(product_sku)
        destination_dir.mkdir(parents=True, exist_ok=True)
        content = self.content_hash(source)
        destination = destination_dir / f"{content[:16]}{source.suffix.lower()}"
        if not destination.exists():
            shutil.copy2(source, destination)

        with Image.open(destination) as img:
            width, height = img.size
            real_format = (img.format or self.detect_format(destination)).upper()

        return ImageInfo(
            path=str(destination),
            original_name=source.name,
            file_format=real_format,
            width=width,
            height=height,
            size_bytes=destination.stat().st_size,
            content_hash=content,
            perceptual_hash=self.perceptual_hash(destination),
            is_primary=is_primary,
            position=position,
            warnings=warnings,
        )

    def import_many(
        self, paths: list[str | Path], product_sku: str, skip_duplicates: bool = True
    ) -> tuple[list[ImageInfo], list[str]]:
        """Importa varias imagenes. Devuelve (importadas, incidencias)."""
        imported: list[ImageInfo] = []
        problems: list[str] = []
        seen_hashes: set[str] = set()

        for path in paths:
            try:
                info = self.import_image(path, product_sku, position=len(imported))
            except ImageValidationError as exc:
                problems.append(str(exc))
                continue
            if skip_duplicates and info.content_hash in seen_hashes:
                problems.append(f"'{info.original_name}' se ha omitido por estar repetida.")
                continue
            seen_hashes.add(info.content_hash)
            info.is_primary = not imported
            info.position = len(imported)
            imported.append(info)
        return imported, problems

    # ------------------------------------------------------------------
    # Duplicados y ordenacion
    # ------------------------------------------------------------------
    @staticmethod
    def find_duplicates(images: list[dict[str, Any]]) -> list[DuplicateGroup]:
        return find_duplicate_images(images)

    @staticmethod
    def deduplicate(images: list[ImageInfo]) -> tuple[list[ImageInfo], list[ImageInfo]]:
        """Separa las imagenes unicas de las repetidas (sin borrar nada)."""
        unique: list[ImageInfo] = []
        duplicates: list[ImageInfo] = []
        seen: set[str] = set()
        for image in images:
            key = image.content_hash or image.perceptual_hash
            if key and key in seen:
                duplicates.append(image)
            else:
                if key:
                    seen.add(key)
                unique.append(image)
        return unique, duplicates

    @staticmethod
    def reorder(images: list[ImageInfo], order: list[str]) -> list[ImageInfo]:
        """Reordena por nombre original; las no mencionadas van al final."""
        index = {name: position for position, name in enumerate(order)}
        ordered = sorted(images, key=lambda i: index.get(i.original_name, len(order)))
        for position, image in enumerate(ordered):
            image.position = position
            image.is_primary = position == 0
        return ordered

    # ------------------------------------------------------------------
    # Generacion por IA (requiere proveedor autorizado)
    # ------------------------------------------------------------------
    @staticmethod
    def generation_available() -> bool:
        """No hay proveedor de generacion de imagenes configurado."""
        return False

    def generate_image(self, prompt: str) -> ImageInfo:
        raise NotImplementedError(
            "La generación de imágenes por IA requiere integrar un proveedor autorizado. "
            "No hay ninguno configurado en esta instalación."
        )


def _safe_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in value)[:60] or "producto"
