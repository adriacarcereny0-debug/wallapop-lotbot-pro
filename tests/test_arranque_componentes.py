"""Si falta Playwright (p. ej. tras copiar una versión nueva), se instala
solo al arrancar; si ya está, no se hace nada."""

from __future__ import annotations

import builtins
import importlib
import sys


def _arranque():
    sys.modules.pop("run_lot_bot", None)
    return importlib.import_module("run_lot_bot")


def test_si_falta_playwright_se_instala_al_arrancar(monkeypatch):
    modulo = _arranque()
    llamadas = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: llamadas.append(cmd))
    original = builtins.__import__

    def sin_playwright(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise ImportError("no instalado")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", sin_playwright)
    modulo.ensure_browser_component()
    assert len(llamadas) == 1
    assert llamadas[0][1:4] == ["-m", "pip", "install"]
    assert any(str(a).endswith("requirements.txt") for a in llamadas[0])


def test_si_ya_esta_no_se_instala_nada(monkeypatch):
    modulo = _arranque()
    llamadas = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: llamadas.append(cmd))
    modulo.ensure_browser_component()
    assert llamadas == []
