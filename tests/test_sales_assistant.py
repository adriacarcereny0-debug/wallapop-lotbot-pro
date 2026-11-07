"""Pruebas del asistente de ventas: no puede inventar datos."""

from __future__ import annotations

import pytest

from lot_bot.messages.sales_assistant import (
    UNKNOWN_ANSWER,
    BuyerIntent,
    SalesAssistant,
    SalesContext,
    build_context_from_data,
    detect_intent,
)


@pytest.fixture()
def contexto_completo():
    return SalesContext(
        product_name="canapé",
        size="135x190",
        color="Gris",
        price=270.0,
        stock=4,
        delivery_policy="El transporte y el montaje son gratuitos.",
        payment_policy="Aceptamos efectivo y Bizum.",
        whatsapp="600000000",
    )


@pytest.mark.parametrize(
    "pregunta, intencion",
    [
        ("¿Está disponible el canapé de 135?", BuyerIntent.AVAILABILITY),
        ("¿Cuánto cuesta?", BuyerIntent.PRICE),
        ("¿Hacéis envío a Sabadell?", BuyerIntent.DELIVERY),
        ("¿El montaje está incluido?", BuyerIntent.ASSEMBLY),
        ("Lo quiero, ¿cómo lo compro?", BuyerIntent.PURCHASE),
        ("¿Aceptáis bizum?", BuyerIntent.PAYMENT),
        ("Me ha llegado roto", BuyerIntent.COMPLAINT),
    ],
)
def test_deteccion_de_intencion(pregunta, intencion):
    assert detect_intent(pregunta) is intencion


def test_responde_con_datos_reales(contexto_completo):
    respuesta = SalesAssistant().suggest("¿Está disponible el canapé de 135?", contexto_completo)
    assert "135x190" in respuesta.text
    assert UNKNOWN_ANSWER not in respuesta.text
    assert respuesta.used_facts


def test_el_precio_que_da_es_el_real(contexto_completo):
    respuesta = SalesAssistant().suggest("¿Cuánto cuesta?", contexto_completo)
    assert "270.00" in respuesta.text
    assert respuesta.used_facts["precio"] == 270.0


@pytest.mark.parametrize(
    "pregunta, campo",
    [
        ("¿Está disponible?", "unidades_disponibles"),
        ("¿Cuánto cuesta?", "precio"),
        ("¿Hacéis envío?", "envio"),
        ("¿Aceptáis bizum?", "formas_de_pago"),
        ("¿Puedo recogerlo en tienda?", "recogida"),
    ],
)
def test_sin_dato_responde_que_no_lo_sabe(pregunta, campo):
    """Nunca se inventa stock, precio, envio, pago ni recogida."""
    respuesta = SalesAssistant().suggest(pregunta, SalesContext(product_name="canapé"))
    assert UNKNOWN_ANSWER in respuesta.text
    assert campo in respuesta.missing_facts
    assert respuesta.needs_human


def test_sin_stock_no_promete_disponibilidad():
    contexto = SalesContext(product_name="canapé", size="135x190", stock=0, price=270.0)
    respuesta = SalesAssistant().suggest("¿Está disponible?", contexto)
    assert "no tenemos disponible" in respuesta.text.lower()


def test_intencion_de_compra_deriva_al_whatsapp(contexto_completo):
    respuesta = SalesAssistant().suggest("Lo quiero, ¿cómo lo compro?", contexto_completo)
    assert "600000000" in respuesta.text
    assert respuesta.intent is BuyerIntent.PURCHASE


def test_sin_whatsapp_no_se_inventa_un_canal():
    contexto = SalesContext(product_name="canapé", price=270.0)
    respuesta = SalesAssistant().suggest("Lo quiero", contexto)
    assert "whatsapp" not in respuesta.text.lower()
    assert "whatsapp" in respuesta.missing_facts


def test_las_incidencias_se_derivan_a_una_persona(contexto_completo):
    respuesta = SalesAssistant().suggest("Me ha llegado roto", contexto_completo)
    assert respuesta.needs_human
    assert "persona" in respuesta.text.lower()


def test_el_contexto_solo_expone_lo_conocido():
    contexto = SalesContext(product_name="canapé", price=270.0)
    hechos = contexto.known_facts()
    assert "precio" in hechos
    assert "envio" not in hechos
    assert "envio" in contexto.unknown_fields()


def test_contexto_construido_desde_datos_reales():
    contexto = build_context_from_data(
        product={"tipo": "Canapé", "medida": "135x190", "precio": 270.0, "stock": 3},
        listing={"titulo": "Canapé abatible 135x190"},
        business={"whatsapp": "600000000", "delivery": "Transporte gratuito."},
    )
    assert contexto.price == 270.0
    assert contexto.stock == 3
    assert contexto.whatsapp == "600000000"
    assert contexto.payment_policy is None  # no configurado -> no se afirma


def test_la_herramienta_de_hechos_avisa_de_lo_que_falta(app_with_data):
    """get_sales_facts debe decir explicitamente que no se sabe."""
    from lot_bot.ai.tools import build_registry
    from lot_bot.ai.tools.base import ToolContext

    conversaciones = app_with_data.messages.list_conversations()
    assert conversaciones
    registro = build_registry()
    resultado = registro.execute(
        "get_sales_facts",
        {"conversacion": conversaciones[0].id},
        ToolContext(app=app_with_data),
    )
    assert resultado.ok
    assert "No dispongo de esa información" in resultado.summary
    assert isinstance(resultado.data["desconocidos"], list)


def test_preparar_respuesta_no_envia_nada(app_with_data, monkeypatch):
    """El asistente de ventas se conserva para una futura integración con
    mensajes reales; aquí se simula que existe."""
    from lot_bot.ai.tools import build_registry
    from lot_bot.ai.tools.base import ToolContext
    from lot_bot.messages.service import MessageService

    monkeypatch.setattr(MessageService, "messaging_available", property(lambda self: True))
    app_with_data.messages.sync_all([a.internal_ref for a in app_with_data.accounts.list_accounts()])

    conversacion = app_with_data.messages.list_conversations()[0]
    registro = build_registry()
    resultado = registro.execute(
        "prepare_message_response",
        {"conversacion": conversacion.id},
        ToolContext(app=app_with_data),
    )
    assert resultado.ok
    assert "NO se ha enviado" in resultado.summary
    actualizada = app_with_data.messages.get_conversation(conversacion.id)
    borradores = [m for m in actualizada.messages if m.is_draft]
    assert len(borradores) == 1
    enviados = [m for m in actualizada.messages if not m.is_draft and m.direction == "out"]
    assert len(enviados) == 0
