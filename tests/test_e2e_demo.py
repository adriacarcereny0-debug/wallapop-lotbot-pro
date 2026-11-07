"""Pruebas de extremo a extremo en MODO DEMO.

Recorren el flujo completo del cliente: catalogo -> validacion -> vista previa
-> confirmacion -> publicacion -> modificacion -> mensajeria, sin tocar
Wallapop en ningun momento.
"""

from __future__ import annotations

import pytest
from PIL import Image

from lot_bot.database.models import ProductStatus
from lot_bot.publishing.listings import ListingFilter
from lot_bot.publishing.service import ConfirmationRequiredError


@pytest.fixture()
def producto_publicable(app_with_data, tmp_path):
    """Producto completo, con fotografias y asignado a dos cuentas."""
    producto = app_with_data.catalog.create_product(
        {
            "name": "Canapé abatible 135x190 Gris",
            "product_type": "Canapé abatible",
            "category": "Hogar y jardín",
            "size": "135x190",
            "color": "Gris",
            "material": "Madera",
            "condition": "Nuevo",
            "price": 270.0,
            "stock": 5,
            "description": "Canapé abatible con gran capacidad de almacenaje.",
        }
    )
    for indice in range(3):
        ruta = tmp_path / f"foto{indice}.jpg"
        Image.new("RGB", (900, 700), (100 + indice * 20, 80, 60)).save(ruta)
        info = app_with_data.images.import_image(ruta, producto.sku, position=indice)
        info.is_primary = indice == 0
        app_with_data.catalog.add_image(producto.id, info.to_dict())

    cuentas = [a.internal_ref for a in app_with_data.accounts.list_accounts()][:2]
    app_with_data.catalog.assign_to_accounts(producto.id, cuentas)
    return app_with_data.catalog.get_product(producto.id)


# ---------------------------------------------------------------------------
def test_arranque_en_modo_demo(app):
    assert app.demo_mode
    assert app.wallapop.is_mock
    assert app.backend_label == "MODO DEMO"
    assert len(app.accounts.list_accounts()) == 4


def test_sincronizacion_trae_anuncios_aislados_por_cuenta(app_with_data):
    por_cuenta = app_with_data.listings.count_by_account()
    assert sum(por_cuenta.values()) == app_with_data.listings.stats()["total"]
    for referencia in por_cuenta:
        anuncios = app_with_data.listings.search(ListingFilter(account_ref=referencia))
        assert all(v.account_ref == referencia for v in anuncios)


def test_flujo_completo_de_publicacion(app_with_data, producto_publicable):
    # 1. Validación
    informe = app_with_data.catalog.validate_product(producto_publicable.id)
    assert informe.can_publish, informe.summary()

    # 2. Vista previa
    vistas = app_with_data.publishing.build_previews(producto_publicable.id)
    assert len(vistas) == 2
    assert all(v.can_publish for v in vistas)
    assert all("{" not in v.title for v in vistas)

    # 3. Sin confirmación NO se publica
    with pytest.raises(ConfirmationRequiredError):
        app_with_data.publishing.publish(producto_publicable.id, confirmed=False)

    # 4. Con confirmación sí
    resultados = app_with_data.publishing.publish(producto_publicable.id, confirmed=True)
    assert all(r.success for r in resultados)

    # 5. Los anuncios quedan registrados en la cuenta correcta
    for resultado in resultados:
        anuncios = app_with_data.listings.search(
            ListingFilter(account_ref=resultado.account_ref, product_sku=producto_publicable.sku)
        )
        assert len(anuncios) == 1

    # 6. El producto pasa a publicado
    assert app_with_data.catalog.get_product(producto_publicable.id).status == (
        ProductStatus.PUBLISHED.value
    )


def test_no_se_publica_un_producto_incompleto(app_with_data):
    producto = app_with_data.catalog.create_product({"name": "Producto sin datos"})
    cuentas = [a.internal_ref for a in app_with_data.accounts.list_accounts()][:1]
    app_with_data.catalog.assign_to_accounts(producto.id, cuentas)

    resultados = app_with_data.publishing.publish(producto.id, confirmed=True)
    assert not any(r.success for r in resultados)
    assert resultados[0].error_code == "CALIDAD_INSUFICIENTE"


def test_producto_sin_cuentas_no_se_publica(app_with_data):
    producto = app_with_data.catalog.create_product({"name": "Huérfano", "price": 10})
    with pytest.raises(ValueError, match="no está asignado"):
        app_with_data.publishing.build_previews(producto.id)


def test_cambio_de_precio_en_lote_por_el_asistente(app_with_data):
    respuesta = app_with_data.agent.ask("Cambia el precio de los canapés de 135x190 a 269 €")
    assert respuesta.needs_confirmation
    afectados = respuesta.pending.request.affected
    app_with_data.agent.confirm(respuesta.pending.token)

    anuncios = app_with_data.listings.search(ListingFilter(size="135x190"))
    assert len(anuncios) == afectados
    assert {v.price for v in anuncios} == {269.0}


def test_eliminar_anuncio_exige_confirmacion(app_with_data):
    anuncio = app_with_data.listings.search(ListingFilter(limit=1))[0]
    with pytest.raises(ConfirmationRequiredError):
        app_with_data.publishing.delete_listing(anuncio.id, confirmed=False)

    resultado = app_with_data.publishing.delete_listing(anuncio.id, confirmed=True)
    assert resultado.success
    assert app_with_data.listings.get(anuncio.id).status == "removed"


def test_eliminar_varios_de_uno_en_uno_con_espera(app_with_data):
    anuncios = app_with_data.listings.search(ListingFilter(limit=3))
    ids = [a.id for a in anuncios]
    with pytest.raises(ConfirmationRequiredError):
        app_with_data.publishing.delete_listings(ids, confirmed=False)
    esperas, avance = [], []
    resultados = app_with_data.publishing.delete_listings(
        ids, confirmed=True, interval_seconds=60, sleep=esperas.append,
        on_progress=lambda hecho, total, _o: avance.append((hecho, total)),
    )
    assert all(r.success for r in resultados)
    assert esperas == [60, 60]  # una espera ENTRE cada uno
    assert avance == [(1, 3), (2, 3), (3, 3)]
    assert {app_with_data.listings.get(i).status for i in ids} == {"removed"}


def test_eliminar_varios_se_para_si_wallapop_pide_verificacion(app_with_data, monkeypatch):
    from lot_bot.wallapop.errors import VerificationRequiredError

    anuncios = app_with_data.listings.search(ListingFilter(limit=3))
    ids = [a.id for a in anuncios]
    llamadas = []

    def falla(account_ref, item_id, item_url=None):
        llamadas.append(item_id)
        raise VerificationRequiredError("captcha")

    monkeypatch.setattr(app_with_data.publishing._wallapop, "delete_item", falla)
    resultados = app_with_data.publishing.delete_listings(ids, confirmed=True, sleep=lambda s: None)
    assert len(resultados) == 1 and not resultados[0].success  # los demás, sin tocar
    assert len(llamadas) == 1
    assert {app_with_data.listings.get(i).status for i in ids[1:]} != {"removed"}


def test_error_de_wallapop_se_muestra_sin_ocultarlo(app_with_data):
    """Un fallo de Wallapop debe llegar al usuario con mensaje entendible."""
    producto = app_with_data.catalog.create_product(
        {
            "name": "__error_validacion__ canapé",
            "category": "Hogar",
            "price": 100.0,
            "description": "d" * 60,
            "size": "135x190",
            "color": "Gris",
            "material": "Madera",
        }
    )
    app_with_data.catalog.update_product(producto.id, {"title_override": "__error_validacion__"})
    app_with_data.catalog.add_image(
        producto.id, {"path": "x.jpg", "file_format": "JPEG", "is_primary": True}
    )
    cuentas = [a.internal_ref for a in app_with_data.accounts.list_accounts()][:1]
    app_with_data.catalog.assign_to_accounts(producto.id, cuentas)

    resultados = app_with_data.publishing.publish(producto.id, confirmed=True)
    assert not resultados[0].success
    assert resultados[0].message  # mensaje entendible
    errores = [e for e in app_with_data.audit.recent(limit=10) if e.result == "error"]
    assert errores


def test_mensajeria_completa(app_with_data):
    conversaciones = app_with_data.messages.list_conversations()
    assert conversaciones
    conversacion = conversaciones[0]

    # Se prepara un borrador, no se envía
    identificador = app_with_data.messages.save_draft(conversacion.id, "Respuesta de prueba")
    assert identificador
    actualizada = app_with_data.messages.get_conversation(conversacion.id)
    assert any(m.is_draft for m in actualizada.messages)

    # Sin confirmación no se envía
    with pytest.raises(PermissionError):
        app_with_data.messages.send(conversacion.id, "Hola", confirmed=False)

    resultado = app_with_data.messages.send(conversacion.id, "Sí, está disponible.", confirmed=True)
    assert resultado["correcto"]
    final = app_with_data.messages.get_conversation(conversacion.id)
    assert any(m.body == "Sí, está disponible." and not m.is_draft for m in final.messages)


def test_automatizaciones_de_solo_lectura_no_modifican_nada(app_with_data):
    from lot_bot.automation.jobs import JOB_DEFINITIONS

    precios_antes = {v.id: v.price for v in app_with_data.listings.search(ListingFilter(limit=1000))}
    for definicion in JOB_DEFINITIONS:
        assert not definicion.writes, f"{definicion.key} no debería escribir"
        resultado = app_with_data.automations.run_now(definicion.key)
        assert resultado.summary
    precios_despues = {v.id: v.price for v in app_with_data.listings.search(ListingFilter(limit=1000))}
    assert precios_despues == precios_antes


def test_analisis_de_mercado_avisa_de_la_limitacion(app_with_data, monkeypatch):
    from lot_bot.market.service import MARKET_SOURCE_REQUIRED_MESSAGE
    from lot_bot.wallapop.capabilities import Capability

    monkeypatch.setattr(
        app_with_data.wallapop, "capabilities", lambda: set(Capability) - {Capability.MARKET_DATA}
    )
    analisis = app_with_data.market.analyze("canapé")
    assert not analisis.has_external_data
    assert MARKET_SOURCE_REQUIRED_MESSAGE in analisis.limitations
    assert analisis.own_stats is not None  # los datos propios sí están


def test_el_historial_registra_cada_accion(app_with_data, producto_publicable):
    app_with_data.publishing.publish(producto_publicable.id, confirmed=True)
    entradas = app_with_data.audit.recent(limit=50)
    acciones = {e.action for e in entradas}
    assert any("Publicación" in a for a in acciones)
    publicaciones = [e for e in entradas if "Publicación" in e.action]
    assert all(e.account_ref for e in publicaciones)


def test_deteccion_de_duplicados_no_borra_nada(app_with_data):
    antes = app_with_data.listings.stats()["total"]
    grupos = app_with_data.listings.find_duplicates()
    assert isinstance(grupos, list)
    assert app_with_data.listings.stats()["total"] == antes


def test_los_mensajes_sin_leer_estan_desactivados(app):
    """No hay forma fiable de leer el buzón de Wallapop con la integración
    actual: la función se desactiva en vez de mostrar datos inventados."""
    assert not app.messages.messaging_available
    assert app.messages.list_conversations() == []  # ni siquiera mensajes DEMO
    texto = " ".join(m.text for m in app.agent.ask("¿Qué mensajes nuevos hay?").messages)
    assert "desactivados" in texto and "no muestra datos que no sean reales" in texto


def test_mensajes_desactivados_tambien_con_el_navegador():
    from lot_bot.wallapop.browser import BrowserWallapopService
    from lot_bot.wallapop.capabilities import Capability

    service = object.__new__(BrowserWallapopService)  # sin abrir ningún navegador
    assert Capability.LIST_CONVERSATIONS not in service.capabilities()


def test_los_mensajes_del_modo_demo_dicen_que_son_simulados(app):
    from lot_bot.wallapop.dto import ItemDraft

    resultado = app.wallapop.create_item(
        "demo-1", ItemDraft(title="Prueba", description="d" * 60, price=10.0)
    )
    assert "DEMO" in resultado.message
    assert "no está en Wallapop" in resultado.message
