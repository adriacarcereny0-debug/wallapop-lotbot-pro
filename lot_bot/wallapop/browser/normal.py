"""Navegador NORMAL para que el usuario inicie sesión a mano.

Para el inicio de sesión (y para «Abrir cuenta») LOT Bot no usa Playwright:
lanza el Chrome o Edge instalado como un proceso normal, con la carpeta de
perfil propia de la cuenta. Es el mismo navegador que el usuario usa a diario,
sin automatización, sin argumentos de Playwright y sin tocar nada de la
página: reCAPTCHA y las verificaciones de Wallapop funcionan como en su Chrome.

Argumentos que se pasan (y NINGUNO más):
  --user-data-dir=<perfil de la cuenta>   perfil propio y persistente
  --no-first-run                          sin asistente de bienvenida
  --no-default-browser-check              sin preguntar si es el predeterminado
  <URL de Wallapop>

Nunca: --no-sandbox, --incognito, --guest, --disable-*, ni nada que oculte la
automatización (aquí no hay automatización: el usuario maneja el navegador).

La comprobación de la sesión y la publicación se hacen DESPUÉS, con Playwright,
sobre ese mismo perfil (ver `service.py`).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from lot_bot.wallapop.browser.driver import BrowserCandidate, find_browsers

logger = logging.getLogger(__name__)

#: Ficheros de bloqueo que Chrome deja en el perfil mientras está abierto.
LOCK_FILES = ("lockfile", "SingletonLock", "SingletonSocket", "SingletonCookie")


def build_normal_args(executable: str, profile_dir: Path, url: str) -> list[str]:
    """Línea de comandos del navegador normal. Pura: se prueba sin abrir nada."""
    return [
        executable,
        f"--user-data-dir={Path(profile_dir)}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]


def browser_version(executable: str, *, platform: str | None = None) -> str:
    """Versión del navegador sin abrir ventanas.

    En Windows, Chrome y Edge guardan cada versión en una carpeta junto al
    ejecutable (…\\Application\\131.0.6778.86\\); ejecutar «--version» abriría
    una ventana. En Linux/macOS se pregunta al propio ejecutable.
    """
    platform = platform or sys.platform
    path = Path(executable)
    if platform.startswith("win"):
        versions = [
            p.name
            for p in path.parent.glob("*")
            if p.is_dir() and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", p.name)
        ]
        return max(versions, key=lambda v: [int(x) for x in v.split(".")]) if versions else "desconocida"
    try:
        out = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=10, check=False
        ).stdout.strip()
        return out or "desconocida"
    except (OSError, subprocess.SubprocessError):
        return "desconocida"


def profile_in_use(profile_dir: Path) -> bool:
    from lot_bot.wallapop.browser.profiles import profile_locked

    return profile_locked(profile_dir)


@dataclass(slots=True)
class NormalBrowserProcess:
    """Un navegador normal abierto con el perfil de una cuenta."""

    process: subprocess.Popen | None
    candidate: BrowserCandidate
    profile_dir: Path
    args: list[str] = field(default_factory=list)
    version: str = "desconocida"

    def running(self) -> bool:
        if self.process is not None and self.process.poll() is None:
            return True
        # El proceso puede haber delegado en otro del mismo perfil: mientras el
        # perfil esté bloqueado, el navegador sigue abierto.
        return profile_in_use(self.profile_dir)

    def wait_closed(self, timeout: float = 15.0) -> bool:
        """Espera a que el navegador termine de cerrarse y libere el perfil."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running():
                return True
            time.sleep(0.3)
        return not self.running()

    def describe(self) -> str:
        return (
            f"navegador={self.candidate.name}; version={self.version}; "
            f"ejecutable={self.candidate.executable}; perfil={self.profile_dir}; "
            f"args={self.args[1:-1]}; pid={self.process.pid if self.process else '-'}"
        )


class NormalBrowserLauncher:
    """Abre Chrome/Edge instalado, sin automatización, con un perfil propio."""

    def __init__(self, channels: list[str] | None = None, popen=subprocess.Popen) -> None:
        self._channels = channels or ["chrome", "msedge"]
        self._popen = popen

    def candidate_for(self, profile_dir: Path) -> BrowserCandidate | None:
        """Si el perfil ya se creó con un navegador, se usa ese mismo."""
        from lot_bot.wallapop.browser.profiles import read_browser_marker

        marker = read_browser_marker(profile_dir)
        if marker:
            for candidate in find_browsers(self._channels):
                if (
                    candidate.name == marker
                    and candidate.executable
                    and Path(candidate.executable).is_file()
                ):
                    return candidate
        return self.candidate()

    def candidate(self) -> BrowserCandidate | None:
        """Primer navegador con ruta conocida (necesaria para lanzarlo así)."""
        for candidate in find_browsers(self._channels):
            if candidate.executable and Path(candidate.executable).is_file():
                return candidate
        return None

    def available(self) -> bool:
        return self.candidate() is not None

    def launch(self, profile_dir: Path, url: str) -> NormalBrowserProcess:
        candidate = self.candidate_for(profile_dir)
        if candidate is None:
            raise FileNotFoundError("No se ha encontrado Chrome ni Edge instalados.")
        profile_dir = Path(profile_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = build_normal_args(candidate.executable, profile_dir, url)
        version = browser_version(candidate.executable)
        try:
            kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if os.name == "nt":  # sin ventana de consola adicional
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = self._popen(args, **kwargs)
        except OSError as exc:
            logger.error(
                "No se ha podido abrir el navegador normal. navegador=%s; version=%s; "
                "ejecutable=%s; perfil=%s; args=%s; error=%s",
                candidate.name,
                version,
                candidate.executable,
                profile_dir,
                args[1:-1],
                exc,
            )
            raise
        from lot_bot.wallapop.browser.profiles import write_browser_marker

        write_browser_marker(profile_dir, candidate.name)
        opened = NormalBrowserProcess(process, candidate, profile_dir, args, version)
        logger.info("Navegador normal abierto para iniciar sesión: %s", opened.describe())
        return opened
