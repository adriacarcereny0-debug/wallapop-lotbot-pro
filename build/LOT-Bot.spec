# -*- mode: python ; coding: utf-8 -*-
"""Receta de PyInstaller para generar LOT-Bot.exe.

Uso:
    pyinstaller build/LOT-Bot.spec --noconfirm --clean

El resultado es `dist/LOT-Bot/LOT-Bot.exe`: el cliente lo ejecuta sin instalar
Python ni ninguna herramienta de desarrollo.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

SPEC_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_DIR.parent

APP_NAME = "LOT-Bot"
ENTRY_POINT = str(PROJECT_ROOT / "run_lot_bot.py")
ICON = PROJECT_ROOT / "lot_bot" / "resources" / "lot_bot.ico"

# --- Datos que deben viajar dentro del ejecutable ---
datas = [
    (str(PROJECT_ROOT / "config" / "access_profile.example.yaml"), "config"),
    (str(PROJECT_ROOT / ".env.example"), "."),
]
resources = PROJECT_ROOT / "lot_bot" / "resources"
if resources.is_dir() and any(resources.iterdir()):
    datas.append((str(resources), "lot_bot/resources"))

# keyring descubre sus backends mediante «entry points», que viven en los
# metadatos del paquete. Sin copiarlos, en el .exe caeria siempre al backend
# nulo y la clave maestra iria a fichero en vez de al Administrador de
# credenciales de Windows.
for package in ("keyring",):
    try:
        datas += copy_metadata(package)
    except Exception:  # el paquete puede no estar instalado en desarrollo
        pass

# Playwright (integración por navegador): incluye su «driver». Los navegadores
# NO se empaquetan: se usa el Chrome o Edge ya instalado en Windows.
# Sin Playwright el .exe solo funcionaría en DEMO: se para y se avisa.
try:
    import playwright  # noqa: F401
except ImportError as exc:
    raise SystemExit(
        "Falta Playwright: el .exe no podría usar el navegador (se quedaría en DEMO). "
        "Instala las dependencias (pip install -r requirements.txt) y vuelve a generarlo."
    ) from exc
from PyInstaller.utils.hooks import collect_data_files

datas += collect_data_files("playwright")

docs = PROJECT_ROOT / "docs" / "cliente"
if docs.is_dir():
    datas.append((str(docs), "docs/cliente"))

# --- Modulos que PyInstaller no siempre detecta solo ---
hiddenimports = [
    "lot_bot",
    # Backends del llavero del sistema (Windows Credential Manager)
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.macOS",
    "keyring.backends.fail",
    # keyring usa win32ctypes para hablar con el Administrador de credenciales
    "win32ctypes",
    "win32ctypes.pywin32",
    "win32ctypes.pywin32.win32cred",
    # SQLAlchemy carga su dialecto de forma dinamica
    "sqlalchemy.dialects.sqlite",
    # APScheduler resuelve sus planificadores por nombre
    "apscheduler.schedulers.background",
    "apscheduler.triggers.interval",
    "apscheduler.executors.pool",
    # Logo e iconos del menú (SVG)
    "PySide6.QtSvg",
]
hiddenimports += collect_submodules("lot_bot")
try:
    hiddenimports += collect_submodules("playwright")
except Exception:
    pass

# --- Lo que NO hace falta empaquetar (reduce mucho el tamano) ---
excludes = [
    "tkinter",
    "test",
    "unittest",
    "pytest",
    "pydoc_data",
    "matplotlib",
    "numpy",
    "pandas",
    "IPython",
    "notebook",
    # Modulos de Qt que LOT Bot no usa
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.QtMultimedia",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
]

block_cipher = None

a = Analysis(
    [ENTRY_POINT],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ---------------------------------------------------------------------------
# Adelgazar el paquete
# ---------------------------------------------------------------------------
# Qt viaja con traducciones y complementos de todos los idiomas y tecnologias.
# LOT Bot solo necesita espanol e ingles y un punado de complementos, asi que
# el resto se descarta: el ejecutable baja de tamano sin perder funcionalidad.
KEEP_TRANSLATION_PREFIXES = ("qt_es", "qtbase_es", "qt_en", "qtbase_en")

#: Carpetas de complementos de Qt que SI se necesitan.
KEEP_PLUGIN_DIRS = (
    "platforms",        # ventana nativa (windows, xcb, cocoa)
    "styles",           # estilo nativo
    "imageformats",     # JPEG, PNG, WEBP en la interfaz
    "iconengines",
    "platformthemes",
    "tls",              # HTTPS de QtNetwork
)


def _keep(entry) -> bool:
    destination = entry[0].replace("\\", "/")
    if "/Qt/translations/" in destination or destination.startswith("PySide6/translations/"):
        return any(Path(destination).name.startswith(p) for p in KEEP_TRANSLATION_PREFIXES)
    if "/Qt/plugins/" in destination:
        parts = destination.split("/Qt/plugins/")[1].split("/")
        return parts[0] in KEEP_PLUGIN_DIRS
    # Documentacion y ejemplos de Qt que a veces se cuelan
    if "/Qt/qml/" in destination or "/Qt/doc/" in destination:
        return False
    return True


a.datas = [entry for entry in a.datas if _keep(entry)]
a.binaries = [entry for entry in a.binaries if _keep(entry)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # console=False -> aplicacion de escritorio, sin ventana negra de terminal
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.is_file() else None,
    version=str(SPEC_DIR / "version_info.txt")
    if (SPEC_DIR / "version_info.txt").is_file()
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
