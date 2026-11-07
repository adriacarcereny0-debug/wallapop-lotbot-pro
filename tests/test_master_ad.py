"""Pruebas del anuncio principal de canapés (plantilla maestra)."""

from __future__ import annotations

import pytest

from lot_bot.master_ad import (
    CLIENT_DESCRIPTION_RENDERED,
    CLIENT_MASTER_AD,
    MASTER_KEY,
    MASTER_NAME,
)
from lot_bot.publishing.service import ConfirmationRequiredError

TITULO_CLIENTE = "Canapé canapé canapé canapé canapé"
CARACTERISTICAS_CLIENTE = ["Nuevo", "Dormitorio", "Gris y Blanco", "Madera"]
DESCRIPCION_EXACTA = (
    "GRAN OFERTA LIMITADA! Renueva tu descanso hoy y paga menos ✨ Canapé + colchón "
    "90x190 → 230€ ✨ Canapé + colchón 135x190 → 270€ ✨ Canapé + colchón 150x190 → "
    "290€ 🚚 Transporte y montaje GRATUITO 📲 Pide el tuyo ahora por WhatsApp: "
    "603710542 🕒 Solo por tiempo limitado. ¡No te quedes sin el tuyo"
)


def desbloquear(app):
    """Desactiva la plantilla única (lo haría el usuario en el panel)."""
    app.master_ads.update(None, {"locked": False}, confirmed=True)


def cuentas(app) -> list[str]:
    return [a.internal_ref for a in app.accounts.list_accounts()]


# ---------------------------------------------------------------------------
# Existe y conserva los datos del cliente
# ---------------------------------------------------------------------------
def test_la_plantilla_existe_al_arrancar(app):
    master = app.master_ads.get()
    assert master is not None
    assert master.key == MASTER_KEY
    assert master.name == MASTER_NAME
    assert master.is_default


def test_los_datos_coinciden_exactamente_con_los_del_cliente(app):
    master = app.master_ads.get()
    assert master.title == TITULO_CLIENTE
    assert master.features == CARACTERISTICAS_CLIENTE
    assert master.features_line == "Nuevo · Dormitorio · Gris y Blanco · Madera"
    assert master.attributes == {
        "estado": "Nuevo",
        "uso": "Dormitorio",
        "color": "Gris y Blanco",
        "material": "Madera",
        "ubicacion": "Barcelona",  # editable en Anuncio principal
    }
    assert master.price == 11.44
    assert master.locked
    assert master.description == CLIENT_DESCRIPTION_RENDERED == DESCRIPCION_EXACTA
    assert master.contact_whatsapp == "603710542"
    assert [(v["medida"], v["precio"]) for v in master.variants] == [
        ("90x190", 230),
        ("135x190", 270),
        ("150x190", 290),
    ]


def test_la_descripcion_es_identica_letra_por_letra(app):
    texto = app.master_ads.get().description
    assert texto.startswith("GRAN OFERTA LIMITADA! Renueva tu descanso hoy y paga menos ✨")
    assert "✨ Canapé + colchón 135x190 → 270€" in texto
    assert "🚚 Transporte y montaje GRATUITO" in texto
    assert "📲 Pide el tuyo ahora por WhatsApp: 603710542" in texto
    assert texto.endswith("🕒 Solo por tiempo limitado. ¡No te quedes sin el tuyo")
    # Las características estructuradas NO se mueven a la descripción.
    for campo in ("Dormitorio", "Gris y Blanco", "Madera"):
        assert campo not in texto


def test_los_datos_comerciales_son_variables_y_no_texto_fijo():
    patron = CLIENT_MASTER_AD["description"]
    assert "{whatsapp}" in patron
    assert "{precio_135x190}" in patron
    assert "603710542" not in patron
    assert "270€" not in patron


def test_el_texto_del_cliente_no_se_corrige(app):
    """El título repetido se conserva tal cual."""
    refs = cuentas(app)
    vista = app.master_ads.build_previews(None, refs[:1])[0]
    assert vista.title == TITULO_CLIENTE
    # El aviso se muestra, pero no bloquea ni cambia nada.
    assert vista.can_publish
    assert any("repite" in i.message for i in vista.quality.warnings)


def test_la_plantilla_no_se_duplica_al_reiniciar(app):
    app.master_ads.ensure_default()
    app.master_ads.ensure_default()
    assert len(app.master_ads.list_all()) == 1


def test_reiniciar_no_pisa_los_cambios_del_usuario(app):
    app.master_ads.update(None, {"price": 12.0}, confirmed=True)
    app.master_ads.ensure_default()
    assert app.master_ads.get().price == 12.0


# ---------------------------------------------------------------------------
# Publicar no toca la plantilla
# ---------------------------------------------------------------------------
def test_publicar_no_modifica_la_plantilla(app):
    antes = app.master_ads.get()
    resultados = app.master_ads.publish(None, cuentas(app), copies=4, confirmed=True)
    assert all(r.success for r in resultados)
    despues = app.master_ads.get()
    assert despues.title == antes.title
    assert despues.price == antes.price
    assert despues.description == antes.description
    assert despues.images == antes.images


def test_publicar_sin_confirmacion_no_publica(app):
    with pytest.raises(ConfirmationRequiredError):
        app.master_ads.publish(None, cuentas(app), confirmed=False)
    assert app.master_ads.publications() == []


def test_publicar_varias_copias_reparte_por_turnos(app):
    refs = cuentas(app)
    app.master_ads.publish(None, refs, copies=6, confirmed=True)
    por_cuenta = app.master_ads.stats()["por_cuenta"]
    assert sum(por_cuenta.values()) == 6
    assert max(por_cuenta.values()) - min(por_cuenta.values()) <= 1


def test_publicar_con_cambios_solo_afecta_a_esas_publicaciones(app):
    desbloquear(app)
    app.master_ads.publish(None, cuentas(app)[:1], overrides={"price": 12.0}, confirmed=True)
    publicacion = app.master_ads.publications()[0]
    assert publicacion.price == 12.0
    assert publicacion.overrides == {"price": 12.0}
    assert app.master_ads.get().price == 11.44


def test_no_se_pueden_cambiar_campos_arbitrarios_en_una_publicacion(app):
    with pytest.raises(ValueError):
        app.master_ads.publish(
            None, cuentas(app)[:1], overrides={"contact_whatsapp": "000"}, confirmed=True
        )


def test_cada_publicacion_queda_en_su_cuenta(app):
    refs = cuentas(app)
    app.master_ads.publish(None, refs, confirmed=True)
    publicaciones = app.master_ads.publications()
    assert sorted(p.account_ref for p in publicaciones) == sorted(refs)


# ---------------------------------------------------------------------------
# Editar una publicación no afecta a las demás
# ---------------------------------------------------------------------------
def test_editar_una_publicacion_no_altera_las_demas(app):
    app.master_ads.publish(None, cuentas(app), copies=3, confirmed=True)
    publicaciones = app.master_ads.publications()
    elegida = publicaciones[1]
    app.publishing.update_prices([elegida.id], 12.0, confirmed=True)

    despues = {p.id: p for p in app.master_ads.publications()}
    assert despues[elegida.id].price == 12.0
    assert despues[elegida.id].overrides == {"price": 12.0}
    for otra in publicaciones:
        if otra.id != elegida.id:
            assert despues[otra.id].price == 11.44
            assert despues[otra.id].overrides == {}
    assert app.master_ads.get().price == 11.44


# ---------------------------------------------------------------------------
# Actualizar la plantilla de forma explícita
# ---------------------------------------------------------------------------
def test_el_usuario_puede_actualizar_la_plantilla(app):
    app.master_ads.update(None, {"price": 12.5, "title": "Otro título"}, confirmed=True)
    master = app.master_ads.get()
    assert master.price == 12.5
    assert master.title == "Otro título"


def test_actualizar_la_plantilla_exige_confirmacion(app):
    with pytest.raises(ConfirmationRequiredError):
        app.master_ads.update(None, {"price": 1.0}, confirmed=False)
    assert app.master_ads.get().price == 11.44


def test_actualizar_la_plantilla_no_cambia_lo_ya_publicado(app):
    app.master_ads.publish(None, cuentas(app)[:1], confirmed=True)
    app.master_ads.update(None, {"price": 20.0}, confirmed=True)
    assert app.master_ads.publications()[0].price == 11.44


def test_cambiar_una_oferta_actualiza_la_descripcion(app):
    desbloquear(app)
    app.master_ads.set_variant_price(None, "135x190", 275, confirmed=True)
    master = app.master_ads.get()
    assert "✨ Canapé + colchón 135x190 → 275€" in master.description
    assert "✨ Canapé + colchón 90x190 → 230€" in master.description


def test_restaurar_vuelve_a_los_datos_del_cliente(app):
    app.master_ads.update(None, {"price": 99.0, "title": "X", "locked": False}, confirmed=True)
    app.master_ads.restore_original(None, confirmed=True)
    master = app.master_ads.get()
    assert master.price == 11.44
    assert master.title == TITULO_CLIENTE
    assert master.description == CLIENT_DESCRIPTION_RENDERED
    assert master.locked


# ---------------------------------------------------------------------------
# Plantilla única: nada automático cambia título, precio ni descripción
# ---------------------------------------------------------------------------
def test_con_la_plantilla_unica_no_se_admiten_cambios_por_publicacion(app):
    from lot_bot.master_ad.service import TemplateLockedError

    for cambio in ({"price": 12.0}, {"title": "Otro"}, {"description": "x"}):
        with pytest.raises(TemplateLockedError):
            app.master_ads.publish(None, cuentas(app)[:1], overrides=cambio, confirmed=True)
        with pytest.raises(ValueError):
            app.publish_queue.enqueue_master(None, cuentas(app)[:1], 1, cambio, generate_images=False)
    with pytest.raises(TemplateLockedError):
        app.master_ads.set_variant_price(None, "135x190", 275, confirmed=True)
    master = app.master_ads.get()
    assert (master.title, master.price, master.description) == (
        TITULO_CLIENTE,
        11.44,
        DESCRIPCION_EXACTA,
    )


def test_la_cola_publica_exactamente_la_plantilla(app):
    queue = app.publish_queue
    queue.enqueue_master(None, cuentas(app), copies=4, generate_images=True)
    queue.run_until_idle()
    publicaciones = app.master_ads.publications()
    assert len(publicaciones) == 4
    for anuncio in publicaciones:
        assert anuncio.title == TITULO_CLIENTE
        assert anuncio.price == 11.44
        assert anuncio.description == DESCRIPCION_EXACTA
    master = app.master_ads.get()
    assert (master.title, master.price, master.description) == (
        TITULO_CLIENTE,
        11.44,
        DESCRIPCION_EXACTA,
    )


def test_los_campos_estructurados_van_al_formulario(app, monkeypatch):
    enviados = []
    original = app.wallapop.create_item

    def espiar(ref, draft):
        enviados.append(draft)
        return original(ref, draft)

    monkeypatch.setattr(app.wallapop, "create_item", espiar)
    app.master_ads.publish(None, cuentas(app)[:1], confirmed=True)
    atributos = enviados[0].attributes
    assert atributos["color"] == "Gris y Blanco"
    assert atributos["material"] == "Madera"
    assert atributos["uso"] == "Dormitorio"
    assert atributos["estado"] == "Nuevo"
    assert enviados[0].description == DESCRIPCION_EXACTA


def test_el_programa_nunca_pisa_el_anuncio_principal_guardado(app):
    """Lo que hay guardado en «Anuncio principal» manda siempre: una versión
    nueva del programa no lo cambia."""
    from lot_bot.database.models import MasterAd

    with app.db.session_scope() as session:
        row = session.query(MasterAd).first()
        row.title = "Título que ha puesto el cliente"
        row.original = {**row.original, "title": "otra versión antigua"}
    master = app.master_ads.ensure_default()
    assert master.title == "Título que ha puesto el cliente"


# ---------------------------------------------------------------------------
# DEMO frente a real
# ---------------------------------------------------------------------------
def test_en_demo_se_puede_publicar_sin_configurar_nada(app):
    vista = app.master_ads.build_previews(None, cuentas(app)[:1])[0]
    assert vista.can_publish
    assert vista.image_paths  # imágenes de demostración


def test_las_imagenes_demo_no_se_guardan_en_la_plantilla(app):
    app.master_ads.build_previews(None, cuentas(app)[:1])
    assert app.master_ads.get().images == []


def test_en_modo_real_sin_fotos_no_se_publica(app):
    app.master_ads.demo_mode = False
    vista = app.master_ads.build_previews(None, cuentas(app)[:1])[0]
    assert not vista.can_publish
    assert {i.field for i in vista.quality.errors} == {"fotografias"}
    # La categoría viene puesta (Wallapop la exige) y se puede cambiar.
    assert vista.category.endswith("Estructura de camas")


def test_en_modo_real_sin_categoria_no_se_publica(app):
    app.master_ads.demo_mode = False
    app.master_ads.update(None, {"category": ""}, confirmed=True)
    vista = app.master_ads.build_previews(None, cuentas(app)[:1])[0]
    assert "categoria" in {i.field for i in vista.quality.errors}


def test_el_historial_marca_las_operaciones_demo(app):
    app.master_ads.publish(None, cuentas(app)[:1], confirmed=True)
    entrada = app.audit.recent(limit=1)[0]
    assert entrada.action.startswith("[DEMO]")


# ---------------------------------------------------------------------------
# Imágenes de la plantilla
# ---------------------------------------------------------------------------
def test_gestion_de_imagenes_de_la_plantilla(app, tmp_path):
    from PIL import Image

    rutas = []
    for n, color in enumerate([(10, 20, 30), (200, 100, 50), (90, 90, 90)]):
        ruta = tmp_path / f"foto{n}.jpg"
        Image.new("RGB", (800, 600), color).save(ruta)
        rutas.append(ruta)
    importadas, _ = app.images.import_many(rutas, "anuncio-principal")
    app.master_ads.add_images(None, [i.to_dict() for i in importadas])
    assert len(app.master_ads.get().images) == 3
    assert app.master_ads.get().images[0]["is_primary"]

    app.master_ads.move_image(None, 0, 1)
    app.master_ads.set_primary_image(None, 2)
    assert app.master_ads.get().images[2]["is_primary"]

    app.master_ads.remove_image(None, 0)
    assert len(app.master_ads.get().images) == 2

    # Con fotos propias, en DEMO ya no se usan las de demostración
    vista = app.master_ads.build_previews(None, cuentas(app)[:1])[0]
    assert len(vista.image_paths) == 2


def test_no_se_repiten_fotos_en_la_plantilla(app, tmp_path):
    from PIL import Image

    ruta = tmp_path / "foto.jpg"
    Image.new("RGB", (800, 600), (1, 2, 3)).save(ruta)
    importadas, _ = app.images.import_many([ruta], "anuncio-principal")
    app.master_ads.add_images(None, [i.to_dict() for i in importadas])
    app.master_ads.add_images(None, [i.to_dict() for i in importadas])
    assert len(app.master_ads.get().images) == 1


# ---------------------------------------------------------------------------
# Integración con el resto
# ---------------------------------------------------------------------------
def test_la_automatizacion_revisa_el_anuncio_principal(app):
    resultado = app.automations.run_now("master_ad_review")
    assert "Anuncio principal" in resultado.summary
    assert app.master_ads.publications() == []  # no publica nada


def test_los_duplicados_de_la_plantilla_se_detectan(app):
    app.master_ads.publish(None, cuentas(app), copies=3, confirmed=True)
    grupos = app.listings.find_duplicates()
    assert any(
        {m.get("plantilla") for m in g.members} == {MASTER_NAME} for g in grupos
    )


def test_el_asistente_de_ventas_usa_la_oferta_real(app):
    from lot_bot.messages.sales_assistant import SalesAssistant, build_context_from_data

    master = app.master_ads.get()
    contexto = build_context_from_data(
        product=None, listing={"titulo": master.title, "precio": master.price},
        business={}, master=master,
    )
    respuesta = SalesAssistant().suggest("¿Cuánto cuesta el de 135?", contexto)
    assert "270.00" in respuesta.text
    assert "603710542" in respuesta.text
    # Sin stock configurado, no se promete disponibilidad
    disponibilidad = SalesAssistant().suggest("¿Está disponible?", contexto)
    assert "No dispongo de esa información" in disponibilidad.text


def test_la_migracion_anade_las_columnas_de_origen():
    import sqlalchemy as sa

    from lot_bot.database import Database

    db = Database("sqlite:///:memory:")
    with db.engine.begin() as c:
        c.execute(
            sa.text(
                "CREATE TABLE listings (id INTEGER PRIMARY KEY, account_id INTEGER, "
                "title VARCHAR(250), currency VARCHAR(8), status VARCHAR(20), views INTEGER, "
                "favorites INTEGER, created_at DATETIME, updated_at DATETIME)"
            )
        )
    db.create_all()
    columnas = {c["name"] for c in sa.inspect(db.engine).get_columns("listings")}
    assert {"master_ad_id", "overrides"} <= columnas
