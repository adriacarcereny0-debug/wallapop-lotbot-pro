"""Pruebas de catalogo, control de calidad y duplicados."""

from __future__ import annotations

import pytest

from lot_bot.catalog.duplicates import find_duplicates, title_similarity
from lot_bot.catalog.service import CatalogService, ProductFilter, normalize_size
from lot_bot.catalog.validation import validate_listing_data
from lot_bot.wallapop.account_manager import AccountManager


@pytest.fixture()
def catalog(database):
    return CatalogService(database)


def test_normalizacion_de_medidas():
    assert normalize_size("135 x 190") == "135x190"
    assert normalize_size("135X190 cm") == "135x190"
    assert normalize_size(None) is None


def test_alta_de_producto_genera_sku(catalog):
    producto = catalog.create_product(
        {"name": "Canapé abatible", "product_type": "Canapé", "size": "135 x 190", "color": "Gris"}
    )
    assert producto.sku
    assert producto.size == "135x190"
    assert producto.features["medida"] == "135x190"


def test_sku_duplicado_es_rechazado(catalog):
    catalog.create_product({"name": "A", "sku": "MISMO"})
    with pytest.raises(ValueError):
        catalog.create_product({"name": "B", "sku": "MISMO"})


def test_asignacion_a_cuentas_y_filtro(database, catalog):
    cuentas = AccountManager(database)
    cuentas.ensure_demo_accounts([("c1", "Cuenta 1"), ("c2", "Cuenta 2")])
    producto = catalog.create_product({"name": "Canapé 135", "size": "135x190"})

    catalog.assign_to_accounts(producto.id, ["c1"])
    assert [p.sku for p in catalog.list_products(ProductFilter(account_ref="c1"))] == [producto.sku]
    assert catalog.list_products(ProductFilter(account_ref="c2")) == []


def test_cuenta_desconocida_es_rechazada(catalog):
    producto = catalog.create_product({"name": "X"})
    with pytest.raises(ValueError):
        catalog.assign_to_accounts(producto.id, ["cuenta-que-no-existe"])


# ---------------------------------------------------------------------------
# Control de calidad
# ---------------------------------------------------------------------------
def test_anuncio_correcto_pasa_el_control():
    informe = validate_listing_data(
        title="Canapé abatible 135x190 Gris Madera",
        description="Canapé abatible con gran capacidad de almacenaje. " * 2,
        price=269.0,
        category="Hogar y jardín",
        condition="Nuevo",
        features={"color": "Gris", "material": "Madera", "medida": "135x190"},
        images=[{"file_format": "JPEG", "is_primary": True}, {"file_format": "JPEG"}, {"file_format": "PNG"}],
    )
    assert informe.can_publish
    assert informe.score == 100


@pytest.mark.parametrize(
    "cambios, campo_esperado",
    [
        ({"title": ""}, "titulo"),
        ({"description": ""}, "descripcion"),
        ({"price": None}, "precio"),
        ({"category": None}, "categoria"),
        ({"images": []}, "fotografias"),
    ],
)
def test_faltan_datos_obligatorios_bloquea_publicacion(cambios, campo_esperado):
    base = {
        "title": "Canapé abatible 135x190 Gris",
        "description": "d" * 60,
        "price": 269.0,
        "category": "Hogar",
        "condition": "Nuevo",
        "features": {"color": "Gris", "material": "Madera"},
        "images": [{"file_format": "JPEG", "is_primary": True}],
    }
    base.update(cambios)
    informe = validate_listing_data(**base)
    assert not informe.can_publish
    assert any(campo_esperado in i.field for i in informe.errors)


def test_variable_sin_rellenar_bloquea():
    informe = validate_listing_data(
        title="{producto} 135x190",
        description="d" * 60,
        price=269.0,
        category="Hogar",
        images=[{"file_format": "JPEG", "is_primary": True}],
    )
    assert not informe.can_publish


def test_formato_de_imagen_no_permitido_bloquea():
    informe = validate_listing_data(
        title="Canapé abatible 135x190",
        description="d" * 60,
        price=269.0,
        category="Hogar",
        images=[{"file_format": "GIF", "is_primary": True}],
    )
    assert not informe.can_publish
    assert any("GIF" in i.message for i in informe.errors)


def test_medida_contradictoria_se_detecta():
    informe = validate_listing_data(
        title="Canapé abatible 90x190 Gris",
        description="d" * 60,
        price=269.0,
        category="Hogar",
        features={"medida": "135x190", "color": "Gris", "material": "Madera"},
        images=[{"file_format": "JPEG", "is_primary": True}],
    )
    assert not informe.can_publish
    assert any("coherencia" in i.field for i in informe.errors)


# ---------------------------------------------------------------------------
# Duplicados
# ---------------------------------------------------------------------------
def test_duplicados_por_sku():
    grupos = find_duplicates(
        [
            {"id": 1, "titulo": "A", "sku": "X1"},
            {"id": 2, "titulo": "B", "sku": "X1"},
            {"id": 3, "titulo": "C", "sku": "X2"},
        ]
    )
    assert any(g.reason.value == "sku" and {m["id"] for m in g.members} == {1, 2} for g in grupos)


def test_duplicados_por_titulo_parecido():
    grupos = find_duplicates(
        [
            {"id": 1, "titulo": "Canapé abatible 135x190 Gris Madera", "caracteristicas": {"medida": "135x190"}},
            {"id": 2, "titulo": "Canape abatible gris madera 135x190", "caracteristicas": {"medida": "135x190"}},
        ]
    )
    assert grupos and {m["id"] for m in grupos[0].members} == {1, 2}


def test_caracteristicas_distintas_no_son_duplicados():
    grupos = find_duplicates(
        [
            {"id": 1, "titulo": "Canapé abatible Gris", "caracteristicas": {"medida": "135x190"}},
            {"id": 2, "titulo": "Canapé abatible Gris", "caracteristicas": {"medida": "90x190"}},
        ]
    )
    assert not grupos


def test_similitud_de_titulos():
    assert title_similarity("Canapé 135x190 Gris", "canape 135x190 gris") >= 95
    assert title_similarity("Canapé", "Mesa de comedor") < 50


def test_un_titulo_con_pocas_palabras_no_es_duplicado_de_todo():
    """Fallo real: «Canapé canapé canapé» salía igual a cualquier anuncio con
    «canapé» porque todas sus palabras están contenidas en el otro título."""
    assert title_similarity(
        "Canapé canapé canapé canapé canapé canapé",
        "Canape abatible 105x190 Blanco Tapizado 3D",
    ) < 80
    grupos = find_duplicates(
        [
            {"id": 1, "titulo": "Canapé canapé canapé canapé canapé canapé"},
            {"id": 2, "titulo": "Canape abatible 105x190 Blanco Tapizado 3D"},
        ]
    )
    assert grupos == []
