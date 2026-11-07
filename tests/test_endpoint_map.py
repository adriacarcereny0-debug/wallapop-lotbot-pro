"""Pruebas del mapa de endpoints declarativo (no se inventa ninguna ruta)."""

from __future__ import annotations

import pytest

from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.endpoint_map import EndpointMap, dig, empty_map
from lot_bot.wallapop.errors import ConfigurationError


def test_mapa_vacio_no_concede_ninguna_operacion():
    mapa = empty_map()
    assert mapa.capabilities() == set()
    assert not mapa.is_usable


def test_el_fichero_de_ejemplo_no_declara_endpoints():
    """El ejemplo del repositorio NO debe contener URLs de Wallapop."""
    from pathlib import Path

    ruta = Path(__file__).resolve().parents[1] / "config" / "access_profile.example.yaml"
    mapa = EndpointMap.load(ruta)
    assert mapa.capabilities() == set(), "El ejemplo no debe conceder operaciones"
    assert mapa.base_url == ""


def test_operacion_vacia_se_ignora():
    mapa = EndpointMap.from_dict(
        {
            "api": {"base_url": "https://api.ejemplo"},
            "operations": {"list_items": {"method": "GET", "path": "/i"}, "delete_item": None},
        }
    )
    assert Capability.LIST_ITEMS in mapa.capabilities()
    assert Capability.DELETE_ITEM not in mapa.capabilities()


def test_metodo_invalido_es_rechazado():
    with pytest.raises(ConfigurationError):
        EndpointMap.from_dict(
            {"operations": {"list_items": {"method": "TELEPORT", "path": "/i"}}}
        )


def test_mapeo_de_respuesta():
    mapa = EndpointMap.from_dict(
        {
            "api": {"base_url": "https://api.ejemplo"},
            "operations": {
                "list_items": {
                    "method": "GET",
                    "path": "/items",
                    "response": {
                        "collection_path": "data.items",
                        "fields": {"item_id": "id", "title": "attrs.title", "price": "attrs.price.amount"},
                    },
                }
            },
        }
    )
    operacion = mapa.get(Capability.LIST_ITEMS)
    payload = {"data": {"items": [{"id": "7", "attrs": {"title": "Canapé", "price": {"amount": 269}}}]}}
    crudos = operacion.response.extract_collection(payload)
    assert len(crudos) == 1
    assert operacion.response.apply(crudos[0]) == {
        "item_id": "7",
        "title": "Canapé",
        "price": 269,
    }


def test_dig_navega_listas_y_diccionarios():
    assert dig({"a": [{"b": {"c": 5}}]}, "a.0.b.c") == 5
    assert dig({"a": 1}, "no.existe", "por defecto") == "por defecto"


def test_ruta_con_parametros():
    mapa = EndpointMap.from_dict(
        {
            "api": {"base_url": "https://api.ejemplo"},
            "operations": {"get_item": {"method": "GET", "path": "/items/{item_id}"}},
        }
    )
    assert mapa.get("get_item").render_path({"item_id": "abc"}) == "/items/abc"
    with pytest.raises(ConfigurationError):
        mapa.get("get_item").render_path({})
