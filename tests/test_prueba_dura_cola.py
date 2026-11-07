"""Prueba DURA de la cola: 50 anuncios seguidos con un Chromium REAL contra la
página local que imita Wallapop (no se conecta a Wallapop).

Dos cuentas, fotos que no se repiten, máximo de anuncios por cuenta (el bot
tiene que borrar los más antiguos por el camino) y el formulario con las
pegas del Wallapop real: Color y Material con botón «Seleccionar», el valor
fuera del botón, la ubicación en una caja con buscador... La cola tiene que
terminar SOLA: sin pausas y sin tener que pulsar «Continuar».

Tarda varios minutos, por eso solo se ejecuta con LOT_BOT_PRUEBA_DURA=1:

    LOT_BOT_PRUEBA_DURA=1 python -m pytest tests/test_prueba_dura_cola.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

CHROMIUM = os.environ.get("LOT_BOT_BROWSER_EXECUTABLE") or next(
    (str(p) for p in sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux*/chrome"))), ""
)
pytestmark = [
    pytest.mark.skipif(not CHROMIUM, reason="No hay Chromium para la prueba real."),
    pytest.mark.skipif(
        os.environ.get("LOT_BOT_PRUEBA_DURA") != "1",
        reason="Prueba larga: se activa con LOT_BOT_PRUEBA_DURA=1.",
    ),
]

ANUNCIOS = 50
MAXIMO_POR_CUENTA = 20
#: Las pegas del Wallapop real vistas en tus vídeos y registros.
VARIANTE = "?sinurl=1&ubic=boton&ubicicono=1&seleccionar=1&sinestado=1&valorfuera=1"


def _segunda_cuenta(app):
    from lot_bot.wallapop.auth.base import AuthCredential, AuthKind

    cuenta = app.accounts.add_account("Cuenta de prueba 2")
    app.wallapop.profiles.profile_dir(cuenta.internal_ref).joinpath("Local State").write_text("{}")
    app.accounts.store_credential(
        cuenta.internal_ref,
        AuthCredential(kind=AuthKind.BROWSER_SESSION, metadata={"account_ref": cuenta.internal_ref}),
    )
    return cuenta.internal_ref


def _fotos(app, carpeta: Path, cuantas: int) -> None:
    from PIL import Image

    carpeta.mkdir(parents=True, exist_ok=True)
    nuevas = []
    for n in range(cuantas):
        foto = carpeta / f"canape-{n:02d}.jpg"
        Image.new("RGB", (1000, 750), (40 + n * 4, 120, 200 - n * 3)).save(foto)
        nuevas.append({
            "path": str(foto), "file_format": "JPEG", "width": 1000, "height": 750,
            "content_hash": hashlib.sha256(foto.read_bytes()).hexdigest(),
        })
    app.master_ads.add_images(None, nuevas)


def test_cola_publica_50_anuncios_seguidos_sin_pararse(tmp_path, monkeypatch):
    monkeypatch.setenv("LOT_BOT_BROWSER_EXECUTABLE", CHROMIUM)
    monkeypatch.setenv("LOT_BOT_BROWSER_SANDBOX", "0")
    monkeypatch.setenv("LOT_BOT_DATA_DIR", str(tmp_path / "datos"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import prueba_publicar_canape as guion

    from lot_bot.bootstrap import create_application
    from lot_bot.config.settings import reload_settings
    from lot_bot.publishing.listings import ListingFilter
    from lot_bot.publishing.queue import FakeClock

    reload_settings()
    app = create_application(start_scheduler=False)
    app.set_integration_mode("navegador")
    web, alias = guion._prepare_simulated(app)
    try:
        app.wallapop.site.visible = os.environ.get("LOT_BOT_VISIBLE_TESTS") == "1"
        app.wallapop.site.success_timeout_ms = 15000
        app.wallapop.site.urls["subir"] += VARIANTE
        cuentas = [app.accounts.resolve_ref(alias), _segunda_cuenta(app)]
        _fotos(app, tmp_path / "fotos", ANUNCIOS + 5)

        cola = app.publish_queue
        # El intervalo mínimo (60 s) se respeta, pero con un reloj simulado
        # para no tardar 50 minutos: el bot y el navegador son los reales.
        cola.clock = FakeClock()
        # Ese reloj solo se admite en modo DEMO (protege el Wallapop real);
        # aquí la «web» es la página local de pruebas.
        monkeypatch.setattr(cola, "_check_clock", lambda: None)
        cola.save_settings(
            generate_images=False,
            rotate_photos=True,
            max_listings_per_account=MAXIMO_POR_CUENTA,
        )
        pausas: list[str] = []
        pausar = cola.pause
        monkeypatch.setattr(cola, "pause", lambda job, reason="": (pausas.append(reason), pausar(job, reason)))

        trabajo = cola.enqueue_master(None, cuentas, copies=ANUNCIOS, generate_images=False)
        cola.run_until_idle(max_steps=ANUNCIOS * 4)
        progreso = cola.progress(trabajo)

        # 1. Terminada sola: nada de pausas ni «Continuar».
        assert pausas == [], pausas
        assert progreso.status == "completed", (progreso.status, progreso.tasks[:3])
        assert progreso.published == ANUNCIOS and progreso.failed == 0, progreso.tasks
        assert len(web.published) == ANUNCIOS

        # 2. Cada anuncio, completo y bien (lo que fallaba en tus pruebas).
        for n, anuncio in enumerate(web.published, 1):
            assert anuncio["color"] and anuncio["material"] == "Madera", (n, anuncio)
            assert anuncio["fotos"] == 1, (n, anuncio)
            assert anuncio["envio"] is False, (n, anuncio)

        # 3. Fotos: 50 distintas, cada una en un solo anuncio.
        usadas = cola.used_photos()
        assert len(usadas) == ANUNCIOS, len(usadas)
        assert all(len(v["cuentas"]) == 1 for v in usadas.values())
        for ref in cuentas:
            assert sum(ref in v["cuentas"] for v in usadas.values()) == ANUNCIOS // 2

        # 4. Máximo por cuenta: se han borrado los más antiguos por el camino.
        for ref in cuentas:
            activos = [
                a for a in app.listings.search(ListingFilter(account_ref=ref)) if a.status == "active"
            ]
            assert len(activos) <= MAXIMO_POR_CUENTA, (ref, len(activos))
        assert len(web.deleted) >= ANUNCIOS - 2 * MAXIMO_POR_CUENTA, len(web.deleted)
    finally:
        app.shutdown()
        web.__exit__(None, None, None)
