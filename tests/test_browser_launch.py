"""Arranque del navegador: sandbox activado (sin --no-sandbox), perfil propio y
persistente por cuenta, nada de invitado/incógnito, registro de fallos sin
datos sensibles y flujo de inicio de sesión que nunca da un falso «conectada»."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

import pytest

from lot_bot.wallapop.browser.driver import (
    FORBIDDEN_ARGS,
    UNSAFE_DEFAULT_ARGS,
    BrowserCandidate,
    PlaywrightLauncher,
    build_launch_options,
    describe_launch,
    find_browsers,
    sandbox_enabled,
)
from tests.test_browser_integration import make_service

CHROME = BrowserCandidate("Google Chrome", executable=r"C:\Program Files\Google\Chrome\Application\chrome.exe")


# ---------------------------------------------------------------------------
# Sandbox y argumentos
# ---------------------------------------------------------------------------
def test_windows_nunca_recibe_no_sandbox(tmp_path):
    options = build_launch_options(CHROME, tmp_path / "acc-1", visible=True, locale="es-ES", platform="win32", env={})
    # Playwright añade «--no-sandbox» si chromium_sandbox no es True.
    assert options["chromium_sandbox"] is True
    assert not FORBIDDEN_ARGS & set(options["args"])
    assert "--no-sandbox" not in " ".join(options["args"])


def test_en_windows_no_se_puede_desactivar_el_sandbox(tmp_path):
    assert sandbox_enabled("win32", {"LOT_BOT_BROWSER_SANDBOX": "0"}) is True
    assert sandbox_enabled("darwin", {"LOT_BOT_BROWSER_SANDBOX": "0"}) is True
    # Solo en Linux, y solo si el desarrollador lo pide expresamente.
    assert sandbox_enabled("linux", {"LOT_BOT_BROWSER_SANDBOX": "0"}) is False
    assert sandbox_enabled("linux", {}) is True


def test_se_quitan_opciones_que_reducen_la_seguridad(tmp_path):
    options = build_launch_options(CHROME, tmp_path / "a", visible=True, locale="es-ES", platform="win32", env={})
    for flag in (
        "--disable-client-side-phishing-detection",
        "--disable-popup-blocking",
        "--disable-component-update",
    ):
        assert flag in options["ignore_default_args"]
    # No se quita el aviso de automatización ni se añade nada para ocultarla.
    assert "--enable-automation" not in options["ignore_default_args"]
    assert not any("automation" in arg.lower() for arg in options["args"])
    assert set(UNSAFE_DEFAULT_ARGS) == set(options["ignore_default_args"])


def test_ventana_normal_ni_invitado_ni_incognito(tmp_path):
    perfil = tmp_path / "browser_profiles" / "acc-1"
    options = build_launch_options(CHROME, perfil, visible=True, locale="es-ES", platform="win32", env={})
    assert options["user_data_dir"] == str(perfil)  # perfil propio y persistente
    assert options["headless"] is False
    for flag in ("--guest", "--incognito", "--inprivate", "--temp-profile"):
        assert flag not in options["args"]
    assert options["executable_path"] == CHROME.executable


# ---------------------------------------------------------------------------
# Detección de Chrome / Edge en Windows
# ---------------------------------------------------------------------------
def test_detecta_chrome_y_edge_instalados_en_windows():
    env = {
        "PROGRAMFILES": r"C:\Program Files",
        "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
        "LOCALAPPDATA": r"C:\Users\ana\AppData\Local",
    }
    instalados = {
        str(Path(r"C:\Program Files") / "Google/Chrome/Application/chrome.exe"),
        str(Path(r"C:\Program Files (x86)") / "Microsoft/Edge/Application/msedge.exe"),
    }
    encontrados = find_browsers(
        ["chrome", "msedge", "chromium"], platform="win32", env=env, exists=lambda p: str(p) in instalados
    )
    assert [c.name for c in encontrados] == ["Google Chrome", "Microsoft Edge", "Chromium de Playwright"]
    assert encontrados[0].executable.endswith("chrome.exe")
    assert encontrados[1].executable.endswith("msedge.exe")


def test_sin_chrome_instalado_se_prueba_por_canal():
    encontrados = find_browsers(["chrome", "msedge"], platform="win32", env={}, exists=lambda p: False)
    assert [(c.name, c.channel) for c in encontrados] == [
        ("Google Chrome", "chrome"),
        ("Microsoft Edge", "msedge"),
    ]


def test_ruta_explicita_del_navegador():
    encontrados = find_browsers(["chrome"], env={"LOT_BOT_BROWSER_EXECUTABLE": r"D:\Chrome\chrome.exe"})
    assert [c.executable for c in encontrados] == [r"D:\Chrome\chrome.exe"]


# ---------------------------------------------------------------------------
# Registro de fallos
# ---------------------------------------------------------------------------
def test_un_fallo_de_arranque_se_registra_con_detalle_y_sin_secretos(tmp_path, monkeypatch, caplog):
    import playwright.sync_api as api

    usados: list[dict] = []

    class Chromium:
        def launch_persistent_context(self, **options):
            usados.append(options)
            raise api.Error("Executable doesn't exist at C:\\\\Chrome\\\\chrome.exe")

    class PW:
        chromium = Chromium()

    @contextmanager
    def fake_sync_playwright():
        yield PW()

    monkeypatch.setattr(api, "sync_playwright", fake_sync_playwright)
    monkeypatch.setenv("LOT_BOT_BROWSER_EXECUTABLE", r"C:\Chrome\chrome.exe")
    from lot_bot.wallapop.browser.driver import BrowserUnavailable

    with caplog.at_level(logging.ERROR), pytest.raises(BrowserUnavailable):
        with PlaywrightLauncher().open(tmp_path / "acc-1", visible=True, channels=["chrome"], locale="es-ES"):
            pass
    texto = caplog.text
    assert "navegador=Navegador indicado" in texto
    assert r"ejecutable=C:\Chrome\chrome.exe" in texto
    assert f"perfil={tmp_path / 'acc-1'}" in texto
    assert "args=" in texto and "Executable doesn't exist" in texto
    assert usados[0]["chromium_sandbox"] is True
    for sensible in ("password", "cookie", "token"):
        assert sensible not in texto.lower()


def test_descripcion_del_arranque(tmp_path):
    options = build_launch_options(CHROME, tmp_path / "p", visible=True, locale="es-ES", platform="win32", env={})
    texto = describe_launch(CHROME, options)
    assert "sandbox=True" in texto and "Google Chrome" in texto


# ---------------------------------------------------------------------------
# Perfiles por cuenta
# ---------------------------------------------------------------------------
@pytest.fixture()
def profiles(tmp_path):
    from lot_bot.wallapop.browser import BrowserProfileStore

    return BrowserProfileStore(tmp_path / "browser_profiles")


def test_cada_cuenta_tiene_su_perfil_y_persiste(profiles, tmp_path):
    from lot_bot.wallapop.browser import BrowserSessionAuthMethod

    service, launcher = make_service(profiles, {"logged_in": True}, tmp_path)
    auth = BrowserSessionAuthMethod(service, login_timeout=30)
    for ref in ("cuenta_1", "cuenta_2", "cuenta_3"):
        session = auth.start_login(ref)
        session.wait_for(session.WAITING)
        session.close()
    assert [p.name for p in launcher.opened] == ["cuenta_1", "cuenta_2", "cuenta_3"]
    assert len({str(p) for p in launcher.opened}) == 3  # nunca compartidos
    for ref in ("cuenta_1", "cuenta_2", "cuenta_3"):
        assert profiles.exists(ref)  # siguen ahí al cerrar el navegador
        assert (profiles.root / ref / "Cookies").is_file()


# ---------------------------------------------------------------------------
# Flujo de inicio de sesión
# ---------------------------------------------------------------------------
def _login(profiles, tmp_path, world):
    from lot_bot.wallapop.browser import BrowserSessionAuthMethod

    service, launcher = make_service(profiles, world, tmp_path)
    auth = BrowserSessionAuthMethod(service, login_timeout=60)
    session = auth.start_login("cuenta_1")
    assert session.wait_for(session.WAITING) == session.WAITING
    return auth, session, launcher


def test_abrir_el_navegador_no_marca_la_cuenta_como_conectada(database, profiles, secret_box, tmp_path):
    from lot_bot.database.models import AccountStatus
    from lot_bot.wallapop.account_manager import AccountManager

    manager = AccountManager(database, secret_box)
    manager.add_account("Cuenta 1", internal_ref="cuenta_1")
    auth, session, launcher = _login(profiles, tmp_path, {"logged_in": True})
    # Navegador abierto, incluso con sesión iniciada: sin pulsar «Ya he iniciado
    # sesión» y sin comprobación, no se conecta nada.
    assert launcher.opened and not launcher.closed
    assert not auth.authenticate("cuenta_1").success
    assert manager.get_account("cuenta_1").status != AccountStatus.CONNECTED
    session.close()


def test_sesion_no_confirmada_queda_pendiente(database, profiles, secret_box, tmp_path):
    from lot_bot.database.models import AccountStatus
    from lot_bot.wallapop.account_manager import AccountManager
    from lot_bot.wallapop.browser.auth import UNCONFIRMED

    manager = AccountManager(database, secret_box)
    manager.add_account("Cuenta 1", internal_ref="cuenta_1")
    # Página privada sin redirección ni botón de acceso, pero sin ninguna
    # prueba de sesión: no se puede asegurar.
    auth, session, launcher = _login(profiles, tmp_path, {"logged_in": True, "no_proof": True})
    session.request_check()
    assert session.wait_for(session.UNKNOWN, session.VERIFIED) == session.UNKNOWN
    assert UNCONFIRMED in session.message
    assert not session.verified
    assert not manager.connect("cuenta_1", method=auth, session_check=session.check).success
    assert manager.get_account("cuenta_1").status != AccountStatus.CONNECTED
    assert not launcher.closed  # el navegador sigue abierto para intentarlo otra vez
    session.close()


def test_captcha_pausa_y_se_puede_continuar_despues(database, profiles, secret_box, tmp_path):
    from lot_bot.database.models import AccountStatus
    from lot_bot.wallapop.account_manager import AccountManager

    manager = AccountManager(database, secret_box)
    manager.add_account("Cuenta 1", internal_ref="cuenta_1")
    world = {"logged_in": True, "verification": True}
    auth, session, launcher = _login(profiles, tmp_path, world)
    session.request_check()
    assert session.wait_for(session.VERIFICATION, session.VERIFIED) == session.VERIFICATION
    assert "no la resuelve ni la salta" in session.message
    assert not session.verified
    assert not manager.connect("cuenta_1", method=auth, session_check=session.check).success
    assert manager.get_account("cuenta_1").status != AccountStatus.CONNECTED
    assert not launcher.closed  # sigue abierto para que el usuario la complete
    # LOT Bot no ha tocado la página durante la verificación.
    assert not [e for p in launcher.pages for e in p.log if e[0] in ("fill", "click", "option")]

    world["verification"] = False  # el usuario completa el CAPTCHA
    session.request_check()
    assert session.wait_for(session.VERIFIED) == session.VERIFIED
    session.close()
    assert manager.connect("cuenta_1", method=auth, session_check=session.check).success
    assert manager.get_account("cuenta_1").status == AccountStatus.CONNECTED


def test_redireccion_al_login_no_conecta(profiles, tmp_path):
    auth, session, _ = _login(profiles, tmp_path, {"logged_in": False})
    session.request_check()
    assert session.wait_for(session.NOT_LOGGED) == session.NOT_LOGGED
    assert "redirigido" in session.message or "iniciar sesión" in session.message
    session.close()
