"""ImageManager: fotografías de los anuncios del motor directo.

Antes de publicar:

1. Comprueba cada foto (existe, formato admitido, no vacía, legible) con las
   mismas reglas que `ImageService`.
2. Quita las repetidas (mismo contenido) y respeta el máximo por anuncio
   (`limits.max_images_per_item`, 10 por defecto).
3. Sube cada foto con la operación autorizada `upload_image` y recuerda la
   referencia remota por su huella de contenido: si la publicación se
   interrumpe y se reintenta, NO se vuelve a subir lo ya subido.

Si el acceso autorizado no incluye `upload_image`, el anuncio se envía con
las rutas locales (el servicio decide) y se deja constancia en el historial.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lot_bot.images.service import ImageService
from lot_bot.integration.wallapop_client import WallapopClient
from lot_bot.wallapop.capabilities import Capability

logger = logging.getLogger(__name__)


class ImagePreparationError(ValueError):
    """Las fotos de un anuncio no se pueden publicar (no se arregla reintentando)."""


@dataclass(slots=True)
class PreparedImage:
    path: str
    content_hash: str


@dataclass(slots=True)
class PreparedImages:
    images: list[PreparedImage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        return [i.path for i in self.images]


class ImageManager:
    """Valida, deduplica y sube las fotos de cada anuncio."""

    def __init__(self, image_service: ImageService | None = None) -> None:
        self._images = image_service or ImageService()

    # ------------------------------------------------------------------
    def validate_paths(self, paths: list[str]) -> list[str]:
        """Errores de las fotos (vacío = todo bien). Para validar al encolar."""
        errors: list[str] = []
        for raw in paths:
            ok, problems, _warnings = self._images.validate_file(raw)
            if not ok:
                errors.append(f"{Path(raw).name}: {'; '.join(problems)}")
        return errors

    def prepare(self, paths: list[str], max_images: int = 10) -> PreparedImages:
        """Comprueba las fotos y devuelve las que se publicarán, en orden."""
        result = PreparedImages()
        seen: set[str] = set()
        errors: list[str] = []
        for raw in paths:
            ok, problems, warnings = self._images.validate_file(raw)
            if not ok:
                errors.append(f"{Path(raw).name}: {'; '.join(problems)}")
                continue
            digest = self._images.content_hash(raw)
            if digest in seen:
                result.warnings.append(f"Foto repetida omitida: {Path(raw).name}")
                continue
            seen.add(digest)
            result.warnings.extend(w for w in warnings if "no están configurados" not in w)
            result.images.append(PreparedImage(path=str(raw), content_hash=digest))
        if errors:
            raise ImagePreparationError(
                "Hay fotografías que no se pueden publicar: " + " · ".join(errors)
            )
        if max_images and len(result.images) > max_images:
            result.warnings.append(
                f"Se publican las {max_images} primeras fotos (máximo por anuncio)."
            )
            result.images = result.images[:max_images]
        return result

    def upload(
        self,
        client: WallapopClient,
        account_ref: str,
        prepared: PreparedImages,
        already_uploaded: dict[str, str],
        on_uploaded: Callable[[str, str], None] | None = None,
    ) -> list[str]:
        """Sube lo que falte y devuelve las referencias remotas, en orden.

        `already_uploaded` ({hash: referencia}) evita repetir subidas; cada
        foto subida se avisa a `on_uploaded` para guardarla al momento (si el
        programa se cierra entre dos fotos, no se pierde la primera).
        """
        if not prepared.images:
            return []
        if not client.supports(Capability.UPLOAD_IMAGE):
            return []
        refs: list[str] = []
        for image in prepared.images:
            remote = already_uploaded.get(image.content_hash)
            if not remote:
                remote = client.upload_image(account_ref, image.path)
                already_uploaded[image.content_hash] = remote
                if on_uploaded is not None:
                    on_uploaded(image.content_hash, remote)
            refs.append(remote)
        return refs
