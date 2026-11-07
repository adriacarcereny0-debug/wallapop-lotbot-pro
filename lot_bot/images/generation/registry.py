"""Generación de imágenes únicas: registra cada imagen y evita repeticiones.

Por cada imagen se guarda: hash del fichero (SHA-256), hash perceptual (dHash
de 64 bits), prompt, semilla, fecha, anuncio y cuenta. Si una imagen nueva es
idéntica (mismo SHA-256) o casi idéntica (dHash a distancia ≤ umbral) a una ya
registrada, se descarta y se genera otra variación.
"""

from __future__ import annotations

import hashlib
import logging
import random
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps
from sqlalchemy import select

from lot_bot.database.engine import Database
from lot_bot.database.models import GeneratedImage
from lot_bot.images.generation.base import GenerationError, ImageGenerator
from lot_bot.images.generation.prompts import (
    ProductImageSpec,
    build_edit_prompt,
    build_scene_prompt,
    scene_for,
)

logger = logging.getLogger(__name__)

#: Distancia de Hamming (sobre 64 bits) por debajo de la cual dos imágenes se
#: consideran la misma.
DUPLICATE_THRESHOLD = 4
#: Lado mayor tras «Mejorar».
ENHANCE_TARGET = 2048


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dhash(path: Path) -> str:
    """Hash perceptual por diferencias (dHash) de 64 bits, en hexadecimal."""
    with Image.open(path) as image:
        small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


@dataclass(slots=True)
class GeneratedImageResult:
    path: Path
    prompt: str
    seed: int
    attempts: int
    provider: str
    is_demo: bool
    scene: dict = field(default_factory=dict)
    image_id: int | None = None
    operation: str = "generar"


class ImageGenerationService:
    def __init__(
        self,
        database: Database,
        generator: ImageGenerator,
        output_dir: Path,
        *,
        max_attempts: int = 3,
        rng: random.Random | None = None,
    ) -> None:
        self._db = database
        self.generator = generator
        self._dir = output_dir
        self._max_attempts = max_attempts
        self._rng = rng or random.Random()

    def set_generator(self, generator: ImageGenerator) -> None:
        self.generator = generator

    # ------------------------------------------------------------------
    def find_duplicate(self, sha: str, phash: str) -> GeneratedImage | None:
        with self._db.session_scope() as session:
            exact = session.scalars(
                select(GeneratedImage).where(GeneratedImage.file_hash == sha)
            ).first()
            if exact is not None:
                session.expunge(exact)
                return exact
            for row in session.scalars(select(GeneratedImage)).all():
                if hamming(row.perceptual_hash, phash) <= DUPLICATE_THRESHOLD:
                    session.expunge(row)
                    return row
        return None

    def _register(
        self,
        path: Path,
        *,
        prompt: str,
        seed: int | None,
        provider: str,
        subject: str,
        operation: str,
        scene: dict | None = None,
        source_image: str | None = None,
        account_ref: str | None = None,
        task_id: int | None = None,
    ) -> int:
        with self._db.session_scope() as session:
            row = GeneratedImage(
                path=str(path),
                file_hash=file_sha256(path),
                perceptual_hash=dhash(path),
                prompt=prompt,
                seed=seed,
                provider=provider,
                subject=subject[:200],
                account_ref=account_ref,
                task_id=task_id,
                operation=operation,
                scene=dict(scene or {}),
                source_image=source_image,
            )
            session.add(row)
            session.flush()
            return row.id

    def _unique_attempts(
        self,
        make,
        *,
        subject: str,
        operation: str,
        account_ref: str | None,
        task_id: int | None,
        source: Path | None = None,
    ) -> GeneratedImageResult:
        """Repite `make(attempt, seed)` hasta obtener una imagen no repetida."""
        last_error = ""
        source_hashes = (file_sha256(source), dhash(source)) if source else None
        for attempt in range(1, self._max_attempts + 1):
            seed = self._rng.randint(1, 2_000_000_000)
            path, prompt, scene = make(attempt, seed)
            sha = file_sha256(path)
            phash = dhash(path)
            duplicate = self.find_duplicate(sha, phash)
            same_as_source = source_hashes is not None and (
                sha == source_hashes[0] or hamming(phash, source_hashes[1]) <= DUPLICATE_THRESHOLD
            )
            if duplicate is not None or same_as_source:
                last_error = (
                    "igual a la imagen de partida"
                    if same_as_source
                    else f"repetida con la imagen #{duplicate.id}"
                )
                logger.info("Imagen descartada por %s; se genera otra variación.", last_error)
                path.unlink(missing_ok=True)
                continue
            image_id = self._register(
                path,
                prompt=prompt,
                seed=seed,
                provider=self.generator.name,
                subject=subject,
                operation=operation,
                scene=scene,
                source_image=str(source) if source else None,
                account_ref=account_ref,
                task_id=task_id,
            )
            return GeneratedImageResult(
                path=path,
                prompt=prompt,
                seed=seed,
                attempts=attempt,
                provider=self.generator.name,
                is_demo=self.generator.is_demo,
                scene=dict(scene or {}),
                image_id=image_id,
                operation=operation,
            )
        raise GenerationError(
            f"No se ha conseguido una imagen distinta tras {self._max_attempts} intentos "
            f"({last_error}).",
            code="DUPLICATE",
        )

    def generate_unique(
        self,
        spec: ProductImageSpec,
        *,
        variation: int,
        subject: str,
        account_ref: str | None = None,
        task_id: int | None = None,
        scene: dict | None = None,
        preferred_rooms: list[str] | None = None,
    ) -> GeneratedImageResult:
        """Genera desde cero una imagen que no se repite con ninguna anterior."""

        def make(attempt: int, seed: int):
            chosen = dict(scene) if scene else scene_for(
                variation + (attempt - 1) * 5, preferred_rooms
            )
            prompt = build_scene_prompt(spec, chosen)
            path = self.generator.generate(prompt, seed, self._dir / f"gen-{uuid.uuid4().hex[:12]}")
            return path, prompt, chosen

        return self._unique_attempts(
            make, subject=subject, operation="generar", account_ref=account_ref, task_id=task_id
        )

    def edit(
        self,
        operation: str,
        source: Path,
        *,
        scene: dict | None = None,
        spec: ProductImageSpec | None = None,
        subject: str = "",
    ) -> GeneratedImageResult:
        """Nueva versión de una imagen manteniendo el producto.

        `operation`: «estilo», «habitacion» o «referencia».
        """
        source = Path(source)
        if not source.is_file():
            raise GenerationError("No se encuentra la imagen elegida.", code="NO_SOURCE")
        if not self.generator.supports_reference:
            raise GenerationError(
                f"{self.generator.name} no admite imágenes de referencia.", code="NO_REFERENCE"
            )
        base_scene = {"habitacion": "dormitorio_blanco", "luz": "dia_luminoso",
                      "angulo": "tres_cuartos_pie", "estilo": "natural", **(scene or {})}

        def make(attempt: int, seed: int):
            chosen = dict(base_scene)
            if attempt > 1 and not (scene or {}).get("angulo"):
                chosen["angulo"] = scene_for(attempt)["angulo"]
            prompt = build_edit_prompt(operation, chosen, spec)
            path = self.generator.generate(
                prompt, seed, self._dir / f"gen-{uuid.uuid4().hex[:12]}", input_images=[source]
            )
            return path, prompt, chosen

        return self._unique_attempts(
            make,
            subject=subject or f"{operation}: {source.name}",
            operation=operation,
            account_ref=None,
            task_id=None,
            source=source,
        )

    def enhance(self, source: Path, *, subject: str = "") -> GeneratedImageResult:
        """Mejora LOCAL de calidad: más resolución (hasta 2048 px de lado),
        nitidez y contraste suave. No usa IA ni inventa detalles."""
        source = Path(source)
        if not source.is_file():
            raise GenerationError("No se encuentra la imagen elegida.", code="NO_SOURCE")
        with Image.open(source) as original:
            image = original.convert("RGB")
        longest = max(image.size)
        if longest < ENHANCE_TARGET:
            factor = ENHANCE_TARGET / longest
            image = image.resize(
                (round(image.width * factor), round(image.height * factor)),
                Image.Resampling.LANCZOS,
            )
        image = ImageOps.autocontrast(image, cutoff=1)
        image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))
        path = self._dir / f"mejorada-{uuid.uuid4().hex[:12]}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "JPEG", quality=92)
        image_id = self._register(
            path,
            prompt="Mejora local: resolución, nitidez y contraste",
            seed=None,
            provider="LOT Bot (local)",
            subject=subject or f"mejorar: {source.name}",
            operation="mejorar",
            source_image=str(source),
        )
        return GeneratedImageResult(
            path=path, prompt="", seed=0, attempts=1, provider="LOT Bot (local)",
            is_demo=False, scene={}, image_id=image_id, operation="mejorar",
        )

    def add_own_image(self, source: Path, *, subject: str = "") -> GeneratedImageResult:
        """Guarda una foto propia del usuario (para usarla como referencia)."""
        source = Path(source)
        with Image.open(source) as image:
            image.verify()  # que sea una imagen de verdad
        target = self._dir / f"propia-{uuid.uuid4().hex[:12]}{source.suffix.lower() or '.jpg'}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        duplicate = self.find_duplicate(file_sha256(target), dhash(target))
        if duplicate is not None:
            target.unlink(missing_ok=True)
            raise GenerationError(
                f"Esa imagen ya está guardada (imagen #{duplicate.id}).", code="DUPLICATE"
            )
        image_id = self._register(
            target, prompt="", seed=None, provider="Foto propia",
            subject=subject or source.name, operation="propia",
        )
        return GeneratedImageResult(
            path=target, prompt="", seed=0, attempts=1, provider="Foto propia",
            is_demo=False, scene={}, image_id=image_id, operation="propia",
        )

    def get_image(self, image_id: int) -> dict | None:
        with self._db.session_scope() as session:
            row = session.get(GeneratedImage, image_id)
            return _row_dict(row) if row else None

    def delete_images(self, image_ids: list[int]) -> int:
        """Borra imágenes de la lista (y su fichero, SOLO si está en la carpeta
        de LOT Bot: las fotos originales de tu ordenador nunca se tocan).
        Las que ya se usan en tus anuncios no se ven afectadas: allí hay una copia."""
        base = self._dir.resolve()
        removed = 0
        with self._db.session_scope() as session:
            for image_id in image_ids:
                row = session.get(GeneratedImage, image_id)
                if row is None:
                    continue
                path = Path(row.path)
                try:
                    if path.resolve().is_relative_to(base):
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
                session.delete(row)
                removed += 1
        return removed

    def history(self, limit: int = 50) -> list[dict]:
        with self._db.session_scope() as session:
            rows = session.scalars(
                select(GeneratedImage).order_by(GeneratedImage.id.desc()).limit(limit)
            ).all()
            return [_row_dict(r) for r in rows]


def _row_dict(r: GeneratedImage) -> dict:
    return {
        "id": r.id,
        "ruta": r.path,
        "hash": r.file_hash,
        "prompt": r.prompt,
        "fecha": r.created_at,
        "anuncio": r.subject,
        "cuenta": r.account_ref,
        "operacion": r.operation or "generar",
        "escena": dict(r.scene or {}),
        "origen": r.source_image,
        "proveedor": r.provider,
        "existe": Path(r.path).is_file(),
    }
