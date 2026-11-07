"""El repositorio debe contener todo el código fuente que importa la aplicación.

Protege contra reglas de .gitignore demasiado amplias (p. ej. `logs/` ignoraba el
paquete `lot_bot/logs/`, y un clon limpio fallaba con ModuleNotFoundError).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="sin git")
def test_ningun_fichero_de_codigo_esta_ignorado_por_git():
    sources = [
        str(p.relative_to(ROOT))
        for folder in ("lot_bot", "tests", "build", "scripts")
        for p in (ROOT / folder).rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix in {".py", ".yaml", ".ps1", ".sh", ".spec", ".qss", ".svg", ".ico", ".png"}
    ]
    sources += [p.name for p in ROOT.glob("*.py")]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        input="\n".join(sources),
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.stdout.strip() == "", (
        f"Ficheros de código ignorados por .gitignore:\n{result.stdout}"
    )


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="sin git")
@pytest.mark.parametrize(
    "ruta",
    [
        "browser_profiles/acc-1/Default/Cookies",
        "lot_bot_data/browser_profiles/acc-1/Cookies",
        "data/browser_profiles/acc-2/Local State",
        "config/wallapop_browser.local.yaml",
        "config/access_profile.local.yaml",
        "storage_state.json",
        "sesion.har",
        ".env",
        "master.key",
        "lot_bot.db",
        "logs/navegador/captura.png",
    ],
)
def test_sesiones_cookies_y_claves_nunca_se_suben(ruta):
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", ruta], cwd=ROOT, capture_output=True
    )
    assert result.returncode == 0, f"{ruta} NO está ignorado por .gitignore"


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="sin git")
def test_no_hay_sesiones_ni_bases_de_datos_versionadas():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True
    ).stdout.splitlines()
    prohibidos = ("browser_profiles/", "Cookies", "Login Data", ".har", ".db", "master.key", ".env")
    culpables = [
        f for f in tracked if any(p in f for p in prohibidos) and not f.endswith(".env.example")
    ]
    assert culpables == []


def test_los_perfiles_de_navegador_viven_fuera_del_proyecto(monkeypatch):
    """Sin LOT_BOT_DATA_DIR, la carpeta de datos es la del usuario, no el repo."""
    from lot_bot.config import paths

    monkeypatch.delenv("LOT_BOT_DATA_DIR", raising=False)
    root = paths._default_user_root().resolve()
    assert ROOT not in [root, *root.parents]
