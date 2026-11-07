"""Generador de DEMO: imágenes de prueba locales, sin red y sin créditos.

Cada semilla produce una imagen distinta (y la misma semilla, la misma
imagen), lo que permite probar la detección de repetidas.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw

from lot_bot.images.generation.base import ImageGenerator


class DemoImageGenerator(ImageGenerator):
    name = "Imágenes de demostración"
    is_demo = True
    supports_reference = True

    def __init__(self, size: tuple[int, int] = (1024, 768)) -> None:
        self._size = size

    def generate(
        self, prompt: str, seed: int, destination: Path, input_images=None, **_
    ) -> Path:
        if input_images:
            return self._from_reference(prompt, seed, destination, Path(input_images[0]))
        rnd = random.Random(f"{seed}|{prompt}")
        width, height = self._size
        image = Image.new("RGB", self._size)
        draw = ImageDraw.Draw(image)
        top = tuple(rnd.randint(170, 240) for _ in range(3))
        bottom = tuple(rnd.randint(90, 170) for _ in range(3))
        for y in range(height):
            t = y / height
            draw.line(
                [(0, y), (width, y)],
                fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)),
            )
        # Una «cama» esquemática en posición y color distintos por semilla.
        bx = rnd.randint(80, 360)
        by = rnd.randint(300, 420)
        bw = rnd.randint(420, 560)
        base = tuple(rnd.randint(60, 200) for _ in range(3))
        draw.rectangle([bx, by, bx + bw, by + 170], fill=base)
        draw.rectangle([bx, by - 60, bx + bw, by], fill=(235, 235, 230))
        for _ in range(rnd.randint(3, 7)):
            x, y = rnd.randint(0, width), rnd.randint(0, height // 3)
            r = rnd.randint(20, 90)
            draw.ellipse([x, y, x + r, y + r], fill=tuple(rnd.randint(100, 255) for _ in range(3)))
        draw.rectangle([0, height - 60, width, height], fill=(20, 20, 20))
        draw.text((20, height - 42), f"IMAGEN DE DEMOSTRACION  #{seed}", fill=(255, 255, 255))
        path = destination.with_suffix(".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "PNG")
        return path

    def _from_reference(self, prompt: str, seed: int, destination: Path, reference: Path) -> Path:
        """Simula una edición: la referencia, con otro tono y marco según la semilla."""
        rnd = random.Random(f"{seed}|{prompt}|ref")
        with Image.open(reference) as source:
            base = source.convert("RGB").resize(self._size)
        tint = Image.new("RGB", self._size, tuple(rnd.randint(40, 220) for _ in range(3)))
        image = Image.blend(base, tint, 0.35)
        draw = ImageDraw.Draw(image)
        width, height = self._size
        margin = rnd.randint(10, 60)
        draw.rectangle([margin, margin, width - margin, height - margin], outline=tint.getpixel((0, 0)), width=8)
        draw.rectangle([0, height - 60, width, height], fill=(20, 20, 20))
        draw.text((20, height - 42), f"IMAGEN DE DEMOSTRACION (edicion)  #{seed}", fill=(255, 255, 255))
        path = destination.with_suffix(".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "PNG")
        return path
