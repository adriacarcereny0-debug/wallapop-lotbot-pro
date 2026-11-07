"""Flujo completo con un Chromium REAL contra una página LOCAL que imita
Wallapop (tests/fixtures/wallapop_simulado). No se conecta a Wallapop.

Se ejecuta si Playwright y un Chromium están disponibles. Para verlo en
pantalla: LOT_BOT_VISIBLE_TESTS=1 (con escritorio o xvfb-run).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lot_bot.master_ad.defaults import CLIENT_ATTRIBUTES, CLIENT_TITLE
from lot_bot.wallapop.dto import ItemDraft

playwright = pytest.importorskip("playwright.sync_api")

CHROMIUM = os.environ.get("LOT_BOT_BROWSER_EXECUTABLE") or next(
    (str(p) for p in sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux*/chrome"))), ""
)
pytestmark = pytest.mark.skipif(not CHROMIUM, reason="No hay Chromium para la prueba real.")

DESCRIPCION = (
    "GRAN OFERTA LIMITADA! Renueva tu descanso hoy y paga menos ✨ Canapé + colchón 90x190 → "
    "230€ ✨ Canapé + colchón 135x190 → 270€ ✨ Canapé + colchón 150x190 → 290€ 🚚 Transporte y "
    "montaje GRATUITO 📲 Pide el tuyo ahora por WhatsApp: 603710542 🕒 Solo por tiempo limitado. "
    "¡No te quedes sin el tuyo"
)


@pytest.fixture()
def servicio(tmp_path, monkeypatch):
    from lot_bot.wallapop.browser import (
        BrowserProfileStore,
        BrowserWallapopService,
        PlaywrightLauncher,
        load_site_config,
    )
    from tests.wallapop_simulado import SimulatedWallapop

    monkeypatch.setenv("LOT_BOT_BROWSER_EXECUTABLE", CHROMIUM)
    monkeypatch.setenv("LOT_BOT_BROWSER_SANDBOX", "0")  # solo contenedor Linux de pruebas
    visible = os.environ.get("LOT_BOT_VISIBLE_TESTS") == "1"
    base = load_site_config(Path(__file__).resolve().parents[1] / "lot_bot/resources/wallapop_browser.yaml")
    with SimulatedWallapop() as web:
        site = web.site(base)
        site.visible = visible
        site.success_timeout_ms = 15000
        profiles = BrowserProfileStore(tmp_path / "perfiles")
        service = BrowserWallapopService(
            profiles,
            launcher=PlaywrightLauncher(10000),
            site=site,
            screenshots_dir=tmp_path / "logs" / "navegador",
            is_account_connected=lambda ref: True,
        )
        yield service, web, tmp_path
        service.shutdown()


def borrador(tmp_path) -> ItemDraft:
    from PIL import Image

    foto = tmp_path / "canape.jpg"
    Image.new("RGB", (800, 600), (140, 140, 150)).save(foto)
    return ItemDraft(
        title=CLIENT_TITLE,
        description=DESCRIPCION,
        price=11.44,
        category="Hogar y jardín > Muebles y organización > Camas y accesorios > Estructura de camas",
        condition="Nuevo",
        attributes={**CLIENT_ATTRIBUTES, "ubicacion": "Madrid"},
        image_paths=[str(foto)],
    )


def test_navegador_real_publica_el_canape_completo(servicio):
    service, web, tmp_path = servicio
    resultado = service.create_item("cuenta-1", borrador(tmp_path))
    assert resultado.success, resultado.message
    assert resultado.data["confirmado"]
    assert resultado.item_id == "canape-canape-701"
    assert resultado.data["url"] == f"{web.base}/item/canape-canape-701"
    publicado = web.published[0]
    # Wallapop había puesto su título/descripción de IA y el envío activado:
    # el bot los sustituye por los de la plantilla y apaga el envío.
    assert publicado["titulo"] == CLIENT_TITLE
    assert publicado["descripcion"] == DESCRIPCION
    assert publicado["precio"] in ("11,44", "11.44")  # campo numérico: con punto
    assert publicado["categoria"] == "Estructura de camas"
    assert publicado["fotos"] == 1
    # Ubicación del Anuncio principal (Wallapop tenía Barcelona) y la
    # descripción que la IA de Wallapop reescribió tarde vuelve a ser la tuya.
    assert publicado["ubicacion"] == "Madrid"
    assert publicado["envio"] is False  # «Activar envío» desactivado
    # Pulsa «Continuar» tras el título y NUNCA el menú «Categorías» de la cabecera.
    pasos = resultado.data["pasos"]
    assert "Continuar tras el título" in pasos
    # Orden real de Wallapop: título → Continuar → Fotos → Continuar → detalles.
    assert pasos.index("Fotos") < pasos.index("Continuar tras las fotos") < pasos.index("Categoría")
    for c in ("Estado", "Color", "Material"):
        # Hecho por el bot, o ya puesto por Wallapop con el valor correcto.
        assert any(p.startswith(f"Característica {c}") for p in pasos), pasos
    assert "Título (revisión)" in pasos and "Detalles listos" in pasos
    omitidos = resultado.data["omitidos"]
    assert any("Uso" in o for o in omitidos)  # Wallapop no tiene ese campo
    assert any("elegido «Gris»" in o for o in omitidos)  # no hay «Gris y Blanco»
    assert "/buscar-categorias" not in web.visited
    # Mismo perfil persistente en la segunda publicación (no se vuelve a abrir).
    assert service.create_item("cuenta-1", borrador(tmp_path)).success
    assert service.pool.open_count == {"cuenta-1": 1}
    assert len(web.published) == 2


def test_navegador_real_no_publica_si_el_precio_no_coincide(servicio):
    from lot_bot.wallapop.errors import FormMismatchError

    service, web, tmp_path = servicio
    # La web «reformatea» el precio: el formulario queda con otro valor.
    service.site.price_format = "punto"
    datos = borrador(tmp_path)
    original = service.listing_data

    def con_precio_cambiado(draft):
        data = original(draft)
        data.price_text = "12.00"
        return data

    service.listing_data = con_precio_cambiado
    with pytest.raises(FormMismatchError) as info:
        service.create_item("cuenta-1", datos)
    assert "Precio" in info.value.fields
    assert web.published == []  # no se pulsó «Publicar»
    assert list((tmp_path / "logs" / "navegador").glob("*comprobar_formulario*.png"))


def test_navegador_real_sin_sesion_no_publica(servicio):
    from lot_bot.wallapop.errors import AuthenticationError

    service, web, tmp_path = servicio
    service.site.check_private = ["#no-existe"]
    service.site.logged_in = ["#no-existe"]
    with pytest.raises(AuthenticationError):
        service.create_item("cuenta-1", borrador(tmp_path))
    assert web.published == []


def test_navegador_real_captcha_espera_al_usuario_y_continua(servicio):
    """Wallapop (simulado) muestra un CAPTCHA: LOT Bot no lo toca, espera a
    «Continuar» y sigue. Aquí el «usuario» es la prueba, que pulsa el botón de
    la verificación simulada como lo haría una persona."""
    service, web, tmp_path = servicio
    paginas = []
    original_open = service.launcher.open

    def open_y_guardar(profile_dir, **kw):
        cm = original_open(profile_dir, **kw)

        class Envoltorio:
            def __enter__(self_inner):
                page = cm.__enter__()
                paginas.append(page)
                return page

            def __exit__(self_inner, *exc):
                return cm.__exit__(*exc)

        return Envoltorio()

    service.launcher.open = open_y_guardar
    service.site.urls["subir"] += "?captcha=1"
    avisos = []

    def gate(ref, mensaje):
        avisos.append(mensaje)
        paginas[0].click("text=He completado la verificación")  # la persona
        return True

    service.user_gate = gate
    resultado = service.create_item("cuenta-1", borrador(tmp_path))
    assert resultado.success, resultado.message
    assert avisos and "Wallapop requiere una verificación" in avisos[0]
    assert len(web.published) == 1
    assert list((tmp_path / "logs" / "navegador").glob("*verificacion*.png"))


def test_navegador_real_sube_la_foto_aunque_el_campo_se_cree_al_pulsar_el_boton(servicio):
    """Si no hay campo de archivos hasta pulsar «Sube tus fotos», LOT Bot pulsa
    el botón y entrega la foto en la ventana «Abrir archivo»."""
    service, web, tmp_path = servicio
    service.site.urls["subir"] += "?modo=boton"
    resultado = service.create_item("cuenta-1", borrador(tmp_path))
    assert resultado.success, resultado.message
    assert web.published[0]["fotos"] == 1
    assert "/buscar-por-foto" not in web.visited


def test_navegador_real_foto_grande_o_png_se_prepara_y_se_sube(servicio):
    from PIL import Image

    service, web, tmp_path = servicio
    datos = borrador(tmp_path)
    grande = tmp_path / "grande.png"
    Image.new("RGBA", (5000, 3500), (120, 120, 130, 255)).save(grande)
    datos.image_paths = [str(grande)]
    resultado = service.create_item("cuenta-1", datos)
    assert resultado.success, resultado.message
    assert web.published[0]["fotos"] == 1
    assert "/buscar-por-foto" not in web.visited


@pytest.mark.parametrize("modo", ["arbol", "lento", "dentro", "dentro-arbol", "oculta", "dentro-oculta"])
def test_navegador_real_categoria_en_arbol_o_lenta_queda_seleccionada(servicio, modo):
    """Sin sugeridas (recorre Muebles y organización → Camas y accesorios →
    Estructura de camas) o con opciones que tardan: queda puesta y sigue."""
    service, web, tmp_path = servicio
    service.site.urls["subir"] += f"?cat={modo}"
    resultado = service.create_item("cuenta-1", borrador(tmp_path))
    assert resultado.success, resultado.message
    assert web.published[0]["categoria"] == "Estructura de camas"
    assert "/buscar-categorias" not in web.visited  # nunca el enlace duplicado de fuera
    pasos = resultado.data["pasos"]
    assert pasos.index("Categoría") < pasos.index("Característica Estado")


def test_navegador_real_si_wallapop_no_acepta_la_categoria_no_sigue(servicio):
    from lot_bot.wallapop.errors import BrowserStepError

    service, web, tmp_path = servicio
    service.site.urls["subir"] += "?cat=ignora"
    service.site.timeout_ms = 3000
    with pytest.raises(BrowserStepError) as info:
        service.create_item("cuenta-1", borrador(tmp_path))
    assert info.value.step == "Categoría"
    assert web.published == []  # no se ha seguido rellenando ni publicado
    contexto = next((tmp_path / "logs" / "navegador").glob("*Categor*.json")).read_text(encoding="utf-8")
    assert "Estructura de camas" in contexto and "html_panel" in contexto


@pytest.mark.parametrize("modo", ["", "?cat=arbol", "?cat=dentro"])
def test_navegador_real_categoria_escrita_en_plural_como_en_tu_plantilla(servicio, modo):
    """Tu log: la plantilla decía «Estructuras de camas» (un solo nivel) y
    Wallapop muestra «Estructura de camas». Ahora se encuentra igual."""
    service, web, tmp_path = servicio
    service.site.urls["subir"] += modo
    datos = borrador(tmp_path)
    datos.category = "Estructuras de camas"
    # Sin sugeridas (?cat=arbol) la ruta completa sale de «rutas» del YAML.
    resultado = service.create_item("cuenta-1", datos)
    assert resultado.success, resultado.message
    assert web.published[0]["categoria"] == "Estructura de camas"


@pytest.mark.parametrize("envio, esperado", [("sí", True), ("no", False), ("", False)])
def test_navegador_real_envio_como_diga_el_anuncio_principal(servicio, envio, esperado):
    """«Envío» del Anuncio principal: activado o no en Wallapop (Wallapop lo
    trae activado; vacío = lo de siempre, sin envío)."""
    service, web, tmp_path = servicio
    datos = borrador(tmp_path)
    datos.attributes = {**datos.attributes, "envio": envio}
    resultado = service.create_item("cuenta-1", datos)
    assert resultado.success, resultado.message
    assert web.published[0]["envio"] is esperado
    assert not any("Envío" in o for o in resultado.data["omitidos"])


def test_navegador_real_si_no_hay_envio_ni_ubicacion_publica_igual_y_avisa(servicio):
    """El envío y la ubicación nunca impiden publicar: si Wallapop no los
    muestra, se publica y el resultado dice qué no se pudo hacer."""
    service, web, tmp_path = servicio
    service.site.urls["subir"] += "?extras=no"
    service.site.timeout_ms = 3000
    resultado = service.create_item("cuenta-1", borrador(tmp_path))
    assert resultado.success, resultado.message
    assert len(web.published) == 1
    omitidos = " ".join(resultado.data["omitidos"])
    assert "Envío" in omitidos and "Ubicación" in omitidos


def test_navegador_real_ubicacion_va_en_marca_la_localizacion_no_en_el_buscador(servicio):
    """Tu captura: «Tus productos se verán en: Marca la localización» es una
    caja que abre el buscador de direcciones. La ciudad se escribía en el
    buscador de productos de la cabecera."""
    service, web, tmp_path = servicio
    service.site.urls["subir"] += "?ubic=boton"
    resultado = service.create_item("cuenta-1", borrador(tmp_path))
    assert resultado.success, resultado.message
    publicado = web.published[0]
    assert publicado["ubicacion"] == "Madrid"
    assert publicado["buscador_cabecera"] == ""  # la cabecera no se toca
    assert not any("Ubicación" in o for o in resultado.data["omitidos"])


def test_navegador_real_elimina_el_anuncio_y_comprueba_que_ya_no_esta(servicio):
    """Borrado: abre el anuncio, «Más opciones» → «Eliminar», elige el motivo
    «Ya no lo vendo», confirma y vuelve a abrirlo para COMPROBARLO."""
    service, web, tmp_path = servicio
    publicado = service.create_item("cuenta-1", borrador(tmp_path))
    assert publicado.success
    url = publicado.data["url"]
    resultado = service.delete_item("cuenta-1", publicado.item_id, item_url=url)
    assert resultado.success, resultado.message
    assert publicado.item_id in web.deleted
    # Otra vez: ya no está, y se dice sin tocar nada.
    otra = service.delete_item("cuenta-1", publicado.item_id, item_url=url)
    assert otra.success and "ya no estaba" in otra.message


def test_navegador_real_sin_direccion_no_se_elimina_nada(servicio):
    from lot_bot.wallapop.errors import BrowserStepError

    service, web, _ = servicio
    with pytest.raises(BrowserStepError) as info:
        service.delete_item("cuenta-1", "navegador-123")
    assert "Sincronizar" in info.value.user_message
    assert web.deleted == set()


# ---------------------------------------------------------------------------
# Todo desde LOT Bot como en tu captura: Wallapop NO muestra la dirección del
# anuncio al publicar. Eliminar, cambiar precio y título deben funcionar igual.
# ---------------------------------------------------------------------------
@pytest.fixture()
def lotbot(tmp_path, monkeypatch):
    import sys

    monkeypatch.setenv("LOT_BOT_BROWSER_EXECUTABLE", CHROMIUM)
    monkeypatch.setenv("LOT_BOT_BROWSER_SANDBOX", "0")
    monkeypatch.setenv("LOT_BOT_DATA_DIR", str(tmp_path / "datos"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import prueba_publicar_canape as guion

    from lot_bot.bootstrap import create_application
    from lot_bot.config.settings import reload_settings

    reload_settings()  # base de datos nueva para cada prueba
    app = create_application(start_scheduler=False)
    app.set_integration_mode("navegador")
    web, alias = guion._prepare_simulated(app)
    app.wallapop.site.visible = os.environ.get("LOT_BOT_VISIBLE_TESTS") == "1"
    app.wallapop.site.success_timeout_ms = 15000
    app.wallapop.site.urls["subir"] += "?sinurl=1"
    ref = app.accounts.resolve_ref(alias)
    try:
        yield app, web, ref
    finally:
        app.shutdown()
        web.__exit__(None, None, None)


def _publicar(app, ref, veces):
    for _ in range(veces):
        resultado = app.master_ads.publish_single(None, ref, {}, confirmed=True)
        assert resultado.success, resultado.message


def test_lotbot_elimina_cambia_precio_y_titulo_sin_direccion_al_publicar(lotbot):
    from lot_bot.publishing.listings import ListingFilter

    app, web, ref = lotbot
    _publicar(app, ref, 3)
    anuncios = app.listings.search(ListingFilter(account_ref=ref))
    assert len(anuncios) == 3 and all(not a.url for a in anuncios)  # como en tu captura

    # Cambiar precio de uno: busca su dirección en «Tus productos» y lo edita.
    precio = app.publishing.update_prices([anuncios[0].id], 95.5, confirmed=True)
    assert precio[0].success, precio[0].message
    assert {"95.5", "95.50"} & {e["precio"] for e in web.edits}
    # Ya tienen su dirección guardada (las 3, de una sola lectura).
    assert all(a.url for a in app.listings.search(ListingFilter(account_ref=ref)))

    # Cambiar título.
    titulo = app.publishing.update_listing(
        anuncios[1].id, {"title": "Canapé abatible nuevo"}, confirmed=True
    )
    assert titulo.success, titulo.message
    assert web.edits[-1]["titulo"] == "Canapé abatible nuevo"

    # Eliminar los 3 de golpe («Seleccionar todos» + «Eliminar seleccionados»).
    borrados = app.publishing.delete_listings(
        [a.id for a in anuncios], confirmed=True, sleep=lambda s: None
    )
    assert [b.success for b in borrados] == [True, True, True], [b.message for b in borrados]
    assert len(web.deleted) == 3
    assert {app.listings.get(a.id).status for a in anuncios} == {"removed"}


def test_lotbot_sincronizar_lee_tus_productos(lotbot):
    from lot_bot.publishing.listings import ListingFilter

    app, web, ref = lotbot
    _publicar(app, ref, 2)
    resultado = app.listings.sync_account(ref)
    assert resultado["nuevos"] == 0 and resultado["actualizados"] == 2
    activos = [
        a for a in app.listings.search(ListingFilter(account_ref=ref)) if a.status == "active"
    ]
    assert len(activos) == 2
    assert all(a.url and a.url.startswith(web.base + "/item/") for a in activos)


def test_lotbot_sin_confirmacion_lo_comprueba_en_tus_productos_y_sigue(lotbot):
    """Wallapop publica pero no enseña confirmación: el bot lo busca en «Tus
    productos», lo da por publicado y la cola sigue sin pedir «Continuar»."""
    app, web, ref = lotbot
    app.wallapop.site.urls["subir"] += "&mudo=1"
    app.wallapop.site.success_timeout_ms = 1500
    from lot_bot.publishing.listings import ListingFilter as _Filtro

    for viejo in app.listings.search(_Filtro(account_ref=ref)):  # restos de otras pruebas
        app.listings.mark_removed(viejo.id)
    cola = app.publish_queue
    cola.save_settings(generate_images=False, rotate_photos=False)
    trabajo = cola.enqueue_master(None, [ref], copies=1, generate_images=False)
    cola.run_until_idle()
    progreso = cola.progress(trabajo)
    assert len(web.published) == 1
    assert progreso.status == "completed" and progreso.published == 1, progreso.tasks
    from lot_bot.publishing.listings import ListingFilter

    activos = [a for a in app.listings.search(ListingFilter(account_ref=ref)) if a.status == "active"]
    assert any(a.url and "/item/" in a.url for a in activos)  # ya está en «Anuncios»



@pytest.mark.parametrize("variante", ["?ubic=boton", "?ubic=boton&ubicicono=1"])
def test_navegador_real_ubicacion_barcelona_no_elige_llobregat(servicio, variante):
    """Tu caso: al escribir «Barcelona» salen antes «L'Hospitalet de
    Llobregat, Barcelona» y «Sant Feliu de Llobregat, Barcelona». También
    cuando cada sugerencia empieza con un icono («location_on»)."""
    service, web, tmp_path = servicio
    service.site.urls["subir"] += variante
    datos = borrador(tmp_path)
    datos.attributes = {**datos.attributes, "ubicacion": "Barcelona"}
    resultado = service.create_item("cuenta-1", datos)
    assert resultado.success, resultado.message
    assert web.published[0]["ubicacion"] == "Barcelona"
    assert not any("Ubicación" in o for o in resultado.data["omitidos"])


@pytest.mark.parametrize(
    "variante",
    [
        "?multilento=1",
        "?multilento=1&sinestado=1",
        # Tu vídeo: la lista tiene un botón verde «Seleccionar»; sin pulsarlo
        # Wallapop olvida el color y el material.
        "?seleccionar=1&sinestado=1",
        # Wallapop (su IA) ya había marcado el color sin mostrarlo: pulsarlo lo
        # quitaba. Ahora se pulsa antes «Borrar» de la lista.
        "?seleccionar=1&sinestado=1&iaprevio=1",
        # Como el real: lo elegido se ve en el recuadro, fuera del botón «Color*».
        "?seleccionar=1&sinestado=1&iaprevio=1&valorfuera=1",
        "?seleccionar=1&sinestado=1&valorfuera=1",
    ],
)
def test_navegador_real_color_y_material_no_se_desmarcan_si_el_campo_tarda(servicio, variante):
    """Tu caso: el bot marcaba Color y Material, el campo tardaba en mostrarlo,
    lo volvía a pulsar y así lo QUITABA (y faltaban esos dos campos). También
    cuando la lista no dice qué está marcado."""
    service, web, tmp_path = servicio
    service.site.urls["subir"] += variante
    resultado = service.create_item("cuenta-1", borrador(tmp_path))
    assert resultado.success, resultado.message
    publicado = web.published[0]
    assert publicado["color"] == "Gris", resultado.data["omitidos"]
    assert publicado["material"] == "Madera", resultado.data["omitidos"]


def test_navegador_real_revision_final_no_desmarca_lo_ya_elegido(servicio):
    """Tu vídeo: Blanco y Madera quedaban bien puestos, pero la revisión final
    no los veía, los volvía a pulsar y los DESMARCABA. Ahora un campo ya
    elegido no se vuelve a tocar nunca."""
    service, web, tmp_path = servicio
    service.site.urls["subir"] += "?seleccionar=1&sinestado=1&valorlejos=1"
    resultado = service.create_item("cuenta-1", borrador(tmp_path))
    assert resultado.success, resultado.message
    publicado = web.published[0]
    assert publicado["color"] == "Gris", resultado.data["omitidos"]
    assert publicado["material"] == "Madera", resultado.data["omitidos"]
    assert not any(p.startswith("Característica") and "revisión" in p for p in resultado.data["pasos"])


def test_navegador_real_ubicacion_con_codigo_postal_no_se_repite(servicio):
    """Tu vídeo: la cuenta ya tenía «08007, Barcelona» y el bot no lo daba por
    Barcelona: lo escribía una y otra vez."""
    service, web, tmp_path = servicio
    service.site.urls["subir"] += "?ubic=boton&ubicinicial=1"
    datos = borrador(tmp_path)
    datos.attributes = {**datos.attributes, "ubicacion": "Barcelona"}
    resultado = service.create_item("cuenta-1", datos)
    assert resultado.success, resultado.message
    assert web.published[0]["ubicacion"] == "Barcelona"
    assert "Ubicación Barcelona (ya estaba)" in resultado.data["pasos"], resultado.data["pasos"]
    assert not any("Ubicación" in o for o in resultado.data["omitidos"])


@pytest.mark.parametrize(
    "variante, marca, esperado",
    [
        ("", "Pikolin", "Pikolin"),  # desplegable (como en tu Wallapop)
        ("", "pikolin", "Pikolin"),  # da igual mayúsculas
        ("?marca=texto", "Pikolin", "Pikolin"),  # campo para escribir: sugerencia
        ("?marca=texto", "Marca Propia Canapés", "Marca Propia Canapés"),  # sin sugerencia
    ],
)
def test_navegador_real_pone_la_marca_del_anuncio_principal(servicio, variante, marca, esperado):
    """La marca escrita en «Anuncio principal» se pone en el campo «Marca».
    La ubicación («Marca la localización») no se confunde con la marca."""
    service, web, tmp_path = servicio
    service.site.urls["subir"] += variante
    datos = borrador(tmp_path)
    datos.attributes = {**datos.attributes, "marca": marca}
    resultado = service.create_item("cuenta-1", datos)
    assert resultado.success, resultado.message
    publicado = web.published[0]
    assert publicado["marca"] == esperado, resultado.data["omitidos"]
    assert publicado["ubicacion"] == "Madrid"
    assert any(p.startswith("Característica Marca") for p in resultado.data["pasos"])


def test_navegador_real_marca_que_no_esta_en_la_lista_publica_igual_y_avisa(servicio):
    """Si la marca no existe en el desplegable de Wallapop, NO se elige otra:
    se publica sin marca y el resultado lo dice."""
    service, web, tmp_path = servicio
    datos = borrador(tmp_path)
    datos.attributes = {**datos.attributes, "marca": "Marca Que No Existe"}
    resultado = service.create_item("cuenta-1", datos)
    assert resultado.success, resultado.message
    assert web.published[0]["marca"] == ""
    assert any("Marca" in o for o in resultado.data["omitidos"])


def test_navegador_real_sin_marca_no_toca_el_campo(servicio):
    service, web, tmp_path = servicio
    resultado = service.create_item("cuenta-1", borrador(tmp_path))
    assert resultado.success, resultado.message
    assert web.published[0]["marca"] == ""
