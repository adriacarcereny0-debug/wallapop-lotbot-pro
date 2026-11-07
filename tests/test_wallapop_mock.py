"""Pruebas de MockWallapopService y del contrato de la capa Wallapop."""

from __future__ import annotations

import pytest

from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.dto import ItemDraft, ItemSearchQuery
from lot_bot.wallapop.errors import (
    NotAvailableWithCurrentAccessError,
    NotFoundError,
    RateLimitError,
    ValidationRejectedError,
)


def test_cuentas_demo_estan_aisladas(mock_service):
    """Lo de una cuenta no puede aparecer en otra."""
    items_1 = {i.item_id for i in mock_service.list_items("demo-1")}
    items_2 = {i.item_id for i in mock_service.list_items("demo-2")}
    assert items_1 and items_2
    assert items_1.isdisjoint(items_2)


def test_crear_anuncio_solo_afecta_a_su_cuenta(mock_service):
    antes_2 = len(mock_service.list_items("demo-2"))
    resultado = mock_service.create_item(
        "demo-1", ItemDraft(title="Canapé nuevo 135x190", description="x" * 60, price=269.0)
    )
    assert resultado.success
    assert len(mock_service.list_items("demo-2")) == antes_2
    with pytest.raises(NotFoundError):
        mock_service.get_item("demo-2", resultado.item_id)


def test_crear_anuncio_sin_datos_obligatorios_falla(mock_service):
    with pytest.raises(ValidationRejectedError) as error:
        mock_service.create_item("demo-1", ItemDraft(title="", description="", price=0))
    assert "titulo" in error.value.detail or "título" in error.value.detail


def test_cambio_de_precio(mock_service):
    item = mock_service.list_items("demo-1")[0]
    mock_service.update_item_price("demo-1", item.item_id, 299.0)
    assert mock_service.get_item("demo-1", item.item_id).price == 299.0


def test_precio_invalido_es_rechazado(mock_service):
    item = mock_service.list_items("demo-1")[0]
    with pytest.raises(ValidationRejectedError):
        mock_service.update_item_price("demo-1", item.item_id, 0)


def test_busqueda_por_atributo(mock_service):
    resultados = mock_service.search_items(
        "demo-1", ItemSearchQuery(attributes={"medida": "135x190"})
    )
    assert resultados
    assert all(i.attributes.get("medida") == "135x190" for i in resultados)


def test_errores_simulados(mock_service):
    with pytest.raises(ValidationRejectedError):
        mock_service.create_item(
            "demo-1", ItemDraft(title="__error_validacion__", description="x" * 60, price=10)
        )
    with pytest.raises(RateLimitError):
        mock_service.search_items("demo-1", ItemSearchQuery(text="__error_limite__"))


def test_mensajeria_demo(mock_service):
    conversaciones = mock_service.list_conversations("demo-2")
    assert conversaciones
    mensajes = mock_service.get_conversation_messages(
        "demo-2", conversaciones[0].conversation_id
    )
    assert mensajes and mensajes[0].direction == "in"
    resultado = mock_service.send_message(
        "demo-2", conversaciones[0].conversation_id, "Sí, está disponible."
    )
    assert resultado.success


def test_el_mock_declara_todas_las_capacidades(mock_service):
    assert mock_service.capabilities() == set(Capability)


def test_operacion_no_autorizada_lanza_error_especifico():
    """Sin operacion declarada, NO existe y no se simula."""
    from lot_bot.wallapop.auth import AuthCredential, AuthKind
    from lot_bot.wallapop.authorized_service import AuthorizedWallapopService
    from lot_bot.wallapop.endpoint_map import EndpointMap

    endpoint_map = EndpointMap.from_dict(
        {
            "api": {"base_url": "https://api.ejemplo"},
            "operations": {"list_items": {"method": "GET", "path": "/items"}},
        }
    )
    credential = AuthCredential(kind=AuthKind.SESSION_HANDOFF, headers={"X-Sesion": "valor"})
    service = AuthorizedWallapopService(endpoint_map, lambda ref: credential)

    assert service.supports(Capability.LIST_ITEMS)
    assert not service.supports(Capability.DELETE_ITEM)
    with pytest.raises(NotAvailableWithCurrentAccessError) as error:
        service.delete_item("cuenta", "1")
    assert error.value.code == "NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS"
