"""Flujo completo desde el chat: «Empieza a subir anuncios cada 60 segundos» →
¿en qué cuenta? → ¿cuántos? → confirmación → cola → publicación → estadísticas.

También en modo navegador (con un navegador simulado: nada sale del ordenador).
"""

from __future__ import annotations

import pytest

from tests.test_browser_integration import FakeLauncher


def textos(respuesta) -> str:
    return " ".join(m.text for m in respuesta.messages)


# ---------------------------------------------------------------------------
# DEMO
# ---------------------------------------------------------------------------
def test_empezar_a_subir_pregunta_la_cuenta_y_cuantos(app):
    agente = app.agent
    r = agente.ask("Empieza a subir anuncios cada 60 segundos")
    assert "¿En qué cuenta lo quieres?" in textos(r)
    assert "Cuenta 2 (DEMO)" in textos(r)
    assert not r.needs_confirmation

    r = agente.ask("Cuenta 2")
    assert "¿Cuántos anuncios quieres subir?" in textos(r)

    r = agente.ask("3")
    assert r.needs_confirmation
    plan = r.pending.request
    assert "3 anuncio(s)" in plan.title
    assert any("Cuenta 2 (DEMO): 3" in linea for linea in plan.lines)
    assert any("60 segundos" in linea for linea in plan.lines)

    agente.confirm(plan.token)
    app.publish_queue.run_until_idle()
    progreso = app.publish_queue.progress()
    assert progreso.published == 3
    assert {t["cuenta"] for t in progreso.tasks} == {"Cuenta 2 (DEMO)"}


def test_cuenta_desconocida_vuelve_a_preguntar(app):
    agente = app.agent
    agente.ask("Empieza a subir anuncios")
    r = agente.ask("la cuenta de mi primo")
    assert "No encuentro la cuenta" in textos(r) and "¿En qué cuenta lo quieres?" in textos(r)
    r = agente.ask("todas")
    assert "¿Cuántos anuncios" in textos(r)
    r = agente.ask("cinco")
    assert r.needs_confirmation and "5 anuncio(s)" in r.pending.request.title


def test_cuenta_y_numero_en_la_misma_frase(app):
    r = app.agent.ask("Empieza a subir 4 anuncios en la cuenta 1")
    assert r.needs_confirmation
    assert "4 anuncio(s)" in r.pending.request.title


def test_se_puede_cancelar_la_pregunta(app):
    app.agent.ask("Empieza a subir anuncios cada 60 segundos")
    r = app.agent.ask("déjalo")
    assert "lo dejamos" in textos(r)
    assert app.publish_queue.latest_job_id() is None


def test_numero_invalido(app):
    agente = app.agent
    agente.ask("Empieza a subir anuncios")
    agente.ask("Cuenta 1")
    r = agente.ask("muchísimos")
    assert "Dime un número entre 1 y 100" in textos(r)


def test_sugerencias_del_asistente():
    from lot_bot.ui.views.assistant import EXAMPLES

    assert "Empieza a subir anuncios cada 60 segundos" in EXAMPLES
    assert not any("mensajes" in e.lower() for e in EXAMPLES)
    assert "Muéstrame los anuncios de la cuenta 1" not in EXAMPLES


def test_menu_sin_anuncios_ni_automatizaciones():
    from lot_bot.ui.main_window import NAVIGATION

    nombres = [n for n, _, _ in NAVIGATION]
    assert "Anuncios" not in nombres and "Automatizaciones" not in nombres
    for imprescindible in ("Asistente IA", "Cuentas de Wallapop", "Publicación automática", "Estadísticas"):
        assert imprescindible in nombres


def test_las_automatizaciones_programadas_no_se_arrancan(database, demo_settings, temp_paths):
    from lot_bot.bootstrap import create_application

    app = create_application(settings=demo_settings, database=database, start_scheduler=True)
    try:
        assert not app.automations._scheduler.running if hasattr(app.automations, "_scheduler") else True
        assert app.publish_queue._thread is not None  # la cola sí
    finally:
        app.shutdown()


# ---------------------------------------------------------------------------
# Modo navegador (Wallapop simulado)
# ---------------------------------------------------------------------------
@pytest.fixture()
def real(app, monkeypatch):
    """Aplicación en modo navegador con una cuenta real conectada y un
    navegador simulado que se comporta como Wallapop."""
    from lot_bot.wallapop.auth.base import AuthCredential, AuthKind
    from lot_bot.wallapop.browser.driver import PlaywrightLauncher
    from lot_bot.wallapop.browser.pool import BrowserSessionPool

    monkeypatch.setattr(PlaywrightLauncher, "available", lambda self: (True, ""))
    app.set_integration_mode("navegador")
    world = {
        "logged_in": True,
        "item_url": "https://es.wallapop.com/item/canape-canape-1001",
        "body": "Publicado hace 3 días · 1.234 visualizaciones · 17 favoritos",
    }
    service = app.wallapop
    service.launcher = FakeLauncher(world)
    service.pool = BrowserSessionPool(service.launcher)
    service.site.check_wait_ms = 0
    service.normal_launcher = None
    cuenta = app.accounts.add_account("Mi tienda")
    service.profiles.profile_dir(cuenta.internal_ref).joinpath("Local State").write_text("{}")
    app.accounts.store_credential(
        cuenta.internal_ref,
        AuthCredential(kind=AuthKind.BROWSER_SESSION, metadata={"account_ref": cuenta.internal_ref}),
    )
    yield app, world, cuenta
    service.shutdown()


def test_en_modo_real_sin_fotos_se_explica_que_falta(real):
    app, _, _ = real
    app.publish_queue.save_settings(generate_images=False)
    r = app.agent.ask("Empieza a subir 2 anuncios en la cuenta Mi tienda")
    texto = textos(r)
    assert not r.needs_confirmation
    assert "Todavía no se puede publicar" in texto and "fotografía" in texto
    assert "FLUX" in texto  # dice cómo arreglarlo


def test_en_modo_real_solo_se_ofrecen_cuentas_reales(real):
    app, _, _ = real
    r = app.agent.ask("Empieza a subir anuncios cada 60 segundos")
    assert "«Mi tienda»" in textos(r) and "DEMO" not in textos(r)


def test_publicacion_y_estadisticas_en_modo_navegador(real, tmp_path):
    from PIL import Image

    from lot_bot.publishing.queue import FakeClock

    app, world, cuenta = real
    foto = tmp_path / "canape.jpg"
    Image.new("RGB", (1200, 900), (150, 150, 155)).save(foto)
    app.master_ads.add_images(None, [{"path": str(foto), "file_format": "JPEG", "width": 1200, "height": 900}])
    app.publish_queue.save_settings(generate_images=False)
    app.publish_queue.clock = FakeClock()
    app.backend.service.is_mock = True  # solo para poder usar el reloj de pruebas
    try:
        agente = app.agent
        agente.ask("Empieza a subir anuncios cada 60 segundos")
        agente.ask("Mi tienda")
        r = agente.ask("2")
        assert r.needs_confirmation
        agente.confirm(r.pending.token)
        app.publish_queue.run_until_idle()
    finally:
        app.backend.service.is_mock = False
    progreso = app.publish_queue.progress()
    assert progreso.published == 2
    assert all(t["url"] == world["item_url"] for t in progreso.tasks)
    # Un solo navegador para toda la cola de la cuenta.
    assert app.wallapop.pool.open_count == {cuenta.internal_ref: 1}

    resultado = app.stats.capture()
    assert resultado["leidos"] >= 1
    fila = next(r for r in app.stats.table() if r.account_ref == cuenta.internal_ref)
    assert fila.views == 1234 and fila.favorites == 17
    assert fila.url == world["item_url"]

    world["body"] = "Sin datos visibles"
    app.stats.capture()
    historial = app.stats.history(fila.listing_id)
    assert historial[-1]["visualizaciones"] is None  # no se inventa
    fila = next(r for r in app.stats.table() if r.account_ref == cuenta.internal_ref)
    assert fila.views == 1234  # se mantiene el último dato real


# ---------------------------------------------------------------------------
# «Pensando…» que nunca termina
# ---------------------------------------------------------------------------
def test_el_asistente_tiene_hilos_propios():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from lot_bot.ui.widgets.workers import TaskRunner

    general, asistente = TaskRunner(6), TaskRunner(2)
    assert general._pool is not asistente._pool
    assert QThreadPool.globalInstance() not in (general._pool, asistente._pool)
    # Aunque todas las tareas generales estén ocupadas, el asistente responde.
    import threading
    import time

    liberar = threading.Event()
    for _ in range(6):
        general.run(liberar.wait, 5)
    hecho = threading.Event()
    asistente.run(hecho.set)
    assert hecho.wait(3), "el asistente se ha quedado esperando a otras tareas"
    liberar.set()
    general.wait(5000)
    time.sleep(0.05)


def test_una_operacion_del_navegador_nunca_espera_para_siempre(tmp_path):
    import threading

    from lot_bot.wallapop.browser.driver import BrowserUnavailable
    from lot_bot.wallapop.browser.pool import BrowserSessionPool

    pool = BrowserSessionPool(FakeLauncher({}))
    bloqueo = threading.Event()
    with pytest.raises(BrowserUnavailable):
        pool.run("acc-1", tmp_path / "acc-1", lambda page: bloqueo.wait(10), timeout=0.3,
                 visible=False, channels=["chromium"], locale="es-ES")
    bloqueo.set()


def test_el_asistente_se_libera_si_tarda_demasiado(qtbot=None):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from lot_bot.ui.views.assistant import AssistantView

    assert AssistantView.WATCHDOG_MS <= 120_000


def test_la_interfaz_siempre_recibe_el_aviso_de_terminado():
    """Fallo corregido: el aviso «terminado» se perdía si Python destruía las
    señales de la tarea antes de que la interfaz lo procesara, y el botón se
    quedaba en «Pensando…»."""
    import gc
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    qa = QApplication.instance() or QApplication([])
    from lot_bot.ui.widgets.workers import TaskRunner

    runner = TaskRunner(2)
    terminados: list[int] = []
    for i in range(30):
        runner.run(lambda: None, on_done=lambda i=i: terminados.append(i))
    runner.wait(5000)
    gc.collect()
    fin = time.monotonic() + 5
    while len(terminados) < 30 and time.monotonic() < fin:
        qa.processEvents()
        time.sleep(0.01)
    assert len(terminados) == 30


# ---------------------------------------------------------------------------
# Publicar con fotos propias, SIN clave de FLUX
# ---------------------------------------------------------------------------
def test_sin_clave_de_flux_se_publica_con_mis_fotos(real, tmp_path, monkeypatch):
    import httpx
    from PIL import Image

    from lot_bot.config.api_keys import FLUX
    from lot_bot.publishing.queue import FakeClock

    app, world, cuenta = real

    def prohibido(*a, **k):
        raise AssertionError("No se debe llamar a FLUX sin clave")

    monkeypatch.setattr(httpx.Client, "send", prohibido)
    assert not app.api_keys.has(FLUX)
    app.publish_queue.save_settings(generate_images=True)  # aunque esté elegido FLUX

    foto = tmp_path / "mi_foto.jpg"
    Image.new("RGB", (1200, 900), (140, 140, 150)).save(foto)
    from lot_bot.ui.views.image_studio import use_image_in_ads

    assert use_image_in_ads(app, str(foto))
    assert not use_image_in_ads(app, str(foto))  # no se duplica

    r = app.agent.ask("Empieza a subir 1 anuncio en la cuenta Mi tienda")
    assert r.needs_confirmation
    assert any("las tuyas" in linea and "no hace falta" in linea for linea in r.pending.request.lines)
    app.agent.confirm(r.pending.token)
    app.publish_queue.clock = FakeClock()
    app.backend.service.is_mock = True
    try:
        app.publish_queue.run_until_idle()
    finally:
        app.backend.service.is_mock = False
    tarea = app.publish_queue.progress().tasks[0]
    assert tarea["estado"] == "published" and tarea["imagen"] is None
    subidas = [e for p in app.wallapop.launcher.pages for e in p.log if e[0] == "files"]
    assert subidas and subidas[0][2][0].endswith(".jpg")  # se sube TU foto


def test_sin_fotos_ni_clave_se_explica_como_subirlas(real):
    app, _, _ = real
    r = app.agent.ask("Empieza a subir 1 anuncio en la cuenta Mi tienda")
    texto = textos(r)
    assert "Fotografías…" in texto and "no hace falta ninguna clave" in texto
