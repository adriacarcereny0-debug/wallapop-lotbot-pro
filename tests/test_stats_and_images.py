"""Estadísticas (sin inventar datos), histórico, análisis, optimización y
operaciones avanzadas de imagen. Todo offline: sin Wallapop ni FLUX reales."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from lot_bot.stats import NOT_AVAILABLE, show


def publicar(app, copias=4, imagenes=False):
    refs = [a.internal_ref for a in app.accounts.list_accounts()]
    app.publish_queue.enqueue_master(None, refs, copias, generate_images=imagenes)
    app.publish_queue.run_until_idle()
    return app.master_ads.publications()


# ---------------------------------------------------------------------------
# Estadísticas: nada inventado
# ---------------------------------------------------------------------------
def test_sin_mediciones_todo_es_no_disponible(app):
    publicados = publicar(app, 2)
    filas = {r.listing_id: r for r in app.stats.table()}
    for anuncio in publicados:
        fila = filas[anuncio.id]
        assert fila.views is None and fila.favorites is None
        assert fila.views_per_day is None and fila.favorites_rate is None
        assert show(fila.views) == NOT_AVAILABLE
        assert fila.measurements == 0
        assert fila.to_dict()["visualizaciones"] is None


def test_un_dato_que_no_se_puede_leer_se_guarda_como_no_disponible(app):
    anuncio = publicar(app, 1)[0]
    app.stats.record(anuncio.id, anuncio.account_ref, views=None, favorites=5, status="active", source="navegador")
    fila = next(r for r in app.stats.table() if r.listing_id == anuncio.id)
    assert fila.views is None and fila.favorites == 5
    assert show(fila.views) == NOT_AVAILABLE


def test_sin_capacidad_de_estadisticas_no_se_simula_nada(app, monkeypatch):
    from lot_bot.wallapop.capabilities import Capability

    publicar(app, 1)
    monkeypatch.setattr(
        type(app.wallapop), "capabilities", lambda self: set(Capability) - {Capability.ITEM_STATS}
    )
    resultado = app.stats.capture()
    assert resultado["leidos"] == 0 and resultado["errores"]
    assert all(r.measurements == 0 for r in app.stats.table())


def test_historico_de_estadisticas(app):
    anuncio = publicar(app, 1)[0]
    for _ in range(3):
        app.stats.capture()
    historial = app.stats.history(anuncio.id)
    assert len(historial) == 3
    vistas = [h["visualizaciones"] for h in historial]
    assert vistas == sorted(vistas)  # en DEMO crecen
    assert all(h["origen"] == "demo" for h in historial)
    fila = next(r for r in app.stats.table() if r.listing_id == anuncio.id)
    assert fila.measurements == 3
    assert fila.views_change == vistas[-1] - vistas[0]


def test_ordenar_por_visualizaciones_y_favoritos(app):
    anuncios = publicar(app, 3)
    datos = [(10, 9), (50, 1), (30, 5)]
    for anuncio, (vistas, favs) in zip(anuncios, datos, strict=True):
        app.stats.record(anuncio.id, anuncio.account_ref, views=vistas, favorites=favs, status="active", source="demo")
    ids = {a.id for a in anuncios}
    por_vistas = [r.views for r in app.stats.table(sort_by="visualizaciones") if r.listing_id in ids]
    por_favs = [r.favorites for r in app.stats.table(sort_by="favoritos") if r.listing_id in ids]
    assert por_vistas == [50, 30, 10]
    assert por_favs == [9, 5, 1]
    with pytest.raises(ValueError):
        app.stats.table(sort_by="inventado")


def test_los_no_disponibles_van_al_final(app):
    anuncios = publicar(app, 2)
    app.stats.record(anuncios[0].id, anuncios[0].account_ref, views=3, favorites=None, status="active", source="demo")
    filas = [r for r in app.stats.table(sort_by="visualizaciones") if r.listing_id in {a.id for a in anuncios}]
    assert filas[0].views == 3 and filas[-1].views is None


def test_estadisticas_del_navegador_solo_con_lo_que_aparece():
    from lot_bot.wallapop.browser.service import parse_count

    patron = r"(\d[\d.]*)\s*(?:visualizaciones|visitas)"
    assert parse_count(patron, "Publicado hace 2 días · 1.234 visualizaciones") == 1234
    assert parse_count(patron, "Página sin datos de visitas") is None
    assert parse_count(patron, "") is None


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------
def _sembrar(app, grupos: dict[str, list[int]]):
    """Crea anuncios publicados hace 5 días con una habitación y sus visitas."""
    from lot_bot.database.models import Listing

    refs = [a.internal_ref for a in app.accounts.list_accounts()]
    total = sum(len(v) for v in grupos.values())
    anuncios = publicar(app, total)
    i = 0
    for habitacion, visitas in grupos.items():
        for vistas in visitas:
            anuncio = anuncios[i]
            with app.db.session_scope() as session:
                fila = session.get(Listing, anuncio.id)
                fila.meta = {"imagen": {"id": 1, "escena": {"habitacion": habitacion}}}
                fila.published_at = datetime.now() - timedelta(days=5)
            app.stats.record(anuncio.id, refs[0], views=vistas, favorites=vistas // 10, status="active", source="demo")
            i += 1
    return anuncios


def test_analisis_sin_datos_suficientes_no_saca_conclusiones(app):
    _sembrar(app, {"dormitorio_blanco": [100, 90], "dormitorio_beige": [10]})
    resultado = app.analyzer.analyze("habitacion")
    assert not resultado["datos_suficientes"]
    assert "No hay datos suficientes" in resultado["conclusion"]
    assert app.optimizer.preferred_rooms() == []


def test_analisis_detecta_la_habitacion_que_mejor_funciona(app):
    _sembrar(
        app,
        {"dormitorio_blanco": [500, 450, 480], "dormitorio_beige": [50, 60, 40]},
    )
    resultado = app.analyzer.analyze("habitacion")
    assert resultado["datos_suficientes"]
    assert resultado["mejor"] == "Dormitorio blanco"
    assert "no pruebas de causa" in resultado["conclusion"]  # sin afirmar causalidad
    assert app.optimizer.preferred_rooms() == ["dormitorio_blanco"]


def test_recomendaciones_separadas_de_los_datos(app):
    _sembrar(app, {"dormitorio_blanco": [500, 450, 480], "dormitorio_beige": [50, 60, 40]})
    resultado = app.optimizer.recommendations()
    habitacion = next(r for r in resultado["recomendaciones"] if r["tipo"] == "habitacion")
    assert "Dormitorio blanco" in habitacion["recomendacion"]
    assert habitacion["basado_en"] and habitacion["anuncios"] == 6
    assert "correlaciones" in resultado["nota"]
    texto = " ".join(m.text for m in app.agent.ask("Recomendaciones").messages)
    assert "RECOMENDACIÓN" in texto and "DATOS" in texto


def test_sin_datos_no_hay_recomendaciones(app):
    resultado = app.optimizer.recommendations()
    assert resultado["recomendaciones"] == []
    assert "no hay datos suficientes" in resultado["sin_datos_suficientes"][0].lower()


def test_bajo_rendimiento(app):
    _sembrar(app, {"dormitorio_blanco": [500, 480, 470], "dormitorio_beige": [510, 20, 490]})
    resultado = app.analyzer.low_performers()
    assert resultado["datos_suficientes"]
    assert [a["visualizaciones"] for a in resultado["anuncios"]] == [20]


def test_el_optimizador_orienta_las_nuevas_imagenes(app):
    _sembrar(app, {"dormitorio_blanco": [500, 450, 480], "dormitorio_beige": [50, 60, 40]})
    job = app.publish_queue.enqueue_master(
        None, [app.accounts.list_accounts()[0].internal_ref], 6, generate_images=True
    )
    app.publish_queue.run_until_idle()
    habitaciones = [
        h["escena"]["habitacion"] for h in app.image_generation.history() if h["anuncio"].startswith(f"Cola {job}")
    ]
    assert habitaciones.count("dormitorio_blanco") >= 4  # 2 de cada 3
    assert len(set(habitaciones)) >= 2  # y se sigue probando otras


def test_las_publicaciones_guardan_la_escena_de_su_imagen(app):
    anuncios = publicar(app, 2, imagenes=True)
    for anuncio in anuncios:
        imagen = anuncio.meta["imagen"]
        assert imagen["escena"]["habitacion"]
        assert imagen["id"]


# ---------------------------------------------------------------------------
# Imágenes avanzadas (DEMO, sin red)
# ---------------------------------------------------------------------------
def _una_imagen(app):
    from lot_bot.images.generation import spec_from_master

    return app.image_generation.generate_unique(
        spec_from_master(app.master_ads.get()), variation=0, subject="base"
    )


def test_generar_variantes_distintas(app):
    from lot_bot.images.generation import spec_from_master

    spec = spec_from_master(app.master_ads.get())
    resultados = [
        app.image_generation.generate_unique(spec, variation=i, subject=f"v{i}") for i in range(4)
    ]
    assert len({r.path for r in resultados}) == 4
    assert len({h["hash"] for h in app.image_generation.history()}) == 4


def test_generar_con_producto_indicado_por_el_usuario(app):
    from lot_bot.images.generation.prompts import spec_from_text

    spec = spec_from_text("cama nido blanca de madera")
    assert spec.product_type == "cama nido blanca de madera"
    assert spec.colors == ["white"] and spec.materials == ["wood"]
    resultado = app.image_generation.generate_unique(
        spec, variation=0, subject="cama", scene={"habitacion": "dormitorio_beige", "luz": "nublado"}
    )
    assert "cama nido blanca de madera" in resultado.prompt
    assert resultado.scene["habitacion"] == "dormitorio_beige"


def test_cambiar_habitacion_mantiene_el_producto(app):
    base = _una_imagen(app)
    nueva = app.image_generation.edit("habitacion", base.path, scene={"habitacion": "dormitorio_acogedor"})
    assert nueva.path != base.path and base.path.is_file()  # la original no se toca
    assert nueva.operation == "habitacion"
    assert "Keep the product from the reference image exactly" in nueva.prompt
    assert "a cosy bedroom" in nueva.prompt
    info = app.image_generation.get_image(nueva.image_id)
    assert info["origen"] == str(base.path)


def test_cambiar_estilo(app):
    base = _una_imagen(app)
    nueva = app.image_generation.edit("estilo", base.path, scene={"estilo": "nordico"})
    assert "Scandinavian" in nueva.prompt and nueva.operation == "estilo"


def test_foto_propia_como_referencia(app, tmp_path):
    from PIL import Image

    foto = tmp_path / "mi_canape.jpg"
    Image.new("RGB", (800, 600), (120, 120, 130)).save(foto)
    propia = app.image_generation.add_own_image(foto)
    assert propia.operation == "propia"
    nueva = app.image_generation.edit(
        "referencia", propia.path, scene={"habitacion": "dormitorio_blanco", "estilo": "moderno"}
    )
    assert nueva.operation == "referencia" and nueva.path != propia.path
    # Subir otra vez la misma foto se detecta como repetida.
    from lot_bot.images.generation import GenerationError

    with pytest.raises(GenerationError):
        app.image_generation.add_own_image(foto)


def test_mejorar_imagen_sube_la_resolucion(app, tmp_path):
    from PIL import Image

    foto = tmp_path / "pequena.jpg"
    Image.new("RGB", (400, 300), (90, 100, 110)).save(foto)
    propia = app.image_generation.add_own_image(foto)
    mejorada = app.image_generation.enhance(propia.path)
    with Image.open(mejorada.path) as imagen:
        assert max(imagen.size) == 2048
    assert mejorada.operation == "mejorar"
    assert mejorada.provider == "LOT Bot (local)"  # sin IA ni créditos


def test_la_edicion_no_sirve_si_sale_igual_que_la_original(app, tmp_path):
    from lot_bot.images.generation import GenerationError
    from lot_bot.images.generation.base import ImageGenerator

    class Copia(ImageGenerator):
        name = "copia"
        supports_reference = True

        def generate(self, prompt, seed, destination, input_images=None, **_):
            path = destination.with_suffix(".png")
            path.write_bytes(Path(input_images[0]).read_bytes())
            return path

    base = _una_imagen(app)
    app.image_generation.set_generator(Copia())
    with pytest.raises(GenerationError) as info:
        app.image_generation.edit("estilo", base.path, scene={"estilo": "moderno"})
    assert info.value.code == "DUPLICATE"


def test_prompts_de_edicion_sin_texto_precios_ni_marcas():
    from lot_bot.images.generation.prompts import build_edit_prompt

    for operacion in ("estilo", "habitacion", "referencia"):
        prompt = build_edit_prompt(operacion, {"habitacion": "dormitorio_blanco", "estilo": "moderno"})
        for obligatorio in ("No text", "no price tags", "no logos", "no brand names", "No people"):
            assert obligatorio in prompt
        assert "Do not add, remove or change any feature" in prompt


def test_flux_envia_la_imagen_de_referencia(tmp_path):
    from lot_bot.images.generation import FluxImageService

    enviados = []

    def api(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/v1/flux-2-pro"):
            enviados.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "x", "polling_url": "https://api.bfl.ai/v1/get_result?id=x"})
        if "get_result" in url:
            return httpx.Response(200, json={"status": "Ready", "result": {"sample": "https://d.example/s.jpg"}})
        return httpx.Response(200, content=b"\xff\xd8IMG", headers={"content-type": "image/jpeg"})

    referencia = tmp_path / "ref.jpg"
    referencia.write_bytes(b"\xff\xd8REF")
    service = FluxImageService(
        lambda: "clave-de-prueba-123456",
        http_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(api)),
        sleep=lambda s: None,
    )
    service.generate("prompt", 3, tmp_path / "out", input_images=[referencia])
    import base64

    assert enviados[0]["input_image"] == base64.b64encode(b"\xff\xd8REF").decode()
    assert "width" not in enviados[0]  # con referencia, FLUX respeta sus proporciones


# ---------------------------------------------------------------------------
# Herramientas de la IA
# ---------------------------------------------------------------------------
def test_generar_imagen_desde_el_chat_pide_confirmacion(app):
    respuesta = app.agent.ask("Genera una imagen")
    assert respuesta.needs_confirmation
    assert any("DEMO" in linea for linea in respuesta.pending.request.lines)
    antes = len(app.image_generation.history())
    app.agent.confirm(respuesta.pending.token)
    assert len(app.image_generation.history()) == antes + 1


def test_mejorar_y_cambiar_habitacion_desde_el_chat(app):
    base = _una_imagen(app)
    texto = " ".join(m.text for m in app.agent.ask(f"Mejora la imagen {base.image_id}").messages)
    assert "mejorada" in texto
    respuesta = app.agent.ask(f"Cambia la habitación de la imagen {base.image_id} a dormitorio beige")
    assert respuesta.needs_confirmation
    app.agent.confirm(respuesta.pending.token)
    assert any(h["operacion"] == "habitacion" for h in app.image_generation.history())


def test_estadisticas_desde_el_chat(app):
    publicar(app, 2)
    texto = " ".join(m.text for m in app.agent.ask("Estadísticas").messages)
    assert NOT_AVAILABLE in texto  # sin mediciones todavía
    app.agent.ask("Actualiza las estadísticas")
    texto = " ".join(m.text for m in app.agent.ask("¿Qué anuncios tienen más favoritos?").messages)
    assert "simuladas" in texto  # en DEMO se dice que son simuladas


def test_la_ia_no_ve_credenciales_ni_cookies(app):
    """Ninguna herramienta devuelve cookies, credenciales ni perfiles del navegador."""
    from lot_bot.ai.tools import build_registry

    registro = build_registry()
    for tool in registro.all():
        texto = (tool.name + " " + json.dumps(tool.parameters)).lower()
        for prohibido in ("cookie", "credential", "password", "contraseña", "token", "browser_profile"):
            assert prohibido not in texto, (tool.name, prohibido)
    anuncio = publicar(app, 1)[0]
    app.stats.capture()
    for nombre, argumentos in (
        ("get_statistics", {}),
        ("analyze_statistics", {}),
        ("get_listing_history", {"anuncio": anuncio.id}),
        ("get_account_status", {}),
    ):
        from lot_bot.ai.tools.base import ToolContext

        salida = json.dumps(registro.execute(nombre, argumentos, ToolContext(app=app)).to_model_payload(), default=str)
        assert "Cookies" not in salida and "browser_profiles" not in salida
