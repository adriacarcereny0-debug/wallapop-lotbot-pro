"""Pruebas del agente IA, sus herramientas y el circuito de confirmacion."""

from __future__ import annotations

import pytest

from lot_bot.ai.provider import AIProvider, ProviderReply, ToolCall
from lot_bot.ai.rule_provider import RuleBasedProvider
from lot_bot.ai.tools import build_registry
from lot_bot.ai.tools.base import ToolContext
from lot_bot.ai.tools.registry import ToolRegistry


class GuionProvider(AIProvider):
    """Proveedor de prueba que ejecuta un guion fijo de llamadas."""

    name = "guion"
    natural_language = True

    def __init__(self, guion: list[ProviderReply]) -> None:
        self.guion = list(guion)
        self.llamadas: list[list[dict]] = []

    def complete(self, system_prompt, messages, tools):
        self.llamadas.append(list(messages))
        if self.guion:
            return self.guion.pop(0)
        return ProviderReply(text="Listo.")


# ---------------------------------------------------------------------------
# Registro de herramientas
# ---------------------------------------------------------------------------
def test_todas_las_herramientas_tienen_esquema_valido():
    registro = build_registry()
    assert len(registro.names()) >= 30
    for herramienta in registro.all():
        esquema = herramienta.schema()
        assert esquema["name"] and esquema["description"]
        assert esquema["input_schema"]["type"] == "object"
        for obligatorio in esquema["input_schema"]["required"]:
            assert obligatorio in esquema["input_schema"]["properties"]


def test_las_herramientas_de_escritura_exigen_confirmacion():
    registro = build_registry()
    esperadas = {
        "create_listing",
        "update_price",
        "update_title",
        "update_description",
        "update_images",
        "delete_listing",
        "send_message",
        "create_product",
        "update_product",
        "update_inventory",
    }
    for nombre in esperadas:
        assert registro.get(nombre).requires_confirmation, f"{nombre} debe pedir confirmación"
    assert registro.get("delete_listing").destructive


def test_herramienta_inexistente_no_rompe(app):
    registro = build_registry()
    resultado = registro.execute("volar_a_marte", {}, ToolContext(app=app))
    assert not resultado.ok
    assert "no existe" in resultado.summary


def test_herramienta_sin_permiso_devuelve_codigo_normalizado(app, monkeypatch):
    from lot_bot.wallapop.capabilities import Capability

    monkeypatch.setattr(
        app.wallapop, "capabilities", lambda: set(Capability) - {Capability.DELETE_ITEM}
    )
    registro = build_registry()
    resultado = registro.execute("delete_listing", {"anuncio": 1}, ToolContext(app=app))
    assert resultado.unavailable
    assert "NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS" in resultado.summary


def test_el_registro_bloquea_una_escritura_sin_confirmacion(app):
    """Blindaje: aunque una herramienta se portase mal, no se ejecuta."""
    from lot_bot.ai.tools.base import Tool, ToolCategory, ok

    registro = ToolRegistry()
    registro.register(
        Tool(
            name="mal_educada",
            description="Herramienta que intenta saltarse la confirmación.",
            parameters={"properties": {}, "required": []},
            handler=lambda ctx, args: ok("¡Ya lo he hecho!"),
            category=ToolCategory.LISTINGS,
            requires_confirmation=True,
        )
    )
    resultado = registro.execute("mal_educada", {}, ToolContext(app=app, confirmed=False))
    assert not resultado.ok
    assert "confirmación" in resultado.summary


# ---------------------------------------------------------------------------
# Proveedor de ordenes directas
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "orden, herramienta",
    [
        ("Cambia el precio de todos los canapés de 135x190 a 269 €", "update_price"),
        ("Busca todos los anuncios de canapés", "search_listings"),
        ("Revisa cuáles están duplicados", "detect_duplicates"),
        ("Dime qué productos tienen información incorrecta", "validate_products"),
        ("Muéstrame el inventario", "get_inventory"),
        ("Estado de las cuentas", "get_account_status"),
        ("Analiza los precios del mercado", "get_market_data"),
    ],
)
def test_interpretacion_de_ordenes(orden, herramienta):
    respuesta = RuleBasedProvider().complete("", [{"role": "user", "content": orden}], [])
    assert respuesta.tool_calls, f"No se ha interpretado: {orden}"
    assert respuesta.tool_calls[0].name == herramienta


def test_orden_no_reconocida_ofrece_ayuda():
    respuesta = RuleBasedProvider().complete("", [{"role": "user", "content": "hazme un café"}], [])
    assert not respuesta.tool_calls
    assert "No he entendido" in respuesta.text


def test_extraccion_de_precio_y_medida():
    respuesta = RuleBasedProvider().complete(
        "", [{"role": "user", "content": "Cambia el precio de los canapés de 135x190 a 269 €"}], []
    )
    argumentos = respuesta.tool_calls[0].arguments
    assert argumentos["precio"] == 269.0
    assert argumentos["medida"] == "135x190"


# ---------------------------------------------------------------------------
# Circuito de confirmacion
# ---------------------------------------------------------------------------
def test_cambio_de_precio_pide_confirmacion_y_no_ejecuta(app_with_data):
    from lot_bot.publishing.listings import ListingFilter

    precios_antes = {
        v.id: v.price for v in app_with_data.listings.search(ListingFilter(size="135x190"))
    }
    assert precios_antes

    respuesta = app_with_data.agent.ask("Cambia el precio de los canapés de 135x190 a 269 €")
    assert respuesta.needs_confirmation
    assert respuesta.pending.request.affected == len(precios_antes)

    precios_ahora = {
        v.id: v.price for v in app_with_data.listings.search(ListingFilter(size="135x190"))
    }
    assert precios_ahora == precios_antes, "No se debe cambiar nada antes de confirmar"


def test_confirmar_ejecuta_el_cambio(app_with_data):
    from lot_bot.publishing.listings import ListingFilter

    respuesta = app_with_data.agent.ask("Cambia el precio de los canapés de 135x190 a 269 €")
    app_with_data.agent.confirm(respuesta.pending.token)
    precios = {v.price for v in app_with_data.listings.search(ListingFilter(size="135x190"))}
    assert precios == {269.0}


def test_cancelar_no_cambia_nada(app_with_data):
    from lot_bot.publishing.listings import ListingFilter

    antes = {v.id: v.price for v in app_with_data.listings.search(ListingFilter(size="90x190"))}
    respuesta = app_with_data.agent.ask("Cambia el precio de los canapés de 90x190 a 111 €")
    app_with_data.agent.cancel(respuesta.pending.token)
    despues = {v.id: v.price for v in app_with_data.listings.search(ListingFilter(size="90x190"))}
    assert despues == antes


def test_token_de_confirmacion_invalido(app_with_data):
    app_with_data.agent.ask("Cambia el precio de los canapés de 135x190 a 269 €")
    respuesta = app_with_data.agent.confirm("token-falso")
    assert respuesta.messages[0].role == "error"


def test_no_se_acepta_otra_orden_con_algo_pendiente(app_with_data):
    app_with_data.agent.ask("Cambia el precio de los canapés de 135x190 a 269 €")
    respuesta = app_with_data.agent.ask("Busca todos los anuncios de canapés")
    assert respuesta.needs_confirmation
    assert "confirma o cancela" in respuesta.text().lower()


def test_la_confirmacion_queda_en_el_historial(app_with_data):
    respuesta = app_with_data.agent.ask("Cambia el precio de los canapés de 135x190 a 269 €")
    app_with_data.agent.confirm(respuesta.pending.token)
    acciones = [e.action for e in app_with_data.audit.recent(limit=20)]
    assert any("Confirmación de acción" in a for a in acciones)
    assert any("Cambio de precio" in a for a in acciones)


def test_la_cancelacion_queda_en_el_historial(app_with_data):
    respuesta = app_with_data.agent.ask("Cambia el precio de los canapés de 135x190 a 269 €")
    app_with_data.agent.cancel(respuesta.pending.token)
    entradas = app_with_data.audit.recent(limit=10)
    assert any(e.result == "cancelled" for e in entradas)


def test_el_agente_no_inventa_herramientas(app_with_data):
    """Si el modelo pide una funcion inexistente, se le responde, no se ejecuta."""
    guion = [
        ProviderReply(tool_calls=[ToolCall(id="1", name="borrar_todo", arguments={})], finished=False),
        ProviderReply(text="No puedo hacer eso."),
    ]
    app_with_data.agent.set_provider(GuionProvider(guion))
    respuesta = app_with_data.agent.ask("Borra todo")
    herramienta = [m for m in respuesta.messages if m.role == "herramienta"]
    assert herramienta and "no existe" in herramienta[0].text
