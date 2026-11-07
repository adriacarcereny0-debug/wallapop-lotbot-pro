#!/usr/bin/env python3
"""Arranque de LOT Bot durante el desarrollo.

Uso:  python run_lot_bot.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def ensure_browser_component() -> None:
    """Si falta Playwright (p. ej. tras copiar una versión nueva), se instala
    antes de abrir: sin él, el bot no puede usar el navegador y se queda en
    modo DEMO."""
    try:
        import playwright.sync_api  # noqa: F401

        return
    except ImportError:
        pass
    import subprocess

    requirements = ROOT / "requirements.txt"
    cmd = [sys.executable, "-m", "pip", "install", "--quiet"]
    cmd += ["-r", str(requirements)] if requirements.is_file() else ["playwright>=1.45,<2.0"]
    try:
        subprocess.run(cmd, check=False, timeout=900)
    except Exception:
        pass  # sin conexión: se abre igual (en DEMO) y se reintenta la próxima vez


ensure_browser_component()

from lot_bot.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
