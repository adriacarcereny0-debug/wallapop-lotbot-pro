"""«Publica 1 canapé» de principio a fin en modo navegador (Wallapop simulado).

Comprueba que, tras la confirmación, es LOT Bot quien rellena y publica el
anuncio completo, y cómo se comporta con CAPTCHA, sesión caducada, ventana
de la cuenta abierta y resultado no confirmado.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from lot_bot.master_ad.defaults import CLIENT_ATTRIBUTES, CLIENT_PRICE, CLIENT_TITLE
from tests.test_browser_integration import FORM, SITE
from tests.test_guided_flow import real  # noqa: F401  (fixture)


@pytest.fixture()
def database(temp_paths, tmp_path):
    """Base de datos en fichero: la cola corre en otro hilo en estas pruebas."""
    from lot_bot.database.engine import Database, set_database

    db = Database(f"sqlite:///{tmp_path / 'lotbot.db'}")
    db.create_all()
    set_database(db)
    yield db


def textos(respuesta) -> str:
    return " ".join(m.text for m in respuesta.messages)


@pytest.fixture()
def listo(real, tmp_path):  # noqa: F811
    from PIL import Image

    from lot_bot.publishing.queue import FakeClock

    app, world, cuenta = real
    foto = tmp_path / "canape.jpg"
    Image.new("RGB", (1200, 900), (150, 150, 155)).save(foto)
    app.master_ads.add_images(
        None, [{"path": str(foto), "file_format": "JPEG", "width": 1200, "height": 900}]
    )
    app.publish_queue.save_settings(generate_images=False)
    app.publish_queue.clock = FakeClock()
    app.backend.service.is_mock = True  # solo para poder usar el reloj de pruebas
    yield app, world, cuenta
    app.backend.service.is_mock = False


def publicar_uno(app):
    r = app.agent.ask("Publica 1 canapé en la cuenta Mi tienda")
    assert r.needs_confirmation, textos(r)
    app.agent.confirm(r.pending.token)


def en_segundo_plano(fn):
    hilo = threading.Thread(target=fn, daemon=True)
    hilo.start()
    return hilo


def esperar(condicion, segundos=10.0):
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        if condicion():
            return True
        time.sleep(0.02)
    return False


def test_publica_1_canape_lo_hace_todo_lot_bot(listo):
    app, world, cuenta = listo
    publicar_uno(app)
    app.publish_queue.run_until_idle()
    progreso = app.publish_queue.progress()
    assert progreso.published == 1, progreso.tasks
    tarea = progreso.tasks[0]
    assert tarea["url"] == world["item_url"]
    assert tarea["id_wallapop"] == "canape-canape-1001"

    pagina = world["pages"][0]
    escritos = {e[1]: e[2] for e in pagina.log if e[0] == "fill"}
    assert escritos[FORM.title.targets[0]] == CLIENT_TITLE
    assert escritos[FORM.price.targets[0]] == "11,44"
    descripcion = escritos[FORM.description.targets[0]]
    assert descripcion.startswith("GRAN OFERTA LIMITADA!") and "603710542" in descripcion
    assert "Canapé + colchón 90x190 → 230€" in descripcion
    opciones = [e[1] for e in pagina.log if e[0] == "option"]
    assert opciones == ["Estructura de camas", *CLIENT_ATTRIBUTES.values(), "Barcelona"]
    assert ("fill", FORM.location_input[0], "Barcelona") in pagina.log  # ubicación escrita
    assert any(e[0] == "files" for e in pagina.log)
    # El anuncio queda guardado en la base de datos con su URL.
    fila = next(r for r in app.stats.table() if r.account_ref == cuenta.internal_ref)
    assert fila.url == world["item_url"]
    assert CLIENT_PRICE == 11.44


def test_resultado_no_confirmado_no_cuenta_como_publicado(listo):
    app, world, _ = listo
    world["no_confirm"] = True
    app.wallapop.site.success_timeout_ms = 0
    try:
        publicar_uno(app)
        app.publish_queue.run_until_idle()
    finally:
        app.wallapop.site.success_timeout_ms = SITE.success_timeout_ms
    progreso = app.publish_queue.progress()
    assert progreso.published == 0
    tarea = progreso.tasks[0]
    assert tarea["estado"] == "unconfirmed"
    assert tarea["codigo_error"] == "RESULTADO_NO_CONFIRMADO"
    assert world["submits"] == 1  # no se reintenta a ciegas: podría duplicarse
    assert progreso.status == "completed"  # no pide «Continuar»: queda marcado y sigue


def test_cadena_de_anuncios_sigue_sola_si_uno_falla(listo):
    """Tu cliente: en una cadena tenía que pulsar «Continuar» a cada rato. Un
    anuncio que falla queda marcado y la cola sigue sola con los demás."""
    app, world, _ = listo
    world["missing"] = {"precio"}  # el 1.º falla en un paso del formulario
    app.wallapop.site.timeout_ms, antes = 300, app.wallapop.site.timeout_ms
    r = app.agent.ask("Empieza a subir 3 anuncios en la cuenta Mi tienda")
    app.agent.confirm(r.pending.token)
    try:
        app.publish_queue.run_once()
        world["missing"] = set()  # los siguientes van bien
        app.publish_queue.run_until_idle()
    finally:
        app.wallapop.site.timeout_ms = antes
    progreso = app.publish_queue.progress()
    assert progreso.status == "completed" and progreso.pause_reason in (None, "")
    assert progreso.failed == 1 and progreso.published == 2


def test_captcha_pausa_mantiene_el_navegador_y_continuar_sigue(listo):
    app, world, _ = listo
    world["verification"] = True
    publicar_uno(app)
    cola = app.publish_queue
    hilo = en_segundo_plano(cola.run_until_idle)
    assert esperar(lambda: cola.progress().status == "paused")
    progreso = cola.progress()
    assert "Wallapop requiere una verificación" in progreso.pause_reason
    assert "pulsa Continuar" in progreso.pause_reason
    assert app.wallapop.pool.is_open(progreso.tasks[0]["cuenta_ref"])  # sigue abierto
    world["verification"] = False  # el usuario la completa a mano
    cola.resume(progreso.job_id)  # botón «Continuar»
    hilo.join(10)
    assert cola.progress().published == 1


def test_ventana_de_la_cuenta_abierta_pide_cerrarla_y_continua(listo):
    from lot_bot.wallapop.errors import ProfileInUseError

    app, world, _ = listo
    launcher = app.wallapop.launcher
    original = launcher.open
    intentos = []

    def open_bloqueado(profile_dir, **kw):
        intentos.append(profile_dir)
        if len(intentos) == 1:
            raise ProfileInUseError("perfil en uso")
        return original(profile_dir, **kw)

    launcher.open = open_bloqueado
    publicar_uno(app)
    cola = app.publish_queue
    hilo = en_segundo_plano(cola.run_until_idle)
    assert esperar(lambda: cola.progress().status == "paused")
    assert "ya está abierta" in cola.progress().pause_reason
    cola.resume(cola.progress().job_id)
    hilo.join(10)
    assert cola.progress().published == 1
    assert intentos[0] == intentos[1]  # mismo perfil, el de la cuenta


def test_si_wallapop_pide_entrar_de_nuevo_la_cuenta_no_caduca(listo):
    from lot_bot.database.models import AccountStatus

    app, world, cuenta = listo
    world["logged_in"] = False
    publicar_uno(app)
    cola = app.publish_queue
    hilo = en_segundo_plano(cola.run_until_idle)
    assert esperar(lambda: cola.progress().status == "paused")
    progreso = cola.progress()
    assert "volver a iniciar sesión" in progreso.pause_reason
    assert "Sesión caducada" not in progreso.pause_reason
    assert app.accounts.get_account(cuenta.internal_ref).status is AccountStatus.CONNECTED
    assert app.wallapop.pool.is_open(cuenta.internal_ref)  # misma ventana abierta
    world["logged_in"] = True  # el usuario entra en esa misma ventana
    cola.resume(progreso.job_id)  # «Continuar»
    hilo.join(10)
    assert cola.progress().published == 1
    assert app.wallapop.pool.open_count == {cuenta.internal_ref: 1}  # mismo navegador
    assert app.accounts.get_account(cuenta.internal_ref).status is AccountStatus.CONNECTED


def test_comprobaciones_fallidas_nunca_desconectan_la_cuenta(listo):
    from lot_bot.database.models import AccountStatus

    app, _, cuenta = listo
    for _ in range(5):
        app.accounts.mark_session_checked(cuenta.internal_ref, False, "no se pudo comprobar")
        app.accounts.mark_session_invalid(cuenta.internal_ref, "pestaña cerrada")
    assert app.accounts.get_account(cuenta.internal_ref).status is AccountStatus.CONNECTED
    app.accounts.remove_account(cuenta.internal_ref)  # solo así deja de funcionar
    assert app.accounts.get_account(cuenta.internal_ref) is None


def test_cerrar_la_ventana_no_rompe_nada_se_reabre_el_mismo_perfil(listo):
    app, world, cuenta = listo
    publicar_uno(app)
    app.publish_queue.run_until_idle()
    assert app.publish_queue.progress().published == 1
    world["pages"][0].closed = True  # el usuario cierra la ventana
    publicar_uno(app)
    app.publish_queue.run_until_idle()
    assert app.publish_queue.progress().published == 1  # la nueva cola: 1 de 1
    launcher = app.wallapop.launcher
    assert len(launcher.opened) == 2 and launcher.opened[0] == launcher.opened[1]


def test_varios_anuncios_reutilizan_el_mismo_perfil_y_esperan_60_s(listo):
    app, world, cuenta = listo
    r = app.agent.ask("Empieza a subir 3 anuncios en la cuenta Mi tienda")
    app.agent.confirm(r.pending.token)
    cola = app.publish_queue
    inicio = cola.clock.now()
    cola.run_until_idle()
    progreso = cola.progress()
    assert progreso.published == 3
    assert app.wallapop.pool.open_count == {cuenta.internal_ref: 1}
    assert len(world["pages"]) == 1  # un único navegador / página para toda la cola
    assert cola.clock.now() - inicio >= 120  # 3 anuncios → al menos 2 esperas de 60 s


def test_lo_que_cambias_en_el_anuncio_principal_es_lo_que_se_sube(listo):
    """Cambias el precio en LOT Bot (chat o pantalla) → Wallapop recibe ese precio."""
    app, world, _ = listo
    r = app.agent.ask("Cambia el precio del anuncio principal a 15 euros")
    assert r.needs_confirmation
    app.agent.confirm(r.pending.token)
    assert app.master_ads.get(None).price == 15
    publicar_uno(app)
    app.publish_queue.run_until_idle()
    assert app.publish_queue.progress().published == 1
    pagina = world["pages"][0]
    escritos = {e[1]: e[2] for e in pagina.log if e[0] == "fill"}
    assert escritos[FORM.price.targets[0]] == "15"


def test_una_version_nueva_de_la_plantilla_no_pisa_tus_cambios(app):
    from lot_bot.master_ad import service as master_service

    master = app.master_ads.get(None)
    app.master_ads.update(master.key, {"price": 15}, confirmed=True)
    nueva = dict(master_service.CLIENT_MASTER_AD, delivery_note="Montaje incluido")
    original = master_service.CLIENT_MASTER_AD
    master_service.CLIENT_MASTER_AD = nueva
    try:
        vista = app.master_ads.ensure_default()
    finally:
        master_service.CLIENT_MASTER_AD = original
    assert vista.price == 15  # tu cambio se respeta
    # Nada del programa pisa el Anuncio principal existente.
    assert vista.delivery_note != "Montaje incluido"


def test_si_cierras_la_ventana_no_se_abre_otra_ni_se_reintenta(listo):
    from tests.test_browser_integration import FakePage

    app, world, _ = listo
    original = FakePage.fill

    def cierra(self, target, text):
        self.closed = True  # el usuario cierra la pestaña a mitad
        raise RuntimeError("Target page, context or browser has been closed")

    FakePage.fill = cierra
    try:
        publicar_uno(app)
        app.publish_queue.run_until_idle()
    finally:
        FakePage.fill = original
    progreso = app.publish_queue.progress()
    assert progreso.published == 0
    assert progreso.tasks[0]["estado"] == "failed"
    assert progreso.tasks[0]["codigo_error"] == "WindowClosedError"
    assert len(app.wallapop.launcher.opened) == 1  # una sola ventana, nunca otra
    # Ese anuncio no se repite, pero la cola no se queda parada esperando.
    assert progreso.status == "completed"


def _fotos(app, tmp_path, n, prefix="foto"):
    from PIL import Image

    tmp_path.mkdir(parents=True, exist_ok=True)
    infos = []
    for i in range(n):
        foto = tmp_path / f"{prefix}{i}.jpg"
        Image.new("RGB", (900, 700), (40 * i, 90, 120)).save(foto)
        infos.append({"path": str(foto), "file_format": "JPEG", "width": 900, "height": 700,
                      "content_hash": f"hash-{prefix}-{i}"})
    app.master_ads.add_images(None, infos)
    return [i["path"] for i in app.master_ads.get(None).images]


def test_solo_se_suben_las_fotos_marcadas(listo, tmp_path):
    app, world, _ = listo
    rutas = _fotos(app, tmp_path, 2)  # + la del fixture = 3 fotos
    master = app.master_ads.get(None)
    app.master_ads.set_image_enabled(master.key, 0, False)  # la del fixture: no usar
    publicar_uno(app)
    app.publish_queue.run_until_idle()
    enviadas = next(e for e in world["pages"][0].log if e[0] == "files")[2]
    assert len(enviadas) == 2
    assert all(Path(r).stem in " ".join(enviadas) for r in rutas[1:])


def test_una_foto_por_anuncio_sin_repetir_nunca_y_se_para_al_acabarse(listo, tmp_path):
    """Tu cliente: las fotos se repetían. Ahora una foto ya publicada no se
    vuelve a usar; cuando se acaban, la cola se para y pide fotos nuevas."""
    app, world, _ = listo
    _fotos(app, tmp_path, 2)  # 3 fotos marcadas en total
    app.publish_queue.save_settings(rotate_photos=True)
    r = app.agent.ask("Empieza a subir 4 anuncios en la cuenta Mi tienda")
    plan = " ".join(r.pending.request.lines)
    assert "Vas a publicar 4 anuncios y solo tienes 3 fotos sin usar" in plan
    app.agent.confirm(r.pending.token)
    app.publish_queue.run_until_idle()
    progreso = app.publish_queue.progress()
    assert progreso.published == 3 and progreso.status == "paused"
    assert "Añade fotos nuevas" in progreso.pause_reason
    envios = [e[2] for e in world["pages"][0].log if e[0] == "files"]
    assert [len(e) for e in envios] == [1, 1, 1]  # una foto por anuncio
    nombres = [Path(e[0]).stem for e in envios]
    assert len(set(nombres)) == 3  # ninguna repetida

    # Añade una foto nueva y reanuda: el 4.º sale con ella.
    _fotos(app, tmp_path / "nuevas", 1, prefix="nueva")
    app.publish_queue.resume(progreso.job_id)
    app.publish_queue.run_until_idle()
    assert app.publish_queue.progress().published == 4
    envios = [e[2] for e in world["pages"][0].log if e[0] == "files"]
    assert len({Path(e[0]).stem for e in envios}) == 4

    # En otra cola (u otra cuenta) tampoco se repiten: no queda ninguna.
    assert app.publish_queue.unused_photos(None) == []
    usadas = app.publish_queue.used_photos()
    assert len(usadas) == 4 and all(v["cuentas"] for v in usadas.values())


def test_con_foto_generada_solo_se_sube_esa(listo, tmp_path):
    """FLUX: solo la foto generada; las marcadas no se añaden (se repetirían)."""
    app, _, _ = listo
    _fotos(app, tmp_path, 3)
    generada = tmp_path / "generada.jpg"
    generada.write_bytes(Path(app.master_ads.get(None).images[-1]["path"]).read_bytes())
    master = app.master_ads.get(None)
    ref = app.accounts.list_accounts()[0].internal_ref
    preview = app.master_ads.build_previews(
        master.key, [ref], None, {}, extra_images=[str(generada)]
    )[0]
    assert [Path(p).name for p in preview.image_paths] == ["generada.jpg"]


def test_volver_a_usar_fotos_lo_decide_el_usuario(listo, tmp_path):
    app, _, _ = listo
    app.publish_queue.save_settings(rotate_photos=True)
    app.publish_queue.mark_photo_used("x", "acc")
    assert app.publish_queue.used_photos()
    app.publish_queue.reset_used_photos()
    assert app.publish_queue.used_photos() == {}


def test_todo_lo_que_se_sube_sale_del_anuncio_principal(listo):
    """Lo que un cliente ponga en «Anuncio principal» es lo que se sube:
    nada viene de valores fijos del programa."""
    app, world, _ = listo
    master = app.master_ads.get(None)
    app.master_ads.update(
        master.key,
        {
            "title": "Mesa de comedor extensible",
            "price": 89.5,
            "condition": "Como nuevo",
            "category": "Colchones",
            "description": "Mesa en perfecto estado. Recogida en Madrid.",
            "attributes": {"estado": "Como nuevo", "color": "Negro", "material": "Metal",
                           "ubicacion": "Madrid"},
        },
        confirmed=True,
    )
    publicar_uno(app)
    app.publish_queue.run_until_idle()
    assert app.publish_queue.progress().published == 1, app.publish_queue.progress().tasks
    pagina = world["pages"][0]
    escritos = {e[1]: e[2] for e in pagina.log if e[0] == "fill"}
    assert escritos[FORM.title.targets[0]] == "Mesa de comedor extensible"
    assert escritos[FORM.price.targets[0]] == "89,50"
    assert escritos[FORM.description.targets[0]] == "Mesa en perfecto estado. Recogida en Madrid."
    opciones = [e[1] for e in pagina.log if e[0] == "option"]
    assert opciones[:4] == ["Colchones", "Como nuevo", "Negro", "Metal"]
    assert ("fill", FORM.location_input[0], "Madrid") in pagina.log  # ubicación de la plantilla


def test_anuncio_principal_desactivado_no_se_publica(listo):
    app, world, _ = listo
    master = app.master_ads.get(None)
    app.master_ads.update(
        master.key, {"attributes": {**master.attributes, "activo": "no"}}, confirmed=True
    )
    r = app.agent.ask("Publica 1 canapé en la cuenta Mi tienda")
    assert not r.needs_confirmation
    assert "DESACTIVADO" in textos(r)
    assert world.get("pages") is None  # ni se abre el navegador
