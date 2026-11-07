"""Generación de imágenes: prompts, cliente FLUX.2 Pro (simulado con
httpx.MockTransport, sin red ni créditos), detección de repetidas y
protección de la clave."""

from __future__ import annotations

import logging
import random
from pathlib import Path

import httpx
import pytest

from lot_bot.config.api_keys import FLUX, ApiKeyStore
from lot_bot.images.generation import (
    DemoImageGenerator,
    FluxImageService,
    GenerationError,
    GenerationTimeout,
    ImageGenerationService,
    build_prompt,
    spec_from_master,
)
from lot_bot.images.generation.base import ImageGenerator
from lot_bot.images.generation.registry import dhash, hamming
from lot_bot.logs.redaction import RedactingFilter, redact

FAKE_KEY = "bfl-test-key-0123456789abcdef"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
@pytest.fixture()
def master(app):
    return app.master_ads.get(None)


def test_prompt_con_los_datos_reales_del_canape(master):
    spec = spec_from_master(master)
    prompt = build_prompt(spec, 0)
    assert '"Canapé"' in prompt
    assert "grey and white" in prompt  # «Gris y Blanco»
    assert "wood" in prompt  # «Madera»
    assert "mattress" in prompt  # la descripción habla de «colchón»
    assert '"Dormitorio"' in prompt  # lo no clasificable va literal
    assert "Photorealistic" in prompt and "real home" in prompt
    assert "No people" in prompt and "No text" in prompt and "no logos" in prompt
    assert "no price tags" in prompt


def test_el_prompt_no_inventa_ni_filtra_datos_comerciales(master):
    prompt = build_prompt(spec_from_master(master), 3)
    for prohibido in ("230", "270", "290", "11,44", "€", "603710542", "WhatsApp", "abatible"):
        assert prohibido not in prompt
    assert "size" not in prompt  # sin medida concreta no se pone ninguna


def test_la_medida_solo_si_el_anuncio_la_indica(master):
    assert "size 135x190 cm" in build_prompt(spec_from_master(master, size="135x190"), 0)


def test_cada_anuncio_tiene_un_prompt_distinto_pero_el_mismo_producto(master):
    spec = spec_from_master(master)
    prompts = {build_prompt(spec, i) for i in range(10)}
    assert len(prompts) == 10
    producto = spec.product_sentence()
    assert all(producto in p for p in prompts)


# ---------------------------------------------------------------------------
# Cliente FLUX.2 Pro (sin red)
# ---------------------------------------------------------------------------
class FakeBFL:
    """Imita las respuestas documentadas de la API de Black Forest Labs."""

    def __init__(self, statuses=("Pending", "Ready"), submit_status=200, credits=42.5):
        self.statuses = list(statuses)
        self.submit_status = submit_status
        self.credits = credits
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == "https://api.bfl.ai/v1/credits":
            return httpx.Response(200, json={"credits": self.credits})
        if url == "https://api.bfl.ai/v1/flux-2-pro":
            if self.submit_status != 200:
                return httpx.Response(self.submit_status, json={"detail": "x"})
            return httpx.Response(
                200, json={"id": "abc", "polling_url": "https://api.eu.bfl.ai/v1/get_result?id=abc"}
            )
        if url.startswith("https://api.eu.bfl.ai/v1/get_result"):
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            body = {"status": status}
            if status == "Ready":
                body["result"] = {"sample": "https://delivery.example/sample.jpg"}
            return httpx.Response(200, json=body)
        if url == "https://delivery.example/sample.jpg":
            return httpx.Response(200, content=b"\xff\xd8JPEGDATA", headers={"content-type": "image/jpeg"})
        return httpx.Response(404)


def flux(fake, key=FAKE_KEY, **kwargs):
    return FluxImageService(
        lambda: key,
        http_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(fake)),
        sleep=lambda s: None,
        **kwargs,
    )


def test_flux_envia_espera_y_descarga(tmp_path):
    fake = FakeBFL(statuses=("Pending", "Pending", "Ready"))
    path = flux(fake).generate("un canapé", 7, tmp_path / "img")
    assert path.suffix == ".jpg" and path.read_bytes() == b"\xff\xd8JPEGDATA"
    envio = fake.requests[0]
    assert envio.method == "POST" and envio.headers["x-key"] == FAKE_KEY
    import json

    body = json.loads(envio.content)
    assert body["prompt"] == "un canapé" and body["seed"] == 7
    # Se usa la polling_url devuelta, nunca una construida a mano.
    assert any("api.eu.bfl.ai" in str(r.url) for r in fake.requests)


def test_flux_saldo(tmp_path):
    assert flux(FakeBFL(credits=12)).get_credits() == 12.0


@pytest.mark.parametrize(
    ("estado", "codigo"),
    [("Error", "STATUS_ERROR"), ("Content Moderated", "STATUS_CONTENT_MODERATED"), ("Failed", "STATUS_FAILED")],
)
def test_flux_estado_de_error(tmp_path, estado, codigo):
    with pytest.raises(GenerationError) as info:
        flux(FakeBFL(statuses=(estado,))).generate("x", 1, tmp_path / "img")
    assert info.value.code == codigo


@pytest.mark.parametrize(
    ("http", "codigo"), [(401, "INVALID_KEY"), (402, "NO_CREDITS"), (429, "RATE_LIMITED"), (500, "HTTP_500")]
)
def test_flux_errores_http(tmp_path, http, codigo):
    with pytest.raises(GenerationError) as info:
        flux(FakeBFL(submit_status=http)).generate("x", 1, tmp_path / "img")
    assert info.value.code == codigo


def test_flux_timeout(tmp_path):
    reloj = {"t": 0.0}

    def avanza(_s):
        reloj["t"] += 10

    service = FluxImageService(
        lambda: FAKE_KEY,
        http_client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(FakeBFL(statuses=("Pending",)))
        ),
        sleep=avanza,
        clock=lambda: reloj["t"],
        timeout_seconds=30,
    )
    with pytest.raises(GenerationTimeout):
        service.generate("x", 1, tmp_path / "img")


def test_flux_sin_clave(tmp_path):
    with pytest.raises(GenerationError) as info:
        flux(FakeBFL(), key=None).generate("x", 1, tmp_path / "img")
    assert info.value.code == "NO_API_KEY"


def test_flux_error_de_red(tmp_path):
    def caida(request):
        raise httpx.ConnectError("sin red")

    with pytest.raises(GenerationError) as info:
        flux(caida).generate("x", 1, tmp_path / "img")
    assert info.value.code == "NETWORK"
    assert FAKE_KEY not in info.value.user_message


# ---------------------------------------------------------------------------
# Repetidas
# ---------------------------------------------------------------------------
def test_dhash_detecta_la_misma_imagen(tmp_path):
    gen = DemoImageGenerator()
    a = gen.generate("p", 1, tmp_path / "a")
    b = gen.generate("p", 1, tmp_path / "b")
    c = gen.generate("p", 2, tmp_path / "c")
    assert hamming(dhash(a), dhash(b)) == 0
    assert hamming(dhash(a), dhash(c)) > 4


def test_una_imagen_repetida_se_descarta_y_se_genera_otra(database, tmp_path, master):
    semillas = iter([5, 5, 9])

    class Rng(random.Random):
        def randint(self, a, b):
            return next(semillas)

    class SamePrompt(DemoImageGenerator):
        def generate(self, prompt, seed, destination):
            return super().generate("fijo", seed, destination)

    service = ImageGenerationService(database, SamePrompt(), tmp_path, rng=Rng())
    spec = spec_from_master(master)
    first = service.generate_unique(spec, variation=0, subject="uno")
    second = service.generate_unique(spec, variation=1, subject="dos")
    assert second.attempts == 2  # la primera salió idéntica y se descartó
    assert first.path != second.path
    assert len(list(tmp_path.glob("gen-*"))) == 2  # el duplicado se borró
    historial = service.history()
    assert {h["anuncio"] for h in historial} == {"uno", "dos"}
    assert all(h["hash"] and h["prompt"] for h in historial)


def test_si_siempre_sale_la_misma_imagen_se_avisa(database, tmp_path, master):
    class Always(ImageGenerator):
        name = "fijo"

        def generate(self, prompt, seed, destination):
            return DemoImageGenerator().generate("x", 1, destination)

    service = ImageGenerationService(database, Always(), tmp_path)
    spec = spec_from_master(master)
    service.generate_unique(spec, variation=0, subject="a")
    with pytest.raises(GenerationError) as info:
        service.generate_unique(spec, variation=1, subject="b")
    assert info.value.code == "DUPLICATE"


def test_la_cola_da_una_imagen_distinta_a_cada_anuncio(app):
    queue = app.publish_queue
    refs = [a.internal_ref for a in app.accounts.list_accounts()]
    job = queue.enqueue_master(None, refs, copies=6, generate_images=True)
    queue.run_until_idle()
    tasks = queue.progress(job).tasks
    assert all(t["estado"] == "published" for t in tasks)
    imagenes = [t["imagen"] for t in tasks]
    assert len(set(imagenes)) == 6
    hashes = {h["hash"] for h in app.image_generation.history()}
    assert len(hashes) == 6
    # La imagen generada es la portada del anuncio publicado.
    for anuncio in app.master_ads.publications():
        assert Path(anuncio.image_urls[0]).name.startswith("gen-")


# ---------------------------------------------------------------------------
# DEMO: sin FLUX real
# ---------------------------------------------------------------------------
def test_en_demo_no_se_usa_flux_ni_la_red(app, monkeypatch):
    def prohibido(*args, **kwargs):
        raise AssertionError("En DEMO no se debe contactar con ningún servicio")

    monkeypatch.setattr(httpx.Client, "send", prohibido)
    app.api_keys.set(FLUX, FAKE_KEY)
    app.refresh_image_generator()
    assert app.image_generation.generator.is_demo
    queue = app.publish_queue
    job = queue.enqueue_master(None, [app.accounts.list_accounts()[0].internal_ref], 2, generate_images=True)
    queue.run_until_idle()
    assert queue.progress(job).published == 2


# ---------------------------------------------------------------------------
# Clave de API
# ---------------------------------------------------------------------------
def test_la_clave_se_guarda_cifrada(database, secret_box):
    store = ApiKeyStore(database, secret_box)
    store.set(FLUX, FAKE_KEY)
    from lot_bot.database.models import Setting

    with database.session_scope() as session:
        guardado = str(session.get(Setting, "claves_api_cifradas").value)
    assert FAKE_KEY not in guardado
    assert store.get(FLUX) == FAKE_KEY
    assert store.delete(FLUX) and store.get(FLUX) is None


@pytest.mark.parametrize("mala", ["", "   ", "con espacio"])
def test_claves_invalidas(database, secret_box, mala):
    with pytest.raises(ValueError):
        ApiKeyStore(database, secret_box).set(FLUX, mala)


def test_la_clave_no_aparece_en_los_logs(database, secret_box, caplog):
    ApiKeyStore(database, secret_box).set(FLUX, FAKE_KEY)
    logger = logging.getLogger("prueba.flux")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="prueba.flux"):
        logger.info("cabecera x-key: %s", FAKE_KEY)
        logger.info(f"clave={FAKE_KEY}")
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in redact(f"x-key: {FAKE_KEY}")


def test_la_clave_no_esta_en_el_codigo():
    import re

    patron = re.compile(r"""["']x-key["']\s*:\s*["'][A-Za-z0-9_\-]{8,}""")
    raiz = Path(__file__).resolve().parents[1] / "lot_bot"
    for fichero in raiz.rglob("*.py"):
        assert not patron.search(fichero.read_text(encoding="utf-8")), fichero
