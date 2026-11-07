"""Rutas de la aplicacion.

Resuelve donde viven los datos del usuario, los logs y los recursos empaquetados,
tanto ejecutando desde codigo fuente como desde un ejecutable de PyInstaller.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from lot_bot import APP_SLUG


def is_frozen() -> bool:
    """True cuando corremos dentro de un ejecutable empaquetado (PyInstaller)."""
    return getattr(sys, "frozen", False)


def resource_root() -> Path:
    """Carpeta del paquete `lot_bot` (contiene `resources/`).

    En el ejecutable, PyInstaller copia los datos en `sys._MEIPASS` respetando
    la ruta declarada en la receta (`lot_bot/resources`), asi que hay que
    apuntar a `_MEIPASS/lot_bot` y no a `_MEIPASS` a secas.
    """
    if is_frozen():
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return bundle / "lot_bot"
    return Path(__file__).resolve().parents[1]


def app_dir() -> Path:
    """Carpeta del programa: donde esta LOT-Bot.exe o la raiz del proyecto.

    Es donde el usuario puede dejar su `.env` y su carpeta `config/` para
    configurar el programa sin recompilar.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _default_user_root() -> Path:
    """Carpeta de datos del usuario, dependiente del sistema operativo."""
    override = os.environ.get("LOT_BOT_DATA_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "LOT Bot"
        return Path.home() / "AppData" / "Local" / "LOT Bot"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "LOT Bot"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_SLUG


@dataclass(frozen=True)
class AppPaths:
    """Conjunto de rutas usadas por la aplicacion."""

    root: Path
    database: Path
    logs: Path
    images: Path
    exports: Path
    config: Path
    resources: Path

    def ensure(self) -> AppPaths:
        for directory in (self.root, self.logs, self.images, self.exports, self.config):
            directory.mkdir(parents=True, exist_ok=True)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database.as_posix()}"


_paths: AppPaths | None = None


def build_paths(root: Path | None = None) -> AppPaths:
    base = Path(root).expanduser() if root else _default_user_root()
    return AppPaths(
        root=base,
        database=base / "lot_bot.db",
        logs=base / "logs",
        images=base / "images",
        exports=base / "exports",
        config=base / "config",
        resources=resource_root() / "resources",
    )


def get_paths() -> AppPaths:
    """Devuelve (y crea si hace falta) las rutas de la aplicacion."""
    global _paths
    if _paths is None:
        _paths = build_paths().ensure()
    return _paths


def set_paths(paths: AppPaths) -> AppPaths:
    """Sustituye las rutas activas. Usado por los tests y por el modo portable."""
    global _paths
    _paths = paths.ensure()
    return _paths
