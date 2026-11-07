"""Cliente de FLUX.2 [pro] de Black Forest Labs (API oficial).

Flujo documentado por Black Forest Labs (https://help.bfl.ai, «Quickstart»):

  1. POST {BASE}/v1/flux-2-pro  con cabecera `x-key` y JSON {prompt, width, height, seed}
     → {"id": ..., "polling_url": ...}
  2. GET polling_url (con `x-key`) hasta que "status" sea "Ready"
     → {"status": "Ready", "result": {"sample": "<url firmada>"}}
  3. Descargar `result.sample` enseguida: la URL caduca a los 10 minutos.

Saldo: GET {BASE}/v1/credits → {"credits": <número>}.

Edición con referencias (misma dirección, documentada para FLUX.2 [pro]):
`input_image`, `input_image_2` … `input_image_8` con la imagen en base64. Así
se mantiene el producto y se cambia el estilo, la habitación o el encuadre.

Se usa siempre la `polling_url` devuelta, nunca una construida a mano. Solo
"Pending" significa «seguir esperando»: cualquier otro estado distinto de
"Ready" se trata como fallo y se muestra tal cual.

La clave nunca se escribe en los registros: se registra en el filtro de
redacción y los mensajes de error no incluyen cabeceras.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from lot_bot.images.generation.base import GenerationError, GenerationTimeout, ImageGenerator
from lot_bot.logs.redaction import register_secret

logger = logging.getLogger(__name__)

BFL_API_BASE = "https://api.bfl.ai"
FLUX_2_PRO_PATH = "/v1/flux-2-pro"
CREDITS_PATH = "/v1/credits"

#: Tamaño por defecto: 4:3, habitual en fotos de producto.
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 768


MAX_REFERENCE_IMAGES = 8


class FluxImageService(ImageGenerator):
    name = "FLUX.2 Pro (Black Forest Labs)"
    is_demo = False
    supports_reference = True

    def __init__(
        self,
        api_key_provider: Callable[[], str | None],
        *,
        base_url: str = BFL_API_BASE,
        timeout_seconds: float = 180.0,
        poll_interval: float = 1.5,
        http_client_factory: Callable[[], httpx.Client] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
    ) -> None:
        self._key_provider = api_key_provider
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._poll = poll_interval
        self._client_factory = http_client_factory or (lambda: httpx.Client(timeout=30.0))
        self._sleep = sleep
        self._clock = clock
        self._width = width
        self._height = height

    # ------------------------------------------------------------------
    def _key(self) -> str:
        key = self._key_provider()
        if not key:
            raise GenerationError(
                "No hay clave de FLUX.2 Pro. Añádela en Configuración → IA / Imágenes.",
                code="NO_API_KEY",
            )
        register_secret(key)
        return key

    def _headers(self) -> dict[str, str]:
        return {
            "x-key": self._key(),
            "accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _raise_for_status(response: httpx.Response, action: str) -> None:
        code = response.status_code
        if code < 400:
            return
        if code in (401, 403):
            raise GenerationError(
                "La clave de FLUX.2 Pro no es válida o no tiene permiso.", code="INVALID_KEY"
            )
        if code == 402:
            raise GenerationError(
                "No quedan créditos en la cuenta de Black Forest Labs.", code="NO_CREDITS"
            )
        if code == 429:
            raise GenerationError(
                "Black Forest Labs limita las peticiones ahora mismo. Se puede reintentar "
                "más tarde.",
                code="RATE_LIMITED",
            )
        raise GenerationError(
            f"Black Forest Labs ha respondido con un error ({code}) al {action}.",
            code=f"HTTP_{code}",
        )

    # ------------------------------------------------------------------
    def get_credits(self) -> float | None:
        """Saldo de créditos. También sirve para «Probar conexión»."""
        with self._client_factory() as client:
            try:
                response = client.get(f"{self._base}{CREDITS_PATH}", headers=self._headers())
            except httpx.HTTPError as exc:
                raise GenerationError(
                    f"No se ha podido contactar con Black Forest Labs ({type(exc).__name__}).",
                    code="NETWORK",
                ) from exc
            self._raise_for_status(response, "consultar el saldo")
            data = response.json()
        value = data.get("credits") if isinstance(data, dict) else None
        return float(value) if isinstance(value, int | float) else None

    def test_connection(self) -> float | None:
        return self.get_credits()

    # ------------------------------------------------------------------
    def generate(
        self,
        prompt: str,
        seed: int,
        destination: Path,
        input_images: list[Path] | None = None,
        **_: Any,
    ) -> Path:
        headers = self._headers()
        body: dict[str, Any] = {"prompt": prompt, "seed": int(seed)}
        references = list(input_images or [])[:MAX_REFERENCE_IMAGES]
        if references:
            # Con referencia, FLUX conserva las proporciones de la imagen.
            for index, image in enumerate(references):
                key = "input_image" if index == 0 else f"input_image_{index + 1}"
                body[key] = base64.b64encode(Path(image).read_bytes()).decode("ascii")
        else:
            body["width"] = self._width
            body["height"] = self._height
        with self._client_factory() as client:
            try:
                response = client.post(f"{self._base}{FLUX_2_PRO_PATH}", headers=headers, json=body)
            except httpx.HTTPError as exc:
                raise GenerationError(
                    f"No se ha podido contactar con Black Forest Labs ({type(exc).__name__}).",
                    code="NETWORK",
                ) from exc
            self._raise_for_status(response, "enviar la generación")
            submitted = response.json()
            polling_url = submitted.get("polling_url") if isinstance(submitted, dict) else None
            if not polling_url:
                raise GenerationError(
                    "La respuesta de Black Forest Labs no incluye «polling_url».",
                    code="BAD_RESPONSE",
                )

            deadline = self._clock() + self._timeout
            sample_url: str | None = None
            while True:
                if self._clock() > deadline:
                    raise GenerationTimeout(self._timeout)
                try:
                    poll = client.get(polling_url, headers=headers)
                except httpx.HTTPError as exc:
                    raise GenerationError(
                        f"Se ha perdido la conexión con Black Forest Labs ({type(exc).__name__}).",
                        code="NETWORK",
                    ) from exc
                self._raise_for_status(poll, "consultar el resultado")
                data = poll.json()
                status = str(data.get("status", "")) if isinstance(data, dict) else ""
                if status == "Ready":
                    result = data.get("result") or {}
                    sample_url = result.get("sample") if isinstance(result, dict) else None
                    if not sample_url:
                        raise GenerationError(
                            "El resultado no incluye la imagen («result.sample»).",
                            code="BAD_RESPONSE",
                        )
                    break
                if status == "Pending":
                    self._sleep(self._poll)
                    continue
                # «Error», «Failed», «Request Moderated», «Content Moderated»...
                raise GenerationError(
                    f"Black Forest Labs no ha generado la imagen (estado: {status or 'desconocido'}).",
                    code="STATUS_" + (status.upper().replace(" ", "_") or "UNKNOWN"),
                )

            try:
                image = client.get(sample_url)
            except httpx.HTTPError as exc:
                raise GenerationError(
                    f"No se ha podido descargar la imagen ({type(exc).__name__}).",
                    code="DOWNLOAD",
                ) from exc
            if image.status_code >= 400:
                raise GenerationError(
                    f"No se ha podido descargar la imagen ({image.status_code}).", code="DOWNLOAD"
                )
        content_type = image.headers.get("content-type", "")
        suffix = ".png" if "png" in content_type else ".webp" if "webp" in content_type else ".jpg"
        path = destination.with_suffix(suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image.content)
        logger.info("Imagen generada con FLUX.2 Pro: %s", path.name)
        return path
