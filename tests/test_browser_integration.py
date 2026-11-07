"""Integración por navegador, con un navegador SIMULADO.

Ninguna prueba abre Wallapop ni un navegador real: se usa `FakeLauncher`, que
imita las páginas según lo que declara `wallapop_browser.yaml`.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from lot_bot.wallapop.browser import (
    BrowserLauncher,
    BrowserPage,
    BrowserProfileStore,
    BrowserSessionAuthMethod,
    BrowserWallapopService,
    UnsafeProfileLocation,
    load_site_config,
)
from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.dto import ItemDraft
from lot_bot.wallapop.errors import (
    AuthenticationError,
    BrowserStepError,
    NotAvailableWithCurrentAPIError,
    VerificationRequiredError,
)

SITE = load_site_config(Path(__file__).resolve().parents[1] / "lot_bot/resources/wallapop_browser.yaml")


FORM = SITE.form
from lot_bot.wallapop.browser.config import FormField  # noqa: E402


def form_fields() -> dict[str, object]:
    """Campos del formulario por nombre (como en wallapop_browser.yaml)."""
    fields = {
        "tipo_anuncio": FORM.listing_type,
        "titulo": FORM.title,
        "categoria": FORM.category,
        "subcategoria": FORM.subcategory,
        "descripcion": FORM.description,
        "precio": FORM.price,
        "fotos": FORM.photos,
        "publicar": FORM.submit,
        "ubicacion": FORM.location,
        "envio": FORM.shipping,
        "ubicacion_escribir": FormField(targets=FORM.location_input),
    }
    fields.update(FORM.attributes)
    return fields


class FakePage(BrowserPage):
    """Imita la web: sin sesión, la zona privada redirige a la portada y se ve
    el botón de acceso. Hay un enlace a /app/chat SIEMPRE visible (también sin
    sesión): es lo que provocaba el falso «conectada» del fallo original.

    Con sesión, el formulario de subida guarda lo que se escribe y se elige,
    muestra una miniatura por foto cargada y, al pulsar «Publicar», va a la
    página del anuncio (world["item_url"]).

    Opciones de `world`: missing (campos que no aparecen), missing_options,
    reject_image, no_thumbs, no_confirm, tamper (campo → valor distinto del
    escrito), verification, verification_at (paso donde aparece).
    """

    ALWAYS = {"a[href*='/app/chat']"}

    def __init__(self, world: dict) -> None:
        self.world = world
        self.url = ""
        self.log: list[tuple] = []
        self.values: dict[str, str] = {}
        self.thumbs = 0
        self._open: str | None = None
        self.world.setdefault("pages", []).append(self)

    # --- ayuda --------------------------------------------------------
    def _field_of(self, target):
        for name, spec in form_fields().items():
            if spec.targets and target == spec.targets[0]:
                return name
        return None

    def goto(self, url: str) -> None:
        self.log.append(("goto", url))
        if url == SITE.url("subir"):
            self.world["published"] = False
            self.values.clear()
            self.thumbs = 0
            if not self.world.get("logged_in"):
                url = SITE.url("inicio")  # Wallapop saca de la zona privada
        self.url = url

    closed = False

    def is_alive(self) -> bool:
        return not self.closed and not self.world.get("closed")

    def current_url(self) -> str:
        if self.world.get("closed"):
            raise RuntimeError("navegador cerrado")
        if self.world.get("published") and self.world.get("item_url"):
            return self.world["item_url"]
        return self.url

    def first_visible(self, targets, timeout_ms=0):
        visible = set(self.world.get("visible", set())) | self.ALWAYS
        if not self.world.get("logged_in"):
            visible |= {SITE.logged_out[0]}
        elif self.url.startswith(SITE.url("subir")) and not self.world.get("no_proof"):
            visible |= set(SITE.check_private[:1])
            if not self.world.get("published"):
                missing = self.world.get("missing", set())
                for name, spec in form_fields().items():
                    if spec.targets and name not in missing:
                        visible.add(spec.targets[0])
        if getattr(self, "cat_open", False) and FORM.category_panel:
            visible |= {FORM.category_panel[0]}
        if self.world.get("verification"):
            visible |= {SITE.verification[0]}
        if self.world.get("reject_image") and self.thumbs == 0 and self.world.get("files_sent"):
            visible |= set(FORM.photo_rejected[:1])
        if self.world.get("published") and not self.world.get("no_confirm"):
            visible |= set(SITE.success_texts)
        for target in targets:
            if target in visible:
                return target
        return None

    def fill(self, target, text):
        self.log.append(("fill", target, text))
        self.values[target] = text

    def click(self, target):
        self.log.append(("click", target))
        if target == FORM.submit.targets[0]:
            if not self.world.get("no_confirm"):
                self.world["published"] = True
            self.world["submits"] = self.world.get("submits", 0) + 1
        self._open = target
        if target == FORM.category.targets[0]:
            self.cat_open = True
        if target == FORM.shipping.targets[0]:
            self.shipping_on = not self.is_checked(target)

    def click_option(self, text, timeout_ms, opener=None, opener_box=None):
        self.log.append(("option", text))
        if text in self.world.get("missing_options", set()):
            return False
        if text in self.world.get("unconfirmed_options", set()):
            if self._open:
                self.values[self._open] = text
            self._open = None
            return False
        if self._open:
            self.values[self._open] = text
        if self._open == FORM.category.targets[0]:
            self.cat_open = False
        self._open = None  # la lista se cierra al elegir
        return True

    def set_files(self, target, paths):
        self.log.append(("files", target, list(paths)))
        self.world["files_sent"] = True
        if not self.world.get("reject_image") and not self.world.get("no_thumbs"):
            self.thumbs += len(paths)

    def value_of(self, target):
        name = self._field_of(target)
        tamper = self.world.get("tamper", {})
        if name in tamper:
            return tamper[name]
        return self.values.get(target, "")

    def is_checked(self, target):
        return getattr(self, "shipping_on", True)  # Wallapop lo trae activado

    def attached(self, target):
        if "fotos" in self.world.get("missing", set()) or self.world.get("no_file_input"):
            return 0
        return 1 if target == FORM.photos.targets[0] else 0

    def upload_with_chooser(self, button, paths, timeout_ms):
        self.log.append(("chooser", button))
        self.set_files(button, paths)

    def count(self, target):
        return self.thumbs if FORM.photo_thumbnails and target == FORM.photo_thumbnails[0] else 0

    def text_of(self, target):
        return self.world.get("user_name", "")

    def body_text(self):
        return self.world.get("body", "")

    def links_matching(self, pattern):
        return []

    def wait(self, ms):
        self.world["waited"] = self.world.get("waited", 0) + ms

    def screenshot(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")


class FakeLauncher(BrowserLauncher):
    def __init__(self, world: dict) -> None:
        self.world = world
        self.opened: list[Path] = []
        self.closed: list[Path] = []
        self.pages: list[FakePage] = []

    @contextmanager
    def open(self, profile_dir, *, visible, channels, locale):
        profile_dir = Path(profile_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)
        # Un navegador real deja ficheros en el perfil (cookies, etc.).
        (profile_dir / "Cookies").write_text("cifradas-por-el-navegador")
        self.opened.append(profile_dir)
        page = FakePage(self.world)
        self.pages.append(page)
        try:
            yield page
        finally:
            self.closed.append(profile_dir)


@pytest.fixture()
def profiles(tmp_path):
    return BrowserProfileStore(tmp_path / "browser_profiles")


def make_service(profiles, world, tmp_path, connected=True):
    launcher = FakeLauncher(world)
    SITE.check_wait_ms = 0
    service = BrowserWallapopService(
        profiles,
        launcher=launcher,
        site=SITE,
        screenshots_dir=tmp_path / "shots",
        is_account_connected=lambda ref: connected,
    )
    return service, launcher


def draft(tmp_path):
    image = tmp_path / "foto.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    return ItemDraft(
        title="Canapé canapé canapé canapé canapé",
        description="GRAN OFERTA",
        price=11.44,
        category="Hogar y jardín",
        condition="Nuevo",
        attributes={"estado": "Nuevo", "uso": "Dormitorio", "color": "Gris y Blanco",
                    "material": "Madera"},
        image_paths=[str(image)],
    )


# ---------------------------------------------------------------------------
# Selectores centralizados
# ---------------------------------------------------------------------------
def test_los_selectores_viven_en_un_unico_fichero_y_no_estan_verificados():
    assert SITE.verified is False  # honestidad: no se han probado contra la web real
    assert SITE.url("inicio").startswith("https://es.wallapop.com")
    campos = form_fields()
    for campo in ("titulo", "descripcion", "precio", "categoria", "fotos", "publicar",
                  "estado", "uso", "color", "material"):
        assert campos[campo].targets, campo
    assert FORM.photo_thumbnails, "debe saber comprobar que la foto se ha cargado"
    assert SITE.verification, "debe saber reconocer verificaciones para detenerse"


def test_el_codigo_no_contiene_selectores_de_wallapop():
    raiz = Path(__file__).resolve().parents[1] / "lot_bot" / "wallapop" / "browser"
    for fichero in raiz.glob("*.py"):
        texto = fichero.read_text(encoding="utf-8")
        assert "input[name=" not in texto and "sale_price" not in texto, fichero.name


def test_una_copia_local_sustituye_a_la_incluida(temp_paths):
    from lot_bot.wallapop.browser.config import local_config_path

    destino = local_config_path()
    destino.write_text("verificado: true\nurls: {inicio: 'https://es.wallapop.com/'}\n", encoding="utf-8")
    assert load_site_config().verified is True


# ---------------------------------------------------------------------------
# Publicación
# ---------------------------------------------------------------------------
def test_publicar_rellena_el_formulario_y_devuelve_la_url(profiles, tmp_path):
    world = {
        "logged_in": True,
        "item_url": "https://es.wallapop.com/item/canape-canape-123456789",
    }
    service, launcher = make_service(profiles, world, tmp_path)
    result = service.create_item("acc-1", draft(tmp_path))
    assert result.success and result.data["confirmado"]
    assert result.data["url"] == world["item_url"]
    assert result.item_id == "canape-canape-123456789"
    log = launcher.pages[0].log
    escritos = {entry[2] for entry in log if entry[0] == "fill"}
    assert "Canapé canapé canapé canapé canapé" in escritos
    assert "GRAN OFERTA" in escritos
    assert "11,44" in escritos  # formato de precio español
    assert any(entry[0] == "files" for entry in log)
    for opcion in ("Hogar y jardín", "Nuevo", "Dormitorio", "Gris y Blanco", "Madera"):
        assert ("option", opcion) in log
    # Todos los pasos, en orden, hechos por LOT Bot (no por el usuario).
    assert result.data["pasos"] == [
        "Comprobar sesión", "Abrir crear anuncio", "Tipo de anuncio", "Título", "Fotos",
        "Categoría", "Detalles listos", "Característica Estado", "Característica Uso", "Característica Color",
        "Característica Material", "Descripción", "Precio", "Envío desactivado",
        "Comprobar formulario",
        "Publicar", "Confirmación de Wallapop",
    ]


def test_si_wallapop_no_confirma_no_se_da_por_publicado(profiles, tmp_path):
    world = {"logged_in": True, "no_confirm": True}
    service, _ = make_service(profiles, world, tmp_path)
    service.site.success_timeout_ms = 0
    try:
        result = service.create_item("acc-1", draft(tmp_path))
    finally:
        service.site.success_timeout_ms = SITE.success_timeout_ms
    assert not result.success
    assert result.data["codigo"] == "RESULTADO_NO_CONFIRMADO"
    assert "Resultado no confirmado" in result.message
    assert world["submits"] == 1  # se pulsó una sola vez: nada de reintentos a ciegas
    assert list((tmp_path / "shots").glob("*confirmacion*.png"))


def test_confirmado_por_mensaje_sin_url(profiles, tmp_path):
    service, _ = make_service(profiles, {"logged_in": True}, tmp_path)
    result = service.create_item("acc-1", draft(tmp_path))
    assert result.success and result.data["url"] is None
    assert "no ha mostrado la dirección" in result.message


def test_sin_sesion_no_se_publica(profiles, tmp_path):
    service, _ = make_service(profiles, {"logged_in": False}, tmp_path)
    with pytest.raises(AuthenticationError) as info:
        service.create_item("acc-1", draft(tmp_path))
    assert "volver a iniciar sesión" in info.value.user_message


def test_cuenta_no_conectada_no_abre_el_navegador(profiles, tmp_path):
    service, launcher = make_service(profiles, {"logged_in": True}, tmp_path, connected=False)
    with pytest.raises(AuthenticationError):
        service.create_item("acc-1", draft(tmp_path))
    assert launcher.opened == []


def test_una_verificacion_sin_nadie_que_responda_detiene_la_publicacion(profiles, tmp_path):
    service, launcher = make_service(
        profiles, {"logged_in": True, "verification": True}, tmp_path
    )
    with pytest.raises(VerificationRequiredError) as info:
        service.create_item("acc-1", draft(tmp_path))
    assert "pulsa Continuar" in info.value.user_message
    log = launcher.pages[0].log
    assert not any(entry[0] in ("fill", "click") for entry in log)  # nada automático


def test_captcha_a_mitad_espera_al_usuario_y_sigue_desde_el_mismo_paso(profiles, tmp_path):
    """El CAPTCHA aparece tras escribir el título: LOT Bot NO lo toca, espera a
    que el usuario lo complete y pulse «Continuar», y sigue con la categoría
    sin volver a empezar ni pedirle que rellene nada."""
    world = {"logged_in": True, "item_url": "https://es.wallapop.com/item/c-9"}
    service, launcher = make_service(profiles, world, tmp_path)
    avisos = []

    def gate(ref, mensaje):
        avisos.append((ref, mensaje))
        page = launcher.pages[0]
        assert not any(e[0] in ("fill", "click", "option") and e[-1] == SITE.verification[0]
                       for e in page.log)
        world["verification"] = False  # el usuario completa el CAPTCHA a mano
        return True

    service.user_gate = gate
    original_fill = FakePage.fill

    def fill_then_captcha(self, target, text):
        original_fill(self, target, text)
        if target == FORM.title.targets[0]:
            world["verification"] = True

    FakePage.fill = fill_then_captcha
    try:
        result = service.create_item("acc-1", draft(tmp_path))
    finally:
        FakePage.fill = original_fill
    assert result.success
    assert avisos == [("acc-1", VerificationRequiredError.user_message)]
    log = launcher.pages[0].log
    # Una sola carga: la comprobación de sesión deja el formulario abierto y
    # tras el CAPTCHA se sigue en la misma página (no se reinicia).
    assert [e for e in log if e[0] == "goto"].__len__() == 1
    titulos = [e for e in log if e[0] == "fill" and e[1] == FORM.title.targets[0]]
    assert len(titulos) == 1  # el título no se vuelve a escribir
    assert list((tmp_path / "shots").glob("*verificacion*.json"))


def test_si_un_campo_no_aparece_se_indica_el_paso_y_se_guarda_captura(profiles, tmp_path):
    world = {"logged_in": True, "missing": {"precio"}}
    service, _ = make_service(profiles, world, tmp_path)
    service.site.timeout_ms = 0
    try:
        with pytest.raises(BrowserStepError) as info:
            service.create_item("acc-1", draft(tmp_path))
    finally:
        service.site.timeout_ms = SITE.timeout_ms
    assert info.value.step == "Precio"
    assert info.value.screenshot and Path(info.value.screenshot).is_file()
    contexto = Path(info.value.screenshot).with_suffix(".json")
    datos = json.loads(contexto.read_text(encoding="utf-8"))
    assert datos["paso"] == "Precio" and "Descripción" in datos["pasos_completados"]
    assert "cookie" not in contexto.read_text(encoding="utf-8").lower()
    assert "wallapop_browser.yaml" in info.value.user_message
    assert world.get("submits") is None  # no se ha pulsado «Publicar»


def test_si_no_existe_una_opcion_se_sigue_y_se_publica_igual(profiles, tmp_path):
    """Una característica que falla no deja sin precio, envío, ubicación ni
    «Publicar»: se sigue y se informa."""
    world = {"logged_in": True, "missing_options": {"Madera"}}
    service, launcher = make_service(profiles, world, tmp_path)
    result = service.create_item("acc-1", draft(tmp_path))
    assert result.success and world.get("submits")
    assert any("Material" in o and "NO se ha podido" in o for o in result.data["omitidos"])


def test_opcion_puesta_aunque_el_clic_no_se_confirme_sigue_adelante(profiles, tmp_path):
    """Tu log: «Nuevo» queda puesto pero el bot se quedaba en Estado."""
    world = {"logged_in": True, "unconfirmed_options": {"Nuevo"}}
    service, _ = make_service(profiles, world, tmp_path)
    result = service.create_item("acc-1", draft(tmp_path))
    assert result.success and world.get("submits")
    assert not any("Estado" in o for o in result.data["omitidos"])


def test_color_sin_valor_exacto_usa_la_equivalencia_y_lo_dice(profiles, tmp_path):
    world = {"logged_in": True, "missing_options": {"Gris y Blanco"}}
    service, launcher = make_service(profiles, world, tmp_path)
    result = service.create_item("acc-1", draft(tmp_path))
    assert result.success
    assert ("option", "Gris") in launcher.pages[0].log
    assert any("elegido «Gris»" in o for o in result.data["omitidos"])


def test_campo_opcional_que_no_existe_se_omite_y_se_informa(profiles, tmp_path):
    world = {"logged_in": True, "missing": {"material", "tipo_anuncio"}}
    service, _ = make_service(profiles, world, tmp_path)
    result = service.create_item("acc-1", draft(tmp_path))
    assert result.success
    assert any("Material" in o for o in result.data["omitidos"])


@pytest.mark.parametrize(
    "campo, esperado",
    [("titulo", "Título"), ("precio", "Precio"), ("descripcion", "Descripción"),
     ("color", "Color")],
)
def test_si_el_formulario_no_coincide_no_se_publica(profiles, tmp_path, campo, esperado):
    from lot_bot.wallapop.errors import FormMismatchError

    world = {"logged_in": True, "tamper": {campo: "otra cosa 99"}}
    service, _ = make_service(profiles, world, tmp_path)
    with pytest.raises((FormMismatchError, BrowserStepError)) as info:
        service.create_item("acc-1", draft(tmp_path))
    if isinstance(info.value, FormMismatchError):
        assert esperado in info.value.fields
        assert "NO se ha publicado" in info.value.user_message
    else:  # las características se comprueban nada más elegirlas
        assert esperado in info.value.step and "otra cosa 99" in info.value.detail
    assert world.get("submits") is None


@pytest.mark.parametrize("fallo", ["reject_image", "no_thumbs"])
def test_si_la_foto_no_se_carga_no_se_publica(profiles, tmp_path, fallo):
    from lot_bot.wallapop.errors import ImageUploadError

    world = {"logged_in": True, fallo: True}
    service, _ = make_service(profiles, world, tmp_path)
    service.site.form.photo_wait_ms = 0
    try:
        with pytest.raises(ImageUploadError):
            service.create_item("acc-1", draft(tmp_path))
    finally:
        service.site.form.photo_wait_ms = SITE.form.photo_wait_ms
    assert world.get("submits") is None


def test_sin_foto_no_se_publica(profiles, tmp_path):
    world = {"logged_in": True}
    service, _ = make_service(profiles, world, tmp_path)
    sin_foto = draft(tmp_path)
    sin_foto.image_paths = []
    with pytest.raises(BrowserStepError) as info:
        service.create_item("acc-1", sin_foto)
    assert info.value.step == "Fotos"
    assert world.get("submits") is None


def test_solo_declara_lo_que_hace_de_verdad(profiles, tmp_path):
    service, _ = make_service(profiles, {"logged_in": True}, tmp_path)
    assert service.capabilities() == {
        Capability.ACCOUNT_PROFILE,
        Capability.CREATE_ITEM,
        Capability.ITEM_STATS,
        Capability.DELETE_ITEM,  # en la página del anuncio, con su dirección
        Capability.LIST_ITEMS,  # lista «Tus productos»
        Capability.UPDATE_ITEM,  # «Editar»: título
        Capability.UPDATE_ITEM_PRICE,  # «Editar»: precio
    }
    for llamada in (
        lambda: service.update_item("acc-1", "x", {"description": "otra"}),
        lambda: service.send_message("acc-1", "c", "hola"),
    ):
        with pytest.raises(NotAvailableWithCurrentAPIError):
            llamada()


# ---------------------------------------------------------------------------
# Aislamiento de sesiones
# ---------------------------------------------------------------------------
def test_cada_cuenta_usa_su_propio_perfil(profiles, tmp_path):
    service, launcher = make_service(profiles, {"logged_in": True}, tmp_path)
    service.create_item("acc-1", draft(tmp_path))
    service.create_item("acc-2", draft(tmp_path))
    assert launcher.opened[0] != launcher.opened[1]
    assert launcher.opened[0].name == "acc-1" and launcher.opened[1].name == "acc-2"
    assert launcher.opened[0].parent == launcher.opened[1].parent == profiles.root


def test_borrar_una_sesion_no_toca_las_demas(profiles):
    for ref in ("acc-1", "acc-2"):
        (profiles.profile_dir(ref) / "Cookies").write_text("x")
    assert profiles.delete("acc-1")
    assert not profiles.exists("acc-1")
    assert profiles.exists("acc-2")


@pytest.mark.parametrize("ref", ["../otra", "a/b", "", "x" * 80, "..", "c:\\temp"])
def test_referencias_de_cuenta_peligrosas_se_rechazan(profiles, ref):
    with pytest.raises(ValueError):
        profiles.profile_dir(ref)


def test_no_se_guardan_sesiones_dentro_de_un_repositorio_git(tmp_path):
    repo = tmp_path / "proyecto"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(UnsafeProfileLocation):
        BrowserProfileStore(repo / "datos" / "browser_profiles")


def test_desconectar_y_eliminar_borran_la_sesion_guardada(database, profiles, secret_box):
    from lot_bot.wallapop.account_manager import AccountManager
    from lot_bot.wallapop.auth.base import AuthCredential, AuthKind

    manager = AccountManager(database, secret_box)
    manager.set_browser_profiles(profiles)
    for alias in ("Uno", "Dos"):
        info = manager.add_account(alias, internal_ref=f"acc-{alias.lower()}")
        (profiles.profile_dir(info.internal_ref) / "Cookies").write_text("x")
        manager.store_credential(
            info.internal_ref,
            AuthCredential(kind=AuthKind.BROWSER_SESSION, metadata={"account_ref": info.internal_ref}),
        )
    manager.disconnect("acc-uno")
    assert not profiles.exists("acc-uno") and profiles.exists("acc-dos")
    manager.remove_account("acc-dos")
    assert not profiles.exists("acc-dos")


# ---------------------------------------------------------------------------
# Conexión de la cuenta
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Conexión: nunca «conectada» sin comprobar la sesión de verdad
# ---------------------------------------------------------------------------
def test_abrir_el_navegador_no_conecta_la_cuenta(profiles, tmp_path):
    """El fallo original: el enlace /app/chat se ve sin sesión y la cuenta se
    daba por conectada. Ahora no basta con abrir el navegador."""
    world = {"logged_in": False}
    service, launcher = make_service(profiles, world, tmp_path)
    method = BrowserSessionAuthMethod(service, login_timeout=30)
    session = method.start_login("acc-1")
    assert session.wait_for(session.WAITING) == session.WAITING
    assert not session.verified
    # El navegador sigue abierto mientras el usuario inicia sesión.
    assert launcher.opened and not launcher.closed
    # Sin comprobación real, authenticate se niega.
    assert not method.authenticate("acc-1").success
    session.request_check()
    assert session.wait_for(session.NOT_LOGGED) == session.NOT_LOGGED
    assert not session.verified
    assert not launcher.closed  # sigue abierto para que el usuario lo intente
    session.close()
    assert launcher.closed


def test_la_cuenta_solo_se_conecta_tras_comprobar_la_sesion(database, profiles, secret_box, tmp_path):
    from lot_bot.database.models import AccountStatus
    from lot_bot.wallapop.account_manager import AccountManager

    world = {"logged_in": False, "user_name": "Ana"}
    service, _ = make_service(profiles, world, tmp_path)
    service.site.user_name = ["#nombre"]
    world["visible"] = {"#nombre"}
    method = BrowserSessionAuthMethod(service, login_timeout=30)
    manager = AccountManager(database, secret_box)
    info = manager.add_account("Principal", internal_ref="acc-1")

    session = method.start_login("acc-1")
    session.wait_for(session.WAITING)
    session.request_check()
    session.wait_for(session.NOT_LOGGED)
    assert not manager.connect("acc-1", method=method, session_check=session.check).success
    assert manager.get_account("acc-1").status != AccountStatus.CONNECTED

    world["logged_in"] = True  # el usuario inicia sesión en la ventana
    session.request_check()
    assert session.wait_for(session.VERIFIED) == session.VERIFIED
    session.close()
    outcome = manager.connect("acc-1", method=method, session_check=session.check)
    assert outcome.success
    account = manager.get_account("acc-1")
    assert account.status == AccountStatus.CONNECTED and account.wallapop_login == "Ana"
    credential = manager.get_credential(info.internal_ref)
    assert credential.headers == {} and credential.cookies == {}  # nada sensible
    assert "password" not in str(credential.to_storage()).lower()
    service.site.user_name = []


def test_una_verificacion_durante_el_login_se_deja_al_usuario(profiles, tmp_path):
    world = {"logged_in": True, "verification": True}
    service, launcher = make_service(profiles, world, tmp_path)
    session = BrowserSessionAuthMethod(service, login_timeout=30).start_login("acc-1")
    session.wait_for(session.WAITING)
    session.request_check()
    assert session.wait_for(session.VERIFICATION) == session.VERIFICATION
    fills = [e for p in launcher.pages for e in p.log if e[0] in ("fill", "click")]
    assert fills == []  # no se toca nada de la verificación
    session.close()


def test_si_el_usuario_cierra_el_navegador_no_se_conecta(profiles, tmp_path):
    world = {"logged_in": False}
    service, _ = make_service(profiles, world, tmp_path)
    session = BrowserSessionAuthMethod(service, login_timeout=30).start_login("acc-1")
    session.wait_for(session.WAITING)
    world["closed"] = True
    assert session.wait_for(session.CLOSED, timeout=5) == session.CLOSED
    assert not session.verified


def test_el_perfil_es_persistente_y_sobrevive_al_cierre(profiles, tmp_path):
    world = {"logged_in": True}
    service, launcher = make_service(profiles, world, tmp_path)
    session = BrowserSessionAuthMethod(service, login_timeout=30).start_login("acc-1")
    session.wait_for(session.WAITING)
    session.close()
    assert profiles.exists("acc-1")  # no es un perfil temporal
    assert launcher.opened[0] == profiles.profile_dir("acc-1")
    assert service.check_session("acc-1").ok  # la sesión guardada se reutiliza


# ---------------------------------------------------------------------------
# Reutilización y caducidad de la sesión
# ---------------------------------------------------------------------------
def test_varias_publicaciones_reutilizan_el_mismo_navegador(profiles, tmp_path):
    world = {"logged_in": True, "item_url": "https://es.wallapop.com/item/x-1"}
    service, launcher = make_service(profiles, world, tmp_path)
    for _ in range(3):
        assert service.create_item("acc-b", draft(tmp_path)).success
    assert service.pool.open_count == {"acc-b": 1}
    assert len(launcher.opened) == 1 and not launcher.closed
    service.shutdown()
    assert launcher.closed == launcher.opened


def test_dos_cuentas_con_navegadores_aislados(profiles, tmp_path):
    world = {"logged_in": True}
    service, launcher = make_service(profiles, world, tmp_path)
    service.create_item("acc-a", draft(tmp_path))
    service.create_item("acc-b", draft(tmp_path))
    service.create_item("acc-a", draft(tmp_path))
    assert service.pool.open_count == {"acc-a": 1, "acc-b": 1}
    assert {p.name for p in launcher.opened} == {"acc-a", "acc-b"}
    assert launcher.opened[0] != launcher.opened[1]
    service.shutdown()


def test_sesion_caducada_al_publicar(profiles, tmp_path):
    world = {"logged_in": True}
    service, _ = make_service(profiles, world, tmp_path)
    assert service.create_item("acc-1", draft(tmp_path)).success
    world["logged_in"] = False  # Wallapop ha cerrado la sesión
    with pytest.raises(AuthenticationError):
        service.create_item("acc-1", draft(tmp_path))
    service.shutdown()


def test_backend_navegador_desde_la_configuracion(database, temp_paths):
    from lot_bot.config.settings import Settings
    from lot_bot.wallapop.account_manager import AccountManager
    from lot_bot.wallapop.factory import build_backend

    manager = AccountManager(database)
    backend = build_backend(Settings(), manager, "navegador", launcher=FakeLauncher({}))
    assert not backend.demo
    assert isinstance(backend.service, BrowserWallapopService)
    assert "uso personal autorizado" in backend.reason
    # La elección de la pantalla manda sobre LOT_BOT_DEMO_MODE=true del .env
    # (el .env de ejemplo lo trae así).
    elegido = build_backend(
        Settings(LOT_BOT_DEMO_MODE=True), manager, "navegador", launcher=FakeLauncher({})
    )
    assert not elegido.demo
    assert build_backend(Settings(LOT_BOT_DEMO_MODE=True), manager, "demo").demo


def test_sin_eleccion_se_queda_en_demo(database, temp_paths):
    from lot_bot.config.settings import Settings
    from lot_bot.wallapop.account_manager import AccountManager
    from lot_bot.wallapop.factory import build_backend

    assert build_backend(Settings(), AccountManager(database), "").demo


def test_sin_campo_de_archivos_usa_el_boton_de_subir_fotos(profiles, tmp_path):
    world = {"logged_in": True, "no_file_input": True, "visible": set(FORM.photo_buttons[:1])}
    service, launcher = make_service(profiles, world, tmp_path)
    result = service.create_item("acc-1", draft(tmp_path))
    assert result.success
    assert ("chooser", FORM.photo_buttons[0]) in launcher.pages[0].log


def test_si_no_hay_donde_subir_la_foto_se_dice_claramente(profiles, tmp_path):
    world = {"logged_in": True, "no_file_input": True}
    service, _ = make_service(profiles, world, tmp_path)
    service.site.timeout_ms = 0
    try:
        with pytest.raises(BrowserStepError) as info:
            service.create_item("acc-1", draft(tmp_path))
    finally:
        service.site.timeout_ms = SITE.timeout_ms
    assert info.value.step == "Fotos"
    assert "dónde subir las fotos" in info.value.detail
    assert world.get("submits") is None


def test_la_foto_se_sube_como_jpeg_sin_tocar_el_original(profiles, tmp_path):
    from PIL import Image

    world = {"logged_in": True}
    service, launcher = make_service(profiles, world, tmp_path)
    original = tmp_path / "foto.png"
    Image.new("RGBA", (3000, 2000), (1, 2, 3, 255)).save(original)
    datos = draft(tmp_path)
    datos.image_paths = [str(original)]
    assert service.create_item("acc-1", datos).success
    enviado = next(e for e in launcher.pages[0].log if e[0] == "files")[2][0]
    with Image.open(enviado) as img:
        assert img.format == "JPEG" and max(img.size) <= 2048
    with Image.open(original) as img:
        assert img.size == (3000, 2000)  # el original no se toca
