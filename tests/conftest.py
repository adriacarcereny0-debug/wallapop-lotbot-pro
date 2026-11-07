"""Configuracion comun de las pruebas.

Todas las pruebas usan una carpeta temporal y una base de datos propia: nunca
tocan los datos reales del usuario ni Wallapop.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from lot_bot.config.paths import build_paths, set_paths
from lot_bot.config.secrets import SecretBox, reset_secret_box
from lot_bot.config.settings import Settings
from lot_bot.database.engine import Database, set_database


@pytest.fixture()
def temp_paths(tmp_path: Path):
    """Redirige todas las rutas de la aplicacion a una carpeta temporal."""
    os.environ["LOT_BOT_DATA_DIR"] = str(tmp_path)
    paths = set_paths(build_paths(tmp_path))
    reset_secret_box()
    yield paths
    os.environ.pop("LOT_BOT_DATA_DIR", None)


@pytest.fixture()
def database(temp_paths) -> Database:
    db = Database("sqlite:///:memory:")
    db.create_all()
    set_database(db)
    yield db
    set_database(None)
    db.dispose()


@pytest.fixture()
def secret_box() -> SecretBox:
    return SecretBox(Fernet.generate_key())


@pytest.fixture()
def demo_settings() -> Settings:
    return Settings(
        LOT_BOT_DEMO_MODE=True,
        LOT_BOT_AI_PROVIDER="rules",
        LOT_BOT_LOG_LEVEL="WARNING",
    )


@pytest.fixture()
def mock_service(temp_paths):
    from lot_bot.wallapop.mock_service import MockWallapopService

    return MockWallapopService(state_file=Path(tempfile.mkdtemp()) / "demo.json")


@pytest.fixture()
def app(database, demo_settings, temp_paths):
    """Aplicacion completa en modo DEMO, sin planificador."""
    from lot_bot.bootstrap import create_application
    from lot_bot.publishing.queue import FakeClock

    application = create_application(
        settings=demo_settings, database=database, start_scheduler=False
    )
    # Reloj simulado: la cola respeta el intervalo de 60 s, pero sin esperar
    # de verdad. Solo se admite con el servicio DEMO.
    application.publish_queue.clock = FakeClock()
    yield application
    application.automations.shutdown()


@pytest.fixture()
def app_with_data(app):
    """Aplicacion DEMO con catalogo y anuncios sincronizados."""
    from lot_bot.bootstrap import seed_demo_catalog

    seed_demo_catalog(app)
    refs = [a.internal_ref for a in app.accounts.list_accounts()]
    app.listings.sync_all(refs)
    app.messages.sync_all(refs)
    app.save_business_settings(
        {
            "name": "Tienda de pruebas",
            "whatsapp": "600000000",
            "delivery": "El transporte y el montaje son gratuitos.",
            "precio_90": "230",
            "precio_135": "270",
            "precio_150": "290",
        }
    )
    return app
