"""Contrato común de los generadores de imágenes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class GenerationError(RuntimeError):
    """La generación ha fallado. `user_message` se puede mostrar tal cual."""

    def __init__(self, user_message: str, *, code: str = "GENERATION_ERROR") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.code = code


class GenerationTimeout(GenerationError):
    def __init__(self, seconds: float) -> None:
        super().__init__(
            f"La imagen no ha estado lista en {int(seconds)} segundos. Se puede reintentar.",
            code="TIMEOUT",
        )


class ImageGenerator(ABC):
    #: Nombre visible («FLUX.2 Pro», «Demostración»).
    name: str = ""
    #: True si no consume créditos ni contacta con ningún servicio.
    is_demo: bool = False

    #: True si admite imágenes de referencia (editar, cambiar habitación...).
    supports_reference: bool = False

    @abstractmethod
    def generate(self, prompt: str, seed: int, destination: Path, **kwargs) -> Path:
        """Genera una imagen y la guarda en `destination` (sin extensión).

        `input_images` (opcional): imágenes de referencia del producto.
        Devuelve la ruta final del fichero.
        """
