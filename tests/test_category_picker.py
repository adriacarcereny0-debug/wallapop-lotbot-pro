"""Selector de categoría (CategoryPicker) contra un Wallapop simulado en árbol.

Cada prueba reproduce un comportamiento de la lista de categorías y comprueba
que la categoría final solo se da por buena cuando está REALMENTE puesta.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lot_bot.wallapop.browser.category import CategoryPicker, CategorySelectionError
from lot_bot.wallapop.browser.driver import BrowserPage, same_text

OPENER = "role=combobox[name=/^Categoría/i]"
PANEL = "role=listbox"
PATH = ["Muebles y organización", "Camas y accesorios", "Estructura de camas"]
TREE = {
    "Motos": None,
    "Muebles y organización": {
        "Camas y accesorios": {"Colchones": None, "Estructura de camas": None},
        "Sofás": None,
    },
}


class CategoryPage(BrowserPage):
    """Lista de categorías simulada.

    Opciones: suggested (sugeridas), no_open (no se abre), delay (polls hasta
    que aparecen las opciones), ignore (los clics no hacen nada), stays_open
    (se elige pero no se cierra), wrong (queda otra), fails_nav (error al pulsar),
    dup_outside (el mismo texto fuera del panel), dup_hidden (copia oculta).
    """

    def __init__(self, **cfg) -> None:
        self.cfg = cfg
        self.open = False
        self.level: list[str] = []
        self.field = "Categoría y subcategoría"
        self.polls = 0
        self.clicks: list[tuple] = []

    # --- modelo ---
    def _node(self):
        node = TREE
        for part in self.level:
            node = node[part]
        return node

    def _options(self) -> list[str]:
        if not self.open or self.polls < self.cfg.get("delay", 0):
            return []
        options = list(self._node().keys())
        if not self.level and self.cfg.get("suggested", True):
            options = ["Colchones", "Estructura de camas", *options]
        return options

    # --- BrowserPage ---
    def first_visible(self, targets, timeout_ms=0):
        for t in targets:
            if t == OPENER or (t == PANEL and self.open):
                return t
        return None

    def click(self, target):
        self.clicks.append(("click", target))
        if target == OPENER and not self.cfg.get("no_open"):
            self.open, self.level = True, []

    def bbox(self, target):
        return {"x": 0, "y": 500, "width": 400, "height": 40} if target == OPENER else None

    def text_candidates(self, text, within=None, field=None):
        found = []
        if self.cfg.get("dup_hidden"):
            found.append({"index": len(found), "visible": False, "enabled": True, "box": None})
        if self.cfg.get("dup_outside") and within is None:
            found.append({"index": len(found), "visible": True, "enabled": True,
                          "box": {"x": 0, "y": 900, "width": 100, "height": 20}})
        for option in self._options():
            if same_text(text, option):
                found.append({"index": len(found), "visible": True, "enabled": True,
                              "box": {"x": 0, "y": 100 + len(found) * 30, "width": 300, "height": 25}})
        if not self.open and self.field == text:  # la categoría ya puesta en el campo
            found.append({"index": len(found), "visible": True, "enabled": True,
                          "box": {"x": 0, "y": 505, "width": 300, "height": 25}})
        return found

    def click_text(self, text, index, within=None):
        self.clicks.append(("option", text, index, within))
        if self.cfg.get("fails_nav"):
            raise RuntimeError("Target page, context or browser has been closed")
        if self.cfg.get("ignore"):
            return
        node = self._node()
        text = next((k for k in [*node, "Colchones", "Estructura de camas"] if same_text(text, k)), text)
        if text in node and node[text]:  # rama: baja un nivel
            self.level.append(text)
            return
        self.field = "Colchones" if self.cfg.get("wrong") else text
        if self.cfg.get("breadcrumb") and not self.cfg.get("wrong"):
            # Como el Wallapop real: el campo muestra la ruta de la madre.
            self.field = "Categoría y subcategoría\nMuebles y organización > Camas y accesorios"
        if not self.cfg.get("stays_open"):
            self.open = False

    def value_of(self, target):
        return self.field if target == OPENER else ""

    def outer_html(self, target, limit=20000):
        return "<ul role='listbox'>" + "".join(f"<li>{o}</li>" for o in self._options()) + "</ul>"

    def wait(self, ms):
        self.polls += 1

    # (no usados)
    def goto(self, url): ...
    def current_url(self):
        return "https://es.wallapop.com/app/catalog/upload"
    def fill(self, target, text): ...
    def click_option(self, text, timeout_ms, opener=None, opener_box=None):
        return False
    def set_files(self, target, paths): ...
    def text_of(self, target):
        return ""
    def links_matching(self, pattern):
        return []
    def screenshot(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")


def picker(page, timeout_ms=400):
    return CategoryPicker(page, [OPENER], [PANEL], timeout_ms=timeout_ms,
                          accept_timeout_ms=timeout_ms)


def test_categoria_sugerida_se_selecciona_y_se_verifica():
    page = CategoryPage()
    assert picker(page).select(PATH) == "Estructura de camas"
    assert page.field == "Estructura de camas" and not page.open  # campo puesto, panel cerrado
    assert [c for c in page.clicks if c[0] == "option"] == [
        ("option", "Estructura de camas", 0, PANEL)  # buscada DENTRO del panel
    ]


def test_sin_sugerencia_recorre_el_arbol_nivel_a_nivel():
    page = CategoryPage(suggested=False)
    assert picker(page).select(PATH) == "Estructura de camas"
    pulsadas = [c[1] for c in page.clicks if c[0] == "option"]
    assert pulsadas == PATH  # cada nivel, en orden
    assert page.field == "Estructura de camas"


def test_categoria_no_encontrada_da_error_descriptivo():
    page = CategoryPage(suggested=False)
    with pytest.raises(CategorySelectionError) as info:
        picker(page).select(["Muebles y organización", "Camas y accesorios", "Literas"])
    assert "Literas" in info.value.reason
    assert page.field == "Categoría y subcategoría"
    assert "<li>" in info.value.diagnostics["html_panel"]  # HTML del panel para diagnosticar


def test_categoria_duplicada_se_elige_la_del_panel_no_la_de_fuera():
    page = CategoryPage(dup_hidden=True, dup_outside=True)
    assert picker(page).select(PATH) == "Estructura de camas"
    opcion = next(c for c in page.clicks if c[0] == "option")
    assert opcion[3] == PANEL  # nunca la copia oculta ni la de fuera del panel
    assert opcion[2] == 1  # índice de la copia visible (la 0 es la oculta)


def test_panel_abierto_pero_opciones_tardan_en_aparecer():
    page = CategoryPage(delay=3)
    assert picker(page, timeout_ms=2000).select(PATH) == "Estructura de camas"
    assert page.polls >= 3  # esperó a que aparecieran, sin dar nada por hecho


def test_clic_que_no_provoca_seleccion_no_se_da_por_bueno():
    page = CategoryPage(ignore=True)
    with pytest.raises(CategorySelectionError) as info:
        picker(page).select(PATH)
    assert "sigue abierta" in info.value.reason or "no aparece" in info.value.reason.lower()
    assert page.field == "Categoría y subcategoría"


def test_panel_que_no_se_cierra_no_se_da_por_bueno():
    page = CategoryPage(stays_open=True)
    with pytest.raises(CategorySelectionError) as info:
        picker(page).select(PATH)
    assert "sigue abierta" in info.value.reason


def test_categoria_incorrecta_se_detecta():
    page = CategoryPage(wrong=True)
    with pytest.raises(CategorySelectionError) as info:
        picker(page).select(PATH)
    assert "Colchones" in info.value.reason and "Estructura de camas" in info.value.reason


def test_si_el_panel_no_se_abre_se_dice():
    page = CategoryPage(no_open=True)
    with pytest.raises(CategorySelectionError) as info:
        picker(page).select(PATH)
    assert "no se ha abierto la lista" in info.value.reason


def test_timeout_si_las_opciones_nunca_aparecen():
    page = CategoryPage(delay=10**12)
    with pytest.raises(CategorySelectionError):
        picker(page, timeout_ms=300).select(PATH)


def test_error_de_navegacion_se_propaga_para_guardar_diagnostico():
    page = CategoryPage(fails_nav=True)
    with pytest.raises(RuntimeError):
        picker(page).select(PATH)


def test_ya_seleccionada_no_se_toca():
    page = CategoryPage()
    page.field = "Estructura de camas"
    assert picker(page).select(PATH) == "Estructura de camas"
    assert page.clicks == []


def test_en_el_formulario_un_fallo_guarda_captura_html_y_pasos(tmp_path):
    from lot_bot.wallapop.browser.form import ListingData, ListingForm
    from lot_bot.wallapop.errors import BrowserStepError
    from tests.test_browser_integration import SITE

    page = CategoryPage(stays_open=True)
    site = SITE
    site.form.category.targets[:0] = [OPENER]
    panel_before = list(site.form.category_panel)
    site.form.category_panel[:] = [PANEL]
    site.timeout_ms, before = 300, site.timeout_ms
    try:
        form = ListingForm(
            page, site,
            ListingData("t", "d", 1.0, "1", category=" > ".join(PATH)),
            account_ref="acc-1", shots_dir=tmp_path,
        )
        with pytest.raises(BrowserStepError) as info:
            form.select_category()
    finally:
        site.form.category.targets.remove(OPENER)
        site.form.category_panel[:] = panel_before
        site.timeout_ms = before
    assert info.value.step == "Categoría"
    contexto = next(tmp_path.glob("*Categor*.json")).read_text(encoding="utf-8")
    assert "html_panel" in contexto and "Estructura de camas" in contexto and "pasos" in contexto
    assert list(tmp_path.glob("*Categor*.png"))


def test_sin_selector_de_panel_se_detecta_la_lista_por_sus_opciones():
    """Si Wallapop cambia el panel y su selector deja de valer, la lista
    abierta se reconoce por las opciones visibles fuera del campo."""
    page = CategoryPage(suggested=False)
    assert CategoryPicker(page, [OPENER], [], timeout_ms=400, accept_timeout_ms=400).select(PATH) == "Estructura de camas"
    page = CategoryPage(stays_open=True)
    with pytest.raises(CategorySelectionError):
        CategoryPicker(page, [OPENER], [], timeout_ms=400, accept_timeout_ms=400).select(PATH)


@pytest.mark.parametrize("escrita", ["Estructuras de camas", "estructura de camas", "ESTRUCTURA DE CAMA"])
def test_categoria_escrita_con_plural_o_mayusculas_se_encuentra(escrita):
    """El fallo real: la plantilla decía «Estructuras de camas» (plural) y
    Wallapop la llama «Estructura de camas»."""
    page = CategoryPage()
    assert picker(page).select([escrita]) == escrita
    assert page.field == "Estructura de camas" and not page.open


def test_el_campo_muestra_la_ruta_de_la_categoria_madre_como_en_wallapop():
    """Tu log: tras elegir, el campo muestra «Hogar y jardín > Muebles y
    organización > Camas y accesorios» y no «Estructura de camas»."""
    page = CategoryPage(breadcrumb=True)
    assert picker(page).select(PATH) == "Estructura de camas"
    assert not page.open


def test_ruta_en_el_campo_pero_de_otra_categoria_no_vale():
    page = CategoryPage(wrong=True)
    with pytest.raises(CategorySelectionError):
        picker(page).select(PATH)
