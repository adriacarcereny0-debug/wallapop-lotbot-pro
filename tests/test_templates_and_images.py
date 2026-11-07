"""Pruebas de plantillas y de gestion de imagenes."""

from __future__ import annotations

import pytest
from PIL import Image

from lot_bot.images.service import ImageService, ImageValidationError
from lot_bot.templates_engine.defaults import CANAPE_TEMPLATE, ensure_default_templates
from lot_bot.templates_engine.engine import TemplateEngine, extract_variables


@pytest.fixture()
def contexto_completo():
    return TemplateEngine.build_context(
        product={
            "product_type": "Canapé abatible",
            "size": "135x190",
            "color": "Gris",
            "material": "Madera",
            "condition": "Nuevo",
            "category": "Hogar y jardín",
            "price": 269.0,
        },
        business={"whatsapp": "600000000", "name": "Tienda"},
        extra={"precio_90": "230", "precio_135": "270", "precio_150": "290"},
    )


def test_extraccion_de_variables():
    assert extract_variables("{producto} {medida} {producto}") == ["producto", "medida"]


def test_plantilla_completa_no_deja_huecos(contexto_completo):
    resultado = TemplateEngine().render(
        title_pattern=CANAPE_TEMPLATE["title_pattern"],
        description_pattern=CANAPE_TEMPLATE["description_pattern"],
        features_pattern=CANAPE_TEMPLATE["features_pattern"],
        tags_pattern=CANAPE_TEMPLATE["tags_pattern"],
        context=contexto_completo,
    )
    assert resultado.is_complete
    assert resultado.title == "Canapé abatible 135x190 Gris Madera"
    assert "{" not in resultado.description
    assert "600000000" in resultado.description
    assert "canape" in resultado.tags


def test_variable_ausente_se_marca_y_no_se_inventa():
    resultado = TemplateEngine().render(
        title_pattern="{producto} {medida}",
        description_pattern="Contacto: {whatsapp}",
        context={"producto": "Canapé"},
    )
    assert not resultado.is_complete
    assert set(resultado.missing_variables) == {"medida", "whatsapp"}
    # El marcador sigue visible: nadie publica un hueco sin darse cuenta.
    assert "{medida}" in resultado.title


def test_modo_estricto_lanza_error():
    from lot_bot.templates_engine.engine import TemplateError

    with pytest.raises(TemplateError):
        TemplateEngine(strict=True).render_text("{falta}", {})


def test_la_plantilla_base_no_contiene_datos_reales_del_cliente():
    """El repositorio no debe incluir el telefono ni los importes reales.

    Los datos comerciales van como variables y se rellenan desde Ajustes.
    """
    import re

    texto = CANAPE_TEMPLATE["description_pattern"]
    assert "{whatsapp}" in texto
    assert "{precio_135}" in texto

    # Sin marcadores, no debe quedar ningun telefono de 9 cifras...
    sin_variables = re.sub(r"\{[a-z_0-9]+\}", "", texto)
    assert not re.search(r"\d{9}", sin_variables)
    # ...ni ningun importe escrito a mano (solo quedan medidas tipo 135x190).
    assert not re.search(r"(?<![x\d])\d{2,4}\s*€", sin_variables)


def test_plantillas_por_defecto_se_crean_una_sola_vez(database):
    assert ensure_default_templates(database)
    assert ensure_default_templates(database) == []


# ---------------------------------------------------------------------------
# Imagenes
# ---------------------------------------------------------------------------
@pytest.fixture()
def imagenes(tmp_path):
    servicio = ImageService(storage_root=tmp_path / "almacen")
    buena = tmp_path / "foto.jpg"
    Image.new("RGB", (900, 700), (120, 90, 40)).save(buena)
    copia = tmp_path / "foto_copia.jpg"
    Image.new("RGB", (900, 700), (120, 90, 40)).save(copia)
    pequena = tmp_path / "pequena.png"
    Image.new("RGB", (120, 120), (10, 10, 10)).save(pequena)
    mala = tmp_path / "animada.gif"
    Image.new("RGB", (900, 700)).save(mala)
    return servicio, buena, copia, pequena, mala


def test_validacion_de_formatos(imagenes):
    servicio, buena, _copia, pequena, mala = imagenes
    assert servicio.validate_file(buena)[0]
    assert not servicio.validate_file(mala)[0]
    valida, _errores, avisos = servicio.validate_file(pequena)
    assert valida and any("Resolución baja" in a for a in avisos)


def test_importacion_descarta_repetidas_y_formatos_invalidos(imagenes):
    servicio, buena, copia, pequena, mala = imagenes
    importadas, incidencias = servicio.import_many([buena, copia, pequena, mala], "CAN-135")
    assert [i.original_name for i in importadas] == ["foto.jpg", "pequena.png"]
    assert importadas[0].is_primary and importadas[0].position == 0
    assert len(incidencias) == 2


def test_hash_perceptual_detecta_la_misma_imagen(imagenes):
    servicio, buena, copia, *_ = imagenes
    assert servicio.perceptual_hash(buena) == servicio.perceptual_hash(copia)
    assert servicio.content_hash(buena) == servicio.content_hash(copia)


def test_imagen_invalida_lanza_error(imagenes, tmp_path):
    servicio, *_ = imagenes
    with pytest.raises(ImageValidationError):
        servicio.import_image(tmp_path / "no_existe.jpg", "CAN-135")


def test_generacion_por_ia_no_disponible(imagenes):
    servicio, *_ = imagenes
    assert servicio.generation_available() is False
    with pytest.raises(NotImplementedError):
        servicio.generate_image("un canapé gris")
