"""Inicio de sesión en el Chrome/Edge NORMAL (sin automatización) y
configuración del navegador: sin --no-sandbox, sin invitado, perfil persistente,
sin bloquear recursos y con JavaScript activo.

La última prueba abre un navegador real contra una web local; solo se ejecuta
con LOT_BOT_REAL_BROWSER_TESTS=1 (necesita Chromium instalado).
"""

from __future__ import annotations

import http.server
import logging
import os
import socketserver
import threading
from pathlib import Path

import pytest

from lot_bot.wallapop.browser.auth import (
    STEP_2,
    UNCONFIRMED,
    VERIFICATION_MESSAGE,
    BrowserSessionAuthMethod,
)
from lot_bot.wallapop.browser.normal import (
    NormalBrowserLauncher,
    browser_version,
    build_normal_args,
)
from tests.test_browser_integration import make_service

FORBIDDEN = ("--no-sandbox", "--disable-setuid-sandbox", "--no-zygote", "--incognito", "--guest",
             "--inprivate", "--disable-javascript", "--blink-settings", "--enable-automation",
             "--remote-debugging")


# ---------------------------------------------------------------------------
# Argumentos del navegador normal
# ---------------------------------------------------------------------------
def test_navegador_normal_sin_flags_extra(tmp_path):
    exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    perfil = tmp_path / "browser_profiles" / "cuenta_1"
    args = build_normal_args(exe, perfil, "https://es.wallapop.com/")
    assert args == [
        exe,
        f"--user-data-dir={perfil}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://es.wallapop.com/",
    ]
    for flag in FORBIDDEN:
        assert not any(a.startswith(flag) for a in args), flag
    assert not any(a.startswith("--disable") for a in args)


def test_version_de_chrome_en_windows_sin_abrir_ventanas(tmp_path):
    app = tmp_path / "Application"
    (app / "130.0.6723.70").mkdir(parents=True)
    (app / "131.0.6778.86").mkdir()
    (app / "SetupMetrics").mkdir()
    exe = app / "chrome.exe"
    exe.write_bytes(b"")
    assert browser_version(str(exe), platform="win32") == "131.0.6778.86"


class FakeProcess:
    _next_pid = 1000

    def __init__(self, args, **kwargs):
        FakeProcess._next_pid += 1
        self.pid = FakeProcess._next_pid
        self.args = args
        self.alive = True
        # Como un navegador real, deja ficheros en su perfil.
        profile = next(a.split("=", 1)[1] for a in args if a.startswith("--user-data-dir="))
        (Path(profile) / "Local State").write_text("{}")

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False


class FakeNormalLauncher(NormalBrowserLauncher):
    """Como el real, pero el «navegador» es un proceso simulado."""

    def __init__(self, exe: Path):
        self.processes: list[FakeProcess] = []

        def popen(args, **kwargs):
            process = FakeProcess(args, **kwargs)
            self.processes.append(process)
            return process

        super().__init__(["chrome"], popen=popen)
        self._exe = exe

    def candidate(self):
        from lot_bot.wallapop.browser.driver import BrowserCandidate

        return BrowserCandidate("Google Chrome", executable=str(self._exe))


def test_el_lanzador_registra_navegador_version_ruta_perfil_y_argumentos(tmp_path, caplog):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    launcher = FakeNormalLauncher(exe)
    with caplog.at_level(logging.INFO):
        abierto = launcher.launch(tmp_path / "browser_profiles" / "cuenta_1", "https://es.wallapop.com/")
    texto = caplog.text
    assert "navegador=Google Chrome" in texto and f"ejecutable={exe}" in texto
    assert "perfil=" in texto and "cuenta_1" in texto and "version=" in texto
    assert "--no-first-run" in texto
    for sensible in ("password", "cookie", "token"):
        assert sensible not in texto.lower()
    assert abierto.running()


def test_error_al_abrir_el_navegador_normal_se_registra(tmp_path, caplog):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")

    def falla(args, **kwargs):
        raise OSError("acceso denegado")

    launcher = FakeNormalLauncher(exe)
    launcher._popen = falla
    with caplog.at_level(logging.ERROR), pytest.raises(OSError):
        launcher.launch(tmp_path / "p", "https://es.wallapop.com/")
    assert "acceso denegado" in caplog.text and f"ejecutable={exe}" in caplog.text


# ---------------------------------------------------------------------------
# Flujo de inicio de sesión con el navegador normal
# ---------------------------------------------------------------------------
@pytest.fixture()
def profiles(tmp_path):
    from lot_bot.wallapop.browser import BrowserProfileStore

    return BrowserProfileStore(tmp_path / "browser_profiles")


def _normal(profiles, tmp_path, world):
    service, playwright = make_service(profiles, world, tmp_path)
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    normal = FakeNormalLauncher(exe)
    service.normal_launcher = normal
    return service, playwright, normal


def test_pasos_y_comprobacion_real_tras_cerrar_el_navegador(database, profiles, secret_box, tmp_path):
    from lot_bot.database.models import AccountStatus
    from lot_bot.wallapop.account_manager import AccountManager

    world = {"logged_in": True}
    service, playwright, normal = _normal(profiles, tmp_path, world)
    manager = AccountManager(database, secret_box)
    manager.add_account("Cuenta 1", internal_ref="cuenta_1")
    auth = BrowserSessionAuthMethod(service, login_timeout=60)
    session = auth.start_login("cuenta_1")
    assert session.mode == "normal"
    assert session.wait_for(session.WAITING) == session.WAITING
    assert session.message == STEP_2 and "Paso 2/2" in session.message
    # Se ha abierto el navegador NORMAL con el perfil de la cuenta, no Playwright.
    assert len(normal.processes) == 1 and playwright.opened == []
    assert f"--user-data-dir={profiles.root / 'cuenta_1'}" in normal.processes[0].args

    # «Ya he iniciado sesión» con el navegador aún abierto: NO se conecta; se
    # pide cerrarlo para que Chrome guarde la sesión.
    session.request_check()
    assert session.wait_for(session.CLOSE_TO_SAVE) == session.CLOSE_TO_SAVE
    assert not session.verified
    assert manager.get_account("cuenta_1").status != AccountStatus.CONNECTED

    normal.processes[0].alive = False  # el usuario cierra la ventana
    assert session.wait_for(session.VERIFIED, session.UNKNOWN, session.NOT_LOGGED) == session.VERIFIED
    # La comprobación se ha hecho con Playwright sobre el MISMO perfil.
    assert playwright.opened == [profiles.root / "cuenta_1"]
    assert manager.connect("cuenta_1", method=auth, session_check=session.check).success
    assert manager.get_account("cuenta_1").status == AccountStatus.CONNECTED


def test_captcha_con_navegador_normal(profiles, tmp_path):
    world = {"logged_in": True, "verification": True}
    service, _, normal = _normal(profiles, tmp_path, world)
    session = BrowserSessionAuthMethod(service, login_timeout=60).start_login("cuenta_1")
    session.wait_for(session.WAITING)
    session.request_check()
    normal.processes[0].alive = False
    assert session.wait_for(session.VERIFICATION, session.VERIFIED) == session.VERIFICATION
    assert VERIFICATION_MESSAGE in session.message
    assert not session.verified
    # Se puede volver a abrir el navegador normal para completar la verificación.
    session.request_reopen()
    assert session.wait_for(session.WAITING) == session.WAITING
    assert len(normal.processes) == 2
    world["verification"] = False
    session.request_check()
    normal.processes[1].alive = False
    assert session.wait_for(session.VERIFIED) == session.VERIFIED


def test_sesion_no_confirmada_con_navegador_normal(profiles, tmp_path):
    service, _, normal = _normal(profiles, tmp_path, {"logged_in": True, "no_proof": True})
    session = BrowserSessionAuthMethod(service, login_timeout=60).start_login("cuenta_1")
    session.wait_for(session.WAITING)
    session.request_check()
    normal.processes[0].alive = False
    assert session.wait_for(session.UNKNOWN, session.VERIFIED) == session.UNKNOWN
    assert UNCONFIRMED in session.message and not session.verified


def test_cerrar_sin_pulsar_no_conecta(profiles, tmp_path):
    service, _, normal = _normal(profiles, tmp_path, {"logged_in": True})
    session = BrowserSessionAuthMethod(service, login_timeout=60).start_login("cuenta_1")
    session.wait_for(session.WAITING)
    normal.processes[0].alive = False
    assert session.wait_for(session.BROWSER_CLOSED) == session.BROWSER_CLOSED
    assert not session.verified
    session.close()


def test_cada_cuenta_reutiliza_siempre_su_perfil(profiles, tmp_path):
    service, _, normal = _normal(profiles, tmp_path, {"logged_in": False})
    auth = BrowserSessionAuthMethod(service, login_timeout=60)
    for ref in ("cuenta_1", "cuenta_2", "cuenta_1"):
        session = auth.start_login(ref)
        session.wait_for(session.WAITING)
        session.close()
    dirs = [next(a for a in p.args if a.startswith("--user-data-dir=")) for p in normal.processes]
    assert dirs[0] == dirs[2] != dirs[1]
    assert dirs[0].endswith("cuenta_1") and dirs[1].endswith("cuenta_2")


def test_sin_chrome_instalado_se_usa_el_navegador_integrado(profiles, tmp_path):
    service, playwright = make_service(profiles, {"logged_in": True}, tmp_path)
    service.normal_launcher = None
    session = BrowserSessionAuthMethod(service, login_timeout=60).start_login("cuenta_1")
    assert session.mode == "integrado"
    session.wait_for(session.WAITING)
    assert playwright.opened
    session.close()


def test_la_diagnosis_no_contiene_datos_sensibles():
    from lot_bot.wallapop.browser.driver import safe_url

    assert safe_url("https://es.wallapop.com/login?token=abc123#x") == "https://es.wallapop.com/login"


def test_ningun_codigo_bloquea_recursos():
    """Ni interceptores de peticiones ni bloqueo de JS, imágenes, cookies..."""
    raiz = Path(__file__).resolve().parents[1] / "lot_bot"
    for fichero in raiz.rglob("*.py"):
        texto = fichero.read_text(encoding="utf-8")
        for patron in (".route(", "route.abort", "java_script_enabled=False", "service_workers=\"block\"",
                       "offline=True", "add_init_script", "--blink-settings"):
            assert patron not in texto, (fichero.name, patron)


# ---------------------------------------------------------------------------
# Navegador REAL contra una web local (opcional)
# ---------------------------------------------------------------------------
PAGE = """<html><body>
<img id="img" src="/pixel.gif">
<iframe id="frame" src="/frame.html"></iframe>
<script>
  document.cookie = "prueba=1; SameSite=Lax";
  localStorage.setItem("lotbot", "ok");
  window.jsOk = true;
</script></body></html>"""


@pytest.mark.skipif(os.environ.get("LOT_BOT_REAL_BROWSER_TESTS") != "1", reason="navegador real opcional")
def test_navegador_real_carga_todo_con_javascript(tmp_path):
    from lot_bot.wallapop.browser.driver import PlaywrightLauncher, sandbox_enabled

    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(PAGE)
    (site / "frame.html").write_text("<p id='f'>iframe ok</p>")
    (site / "pixel.gif").write_bytes(
        bytes.fromhex("47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b")
    )

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(site), **k)

        def log_message(self, *a):
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        perfil = tmp_path / "browser_profiles" / "cuenta_1"
        with PlaywrightLauncher().open(perfil, visible=False, channels=["chrome", "msedge", "chromium"], locale="es-ES") as page:
            page.goto(f"http://127.0.0.1:{port}/index.html")
            page.wait(800)
            raw = page._page
            assert raw.evaluate("() => window.jsOk") is True  # JavaScript activo
            assert raw.evaluate("() => document.getElementById('img').naturalWidth") == 1
            assert raw.frame_locator("#frame").locator("#f").inner_text() == "iframe ok"
            assert raw.evaluate("() => localStorage.getItem('lotbot')") == "ok"
            assert "prueba=1" in raw.evaluate("() => document.cookie")
            diag = page.diagnostics()
            assert diag["javascript"] is True and diag["errores_red"] == []
            if sandbox_enabled():
                assert "--no-sandbox" not in _browser_cmdline(perfil)
        server.shutdown()
    # El perfil sigue en disco después de cerrar (persistente, no temporal).
    assert perfil.is_dir() and any(perfil.iterdir())


def _browser_cmdline(profile: Path) -> str:
    proc = Path("/proc")
    if not proc.is_dir():
        return ""
    for pid in proc.iterdir():
        try:
            cmd = (pid / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if str(profile) in cmd:
            return cmd
    return ""
