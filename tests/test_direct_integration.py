"""Integración directa sin navegador: conexión, cola persistente y motor.

El «Wallapop» de estas pruebas es un servidor simulado (httpx.MockTransport)
declarado con un perfil de acceso de prueba: nunca se toca Wallapop real.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from PIL import Image

from lot_bot.database.models import AdQueueStatus
from lot_bot.integration import AdInput, AuthState, EngineState
from lot_bot.publishing.queue import FakeClock

TOKEN = "cred-prueba-123"


# ---------------------------------------------------------------------------
# Servidor simulado
# ---------------------------------------------------------------------------
class FakeWallapop:
    """Imita un servicio autorizado: /me, /items, /images."""

    def __init__(self) -> None:
        self.valid = {f"Bearer {TOKEN}"}
        self.items: list[dict] = []
        self.images: list[str] = []
        #: Fallos a inyectar en la siguiente creación: "timeout", "connect", 500, 429, 422, 401
        self.fail_create: list = []
        self.requests: list[httpx.Request] = []
        self.created_but_lost = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") not in self.valid:
            return httpx.Response(401, json={"message": "invalid token"})
        path = request.url.path
        if path == "/me":
            return httpx.Response(200, json={"id": "u1", "login": "tienda-prueba"})
        if path == "/images" and request.method == "POST":
            url = f"https://img.prueba/{len(self.images) + 1}.jpg"
            self.images.append(url)
            return httpx.Response(200, json={"url": url})
        if path == "/items" and request.method == "GET":
            return httpx.Response(200, json={"items": self.items})
        if path == "/items" and request.method == "POST":
            if self.fail_create:
                failure = self.fail_create.pop(0)
                if failure == "connect":
                    raise httpx.ConnectError("sin red", request=request)
                if failure == "timeout":
                    # Wallapop lo crea, pero la respuesta no llega.
                    self._create(request)
                    raise httpx.ReadTimeout("respuesta perdida", request=request)
                if failure == 429:
                    return httpx.Response(429, headers={"Retry-After": "120"}, json={"message": "slow"})
                return httpx.Response(failure, json={"message": f"error {failure}"})
            return httpx.Response(201, json=self._create(request))
        return httpx.Response(404, json={"message": "no existe"})

    def _create(self, request: httpx.Request) -> dict:
        body = json.loads(request.content or b"{}")
        item = {
            "id": f"it-{len(self.items) + 1}",
            "title": body.get("title"),
            "price": body.get("price"),
            "images": body.get("images", []),
            "url": f"https://es.wallapop.com/item/it-{len(self.items) + 1}",
        }
        self.items.append(item)
        return item


PROFILE = {
    "meta": {"provider": "provisional", "authorized_by": "Prueba"},
    "auth": {
        "method": "delegated_credential",
        "delegated_credential": {
            "header_name": "Authorization",
            "header_format": "Bearer {credential}",
            "portal_url": "https://integraciones.prueba/credencial",
            "instructions": "Pide la credencial a tu gestor de integración.",
        },
    },
    "api": {"base_url": "https://api.prueba.local"},
    "limits": {"max_images_per_item": 3},
    "operations": {
        "account_profile": {"method": "GET", "path": "/me",
                            "response": {"fields": {"user_id": "id", "login": "login"}}},
        "list_items": {"method": "GET", "path": "/items",
                       "response": {"collection_path": "items",
                                    "fields": {"item_id": "id", "title": "title", "price": "price",
                                               "url": "url"}}},
        "create_item": {"method": "POST", "path": "/items",
                        "response": {"fields": {"item_id": "id", "url": "url"}}},
        "upload_image": {"method": "POST", "path": "/images", "response": {"fields": {"url": "url"}}},
    },
}


def _write_profile(tmp_path: Path, profile: dict, name: str = "access_profile.local.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    return path


def _attach(app, server: FakeWallapop) -> None:
    service = app.backend.service
    service._client = httpx.Client(transport=httpx.MockTransport(server.handler))
    service.allow_test_clock = True


@pytest.fixture()
def direct(database, temp_paths, tmp_path):
    """Aplicación en conexión directa (credencial provisional) contra el simulado."""
    from lot_bot.bootstrap import create_application
    from lot_bot.config.settings import Settings

    profile_path = _write_profile(tmp_path, PROFILE)
    settings = Settings(
        LOT_BOT_DEMO_MODE=False,
        LOT_BOT_AI_PROVIDER="rules",
        LOT_BOT_LOG_LEVEL="WARNING",
        WALLAPOP_ACCESS_PROFILE=str(profile_path),
        LOT_BOT_WALLAPOP_INTEGRATION="perfil",
    )
    app = create_application(settings=settings, database=database, start_scheduler=False)
    server = FakeWallapop()
    _attach(app, server)
    app.direct.engine.clock = FakeClock()
    app.publish_queue.clock = FakeClock()
    app.server = server
    yield app
    app.automations.shutdown()


def _connected_account(app, alias="Tienda 1"):
    info = app.auth_manager.add_account(alias)
    result = app.auth_manager.submit_credential(info.internal_ref, TOKEN)
    assert result.ok, result.message
    return info.internal_ref


def _ad(ref, n=1, **extra):
    data = dict(account_ref=ref, title=f"Canapé abatible {n}", description="Nuevo, con garantía.",
                price=200 + n)
    data.update(extra)
    return AdInput(**data)


def _photo(tmp_path: Path, name: str, color=(200, 10, 10)) -> str:
    path = tmp_path / name
    Image.new("RGB", (900, 700), color).save(path, "JPEG")
    return str(path)


# ---------------------------------------------------------------------------
# Arquitectura
# ---------------------------------------------------------------------------
def test_los_seis_modulos_estan_separados_y_ensamblados(app):
    from lot_bot.integration import (
        AuthenticationManager,
        DatabaseManager,
        ImageManager,
        PublicationEngine,
        QueueManager,
        WallapopClient,
    )

    assert isinstance(app.auth_manager, AuthenticationManager)
    assert isinstance(app.wallapop_client, WallapopClient)
    assert isinstance(app.publication_engine, PublicationEngine)
    assert isinstance(app.queue_manager, QueueManager)
    assert isinstance(app.image_manager, ImageManager)
    assert isinstance(app.database_manager, DatabaseManager)


def test_el_motor_directo_no_importa_el_navegador():
    import lot_bot.integration as paquete

    carpeta = Path(paquete.__file__).parent
    for fichero in carpeta.glob("*.py"):
        texto = fichero.read_text(encoding="utf-8")
        assert "playwright" not in texto.lower(), fichero.name
        assert "wallapop.browser" not in texto, fichero.name


def test_conexion_directa_usa_el_proveedor_provisional(direct):
    assert direct.backend.provider == "perfil"
    assert direct.wallapop_client.is_direct
    assert not direct.wallapop_client.uses_browser
    assert direct.backend.label == "WALLAPOP REAL"
    proc = direct.auth_manager.procedure()
    assert proc.needs_credential
    assert proc.portal_url == "https://integraciones.prueba/credencial"
    assert proc.ready


def test_la_conexion_directa_solo_admite_token(database, temp_paths, tmp_path):
    """En «Conectar Wallapop» solo existe la credencial (token)."""
    from lot_bot.bootstrap import create_application
    from lot_bot.config.settings import Settings

    oauth = dict(PROFILE, auth={"method": "oauth", "oauth": {
        "authorize_url": "https://auth.prueba/a", "token_url": "https://auth.prueba/t"}})
    path = _write_profile(tmp_path, oauth)
    settings = Settings(LOT_BOT_DEMO_MODE=False, LOT_BOT_AI_PROVIDER="rules",
                        LOT_BOT_LOG_LEVEL="WARNING", WALLAPOP_ACCESS_PROFILE=str(path),
                        LOT_BOT_WALLAPOP_INTEGRATION="perfil")
    app = create_application(settings=settings, database=database, start_scheduler=False)
    try:
        assert app.demo_mode
        assert any("delegated_credential" in m for m in app.missing_access_data)
    finally:
        app.automations.shutdown()


def test_no_hay_opcion_de_api_oficial():
    from lot_bot.wallapop.factory import INTEGRATION_MODES

    assert "oficial" not in INTEGRATION_MODES
    raiz = Path(__file__).resolve().parents[1]
    assert not (raiz / "config" / "official_api.example.yaml").exists()


# ---------------------------------------------------------------------------
# Conectar Wallapop
# ---------------------------------------------------------------------------
def test_conectar_validar_comprobar_y_desconectar(direct):
    auth = direct.auth_manager
    info = auth.add_account("Tienda Centro")
    ref = info.internal_ref
    assert auth.status(ref).auth_state is AuthState.NOT_CONNECTED

    start = auth.start_connection(ref)
    assert start.ok and start.awaiting_credential
    assert start.open_url == "https://integraciones.prueba/credencial"

    result = auth.submit_credential(ref, TOKEN)
    assert result.ok, result.message
    status = auth.status(ref)
    assert status.auth_state is AuthState.CONNECTED
    assert status.login == "tienda-prueba"
    assert status.last_connected_at and status.last_validated_at

    assert auth.check(ref).ok
    assert auth.status(ref).last_check_ok is True

    off = auth.disconnect(ref)
    assert off.ok
    assert auth.status(ref).auth_state is AuthState.NOT_CONNECTED
    eventos = [e["codigo"] for e in auth.history(ref)]
    for esperado in ("connect_started", "connected", "validated", "check_ok", "disconnected"):
        assert esperado in eventos


def test_credencial_rechazada_no_se_guarda(direct):
    auth = direct.auth_manager
    ref = auth.add_account("Tienda").internal_ref
    result = auth.submit_credential(ref, "credencial-falsa")
    assert not result.ok
    assert "rechazado" in result.message
    assert not direct.accounts.has_credential(ref)
    assert auth.status(ref).auth_state is AuthState.ERROR


def test_credencial_vacia_o_con_saltos_de_linea(direct):
    auth = direct.auth_manager
    ref = auth.add_account("Tienda").internal_ref
    assert not auth.submit_credential(ref, "   ").ok
    assert not auth.submit_credential(ref, "abc\ndef").ok


def test_la_credencial_no_aparece_en_historial_ni_estado(direct):
    ref = _connected_account(direct)
    status = direct.auth_manager.status(ref)
    assert TOKEN not in repr(status)
    assert all(TOKEN not in (e["detalle"] or "") for e in direct.auth_manager.history())


def test_renovar_credencial_manual_pide_reconectar(direct):
    ref = _connected_account(direct)
    result = direct.auth_manager.renew(ref)
    assert not result.ok
    assert "Reconectar" in result.message
    # La credencial actual sigue funcionando.
    assert direct.auth_manager.status(ref).is_usable


def test_comprobar_detecta_credencial_revocada(direct):
    ref = _connected_account(direct)
    direct.server.valid.clear()  # Wallapop la revoca
    result = direct.auth_manager.check(ref)
    assert not result.ok
    assert direct.auth_manager.status(ref).auth_state is AuthState.NEEDS_RECONNECT


# ---------------------------------------------------------------------------
# Cola
# ---------------------------------------------------------------------------
def test_duplicados_en_cola_y_en_la_misma_importacion(direct):
    ref = _connected_account(direct)
    queue = direct.queue_manager
    first = queue.add(_ad(ref, 1))
    assert len(first.added) == 1
    again = queue.add(_ad(ref, 1))
    assert not again.added and again.duplicates
    batch = queue.add_many([_ad(ref, 2), _ad(ref, 2)])
    assert len(batch.added) == 1 and len(batch.duplicates) == 1
    assert queue.add(_ad(ref, 1), allow_duplicates=True).added


def test_validacion_al_encolar(direct):
    ref = _connected_account(direct)
    result = direct.queue_manager.add_many([
        AdInput(account_ref=ref, title="", description="x", price=10),
        AdInput(account_ref=ref, title="Mesa", description="", price=10),
        AdInput(account_ref=ref, title="Mesa", description="Bonita", price=0),
        AdInput(account_ref="no-existe", title="Mesa", description="Bonita", price=10),
        AdInput(account_ref=ref, title="Mesa", description="Bonita", price=10,
                image_paths=["/no/existe.jpg"]),
    ])
    assert not result.added
    assert len(result.invalid) == 5


def test_importar_csv_cientos_de_anuncios(direct, tmp_path):
    ref = _connected_account(direct, "Tienda CSV")
    foto = _photo(tmp_path, "sofa.jpg")
    lines = ["titulo;descripcion;precio;categoria;fotos"]
    for n in range(300):
        lines.append(f"Sofá modelo {n};Sofá en perfecto estado {n};{100 + n},50;Hogar;sofa.jpg")
    lines.append("Sin precio;Descripción;abc;Hogar;")
    csv_path = tmp_path / "anuncios.csv"
    csv_path.write_text("\n".join(lines), encoding="utf-8")
    result = direct.queue_manager.import_csv(csv_path, default_account=ref)
    assert len(result.added) == 300
    assert len(result.invalid) == 1
    item = direct.queue_manager.get(result.added[0])
    assert item["precio"] == 100.5
    assert item["fotos"] == [foto]
    assert direct.queue_manager.counts()["pending"] == 300


# ---------------------------------------------------------------------------
# Motor de publicación
# ---------------------------------------------------------------------------
def test_publica_en_orden_con_fotos_e_intervalo(direct, tmp_path):
    ref = _connected_account(direct)
    fotos = [_photo(tmp_path, "a.jpg"), _photo(tmp_path, "b.jpg", (0, 0, 200))]
    queue = direct.queue_manager
    queue.add_many([_ad(ref, n, image_paths=fotos, category="Hogar") for n in range(1, 6)])
    engine = direct.publication_engine
    engine.start()
    engine.run_until_idle()

    assert queue.counts()["published"] == 5
    assert [i["title"] for i in direct.server.items] == [f"Canapé abatible {n}" for n in range(1, 6)]
    assert direct.server.items[0]["images"] == ["https://img.prueba/1.jpg", "https://img.prueba/2.jpg"]
    # 60 s mínimos entre publicaciones (reloj simulado).
    assert sum(engine.clock.slept) >= 4 * 60
    # Quedan registrados en «Anuncios».
    listados = direct.listings.search()
    assert {v.title for v in listados} >= {f"Canapé abatible {n}" for n in range(1, 6)}


def test_fotos_repetidas_y_maximo_por_anuncio(direct, tmp_path):
    ref = _connected_account(direct)
    a = _photo(tmp_path, "a.jpg")
    copia = tmp_path / "copia.jpg"
    copia.write_bytes(Path(a).read_bytes())
    otras = [_photo(tmp_path, f"f{n}.jpg", (n * 40, 10, 10)) for n in range(1, 5)]
    direct.queue_manager.add(_ad(ref, image_paths=[a, str(copia), *otras]))
    direct.publication_engine.start()
    direct.publication_engine.run_until_idle()
    assert len(direct.server.items[0]["images"]) == 3  # limits.max_images_per_item


def test_error_de_autenticacion_no_pierde_la_cola_y_sigue_al_reconectar(direct):
    ref = _connected_account(direct)
    queue = direct.queue_manager
    queue.add_many([_ad(ref, n) for n in range(1, 4)])
    engine = direct.publication_engine
    engine.start()
    assert engine.run_once()  # publica el primero
    direct.server.valid.clear()  # la credencial deja de valer
    engine.run_until_idle()
    counts = queue.counts()
    assert counts["published"] == 1
    assert counts["pending"] == 2 and counts["failed"] == 0
    assert direct.auth_manager.status(ref).auth_state is AuthState.NEEDS_RECONNECT
    assert "reconecten" in (engine.status().waiting_reason or "")

    # El usuario pega una credencial nueva: continúa solo.
    direct.server.valid.add("Bearer nueva")
    assert direct.auth_manager.submit_credential(ref, "nueva").ok
    engine.run_until_idle()
    assert queue.counts()["published"] == 3


def test_otras_cuentas_siguen_si_una_falla(direct):
    a = _connected_account(direct, "Cuenta A")
    b = _connected_account(direct, "Cuenta B")
    direct.queue_manager.add_many([_ad(a, 1), _ad(b, 2), _ad(a, 3), _ad(b, 4)])
    direct.auth_manager.mark_rejected(a, "caducada")
    direct.publication_engine.start()
    direct.publication_engine.run_until_idle()
    counts_b = direct.queue_manager.counts(b)
    counts_a = direct.queue_manager.counts(a)
    assert counts_b["published"] == 2
    assert counts_a["pending"] == 2


def test_sin_red_reintenta_y_publica(direct):
    ref = _connected_account(direct)
    direct.server.fail_create = ["connect"]
    direct.queue_manager.add(_ad(ref))
    engine = direct.publication_engine
    engine.start()
    engine.run_until_idle()
    item = direct.queue_manager.items()[0]
    assert item["estado"] == "pending" and item["codigo_error"] == "NetworkError"
    # Pasa la espera del reintento.
    from datetime import timedelta

    from lot_bot.database.models import AdQueueItem

    with direct.db.session_scope() as s:
        s.get(AdQueueItem, item["id"]).next_attempt_at -= timedelta(hours=2)
    engine.run_until_idle()
    assert direct.queue_manager.counts()["published"] == 1
    assert len(direct.server.items) == 1


def test_respuesta_perdida_se_comprueba_y_no_se_duplica(direct):
    ref = _connected_account(direct)
    direct.server.fail_create = ["timeout"]  # se crea, pero no llega la respuesta
    direct.queue_manager.add(_ad(ref))
    engine = direct.publication_engine
    engine.start()
    engine.run_until_idle()
    item = direct.queue_manager.items()[0]
    assert item["estado"] == "verifying"
    from datetime import timedelta

    from lot_bot.database.models import AdQueueItem

    with direct.db.session_scope() as s:
        s.get(AdQueueItem, item["id"]).next_attempt_at -= timedelta(minutes=5)
    engine.run_until_idle()
    item = direct.queue_manager.items()[0]
    assert item["estado"] == "published"
    assert item["id_wallapop"] == "it-1"
    assert len(direct.server.items) == 1  # NO se ha duplicado


def test_cierre_inesperado_se_recupera_sin_duplicar(direct):
    ref = _connected_account(direct)
    queue = direct.queue_manager
    queue.add_many([_ad(ref, 1), _ad(ref, 2)])
    # Simula un cierre a mitad de publicación: el primero llegó a Wallapop.
    claimed = queue.claim_next(lambda r: True)
    direct.server.items.append({"id": "it-99", "title": claimed.title, "price": claimed.price})
    direct.publication_engine._set_state(EngineState.RUNNING)

    # «Se vuelve a abrir LOT Bot»
    assert queue.recover_interrupted() == 1
    direct.publication_engine.start_worker = lambda: None  # sin hilo en la prueba
    direct.publication_engine.run_until_idle()
    items = {i["titulo"]: i for i in queue.items()}
    assert items["Canapé abatible 1"]["estado"] == "published"
    assert items["Canapé abatible 1"]["id_wallapop"] == "it-99"
    assert items["Canapé abatible 2"]["estado"] == "published"
    assert len([i for i in direct.server.items if i["title"] == "Canapé abatible 1"]) == 1


def test_motor_en_marcha_al_cerrar_queda_en_pausa(direct):
    engine = direct.publication_engine
    engine._set_state(EngineState.RUNNING)
    engine.start_worker()
    try:
        assert engine.state is EngineState.PAUSED
        assert "Continuar" in (engine.pause_reason or "")
    finally:
        engine.shutdown()


def test_datos_rechazados_quedan_en_error_y_se_pueden_reintentar(direct):
    ref = _connected_account(direct)
    direct.server.fail_create = [422]
    direct.queue_manager.add(_ad(ref))
    engine = direct.publication_engine
    engine.start()
    engine.run_until_idle()
    item = direct.queue_manager.items()[0]
    assert item["estado"] == "failed" and item["error"]
    assert direct.queue_manager.retry([item["id"]]) == 1
    engine.run_until_idle()
    assert direct.queue_manager.counts()["published"] == 1


def test_limite_de_wallapop_429_espera_sin_perder(direct):
    ref = _connected_account(direct)
    direct.server.fail_create = [429]
    direct.queue_manager.add(_ad(ref))
    engine = direct.publication_engine
    engine.start()
    engine.run_until_idle()
    item = direct.queue_manager.items()[0]
    assert item["estado"] == "pending" and item["codigo_error"] == "LIMITE_WALLAPOP"
    assert item["intentos"] == 0
    assert engine._cooldown_until is not None


def test_limite_diario_por_cuenta(direct):
    ref = _connected_account(direct)
    direct.publication_engine.save_settings(max_per_account_per_day=2)
    direct.queue_manager.add_many([_ad(ref, n) for n in range(1, 5)])
    direct.publication_engine.start()
    direct.publication_engine.run_until_idle()
    counts = direct.queue_manager.counts()
    assert counts["published"] == 2 and counts["pending"] == 2
    pendiente = direct.queue_manager.items(AdQueueStatus.PENDING)[0]
    assert pendiente["codigo_error"] == "LIMITE_DIARIO"


def test_intervalo_nunca_inferior_al_minimo(direct):
    with pytest.raises(ValueError):
        direct.publication_engine.save_settings(interval_seconds=10)
    assert direct.publication_engine.save_settings(interval_seconds=120)["interval_seconds"] == 120


def test_varios_fallos_seguidos_pausan(direct):
    ref = _connected_account(direct)
    direct.server.fail_create = [422] * 6
    direct.queue_manager.add_many([_ad(ref, n) for n in range(1, 8)])
    engine = direct.publication_engine
    engine.start()
    engine.run_until_idle()
    assert engine.state is EngineState.PAUSED
    assert "seguidos" in engine.pause_reason


def test_el_reloj_de_pruebas_no_llega_a_wallapop_real(direct):
    direct.backend.service.allow_test_clock = False
    ref = _connected_account(direct)
    direct.queue_manager.add(_ad(ref))
    direct.publication_engine.start()
    with pytest.raises(RuntimeError):
        direct.publication_engine.run_once()


def test_el_motor_directo_no_funciona_en_modo_navegador(app, monkeypatch):
    from lot_bot.wallapop.browser.driver import PlaywrightLauncher

    monkeypatch.setattr(PlaywrightLauncher, "available", lambda self: (True, ""))
    app.set_integration_mode("navegador")
    with pytest.raises(ValueError, match="no usa el navegador"):
        app.publication_engine.start()


# ---------------------------------------------------------------------------
# DEMO
# ---------------------------------------------------------------------------
def test_demo_publica_toda_la_cola(app):
    app.direct.engine.clock = FakeClock()
    ref = app.accounts.list_accounts()[0].internal_ref
    app.queue_manager.add_many([_ad(ref, n) for n in range(1, 21)])
    app.publication_engine.start()
    app.publication_engine.run_until_idle()
    assert app.queue_manager.counts()["published"] == 20


def test_database_manager_integridad_y_ajustes(app):
    db = app.database_manager
    assert db.integrity_ok()
    db.update_setting("prueba", a=1)
    assert db.get_setting("prueba") == {"a": 1}


def test_el_perfil_de_ejemplo_no_concede_acceso_ni_limites():
    """La plantilla está vacía: no inventa nada de Wallapop."""
    from lot_bot.wallapop.access_profile import AccessProfile

    raiz = Path(__file__).resolve().parents[1]
    perfil = AccessProfile.load(raiz / "config" / "access_profile.example.yaml")
    assert not perfil.has_auth and not perfil.has_transport
    assert perfil.limits.min_seconds_between_publications is None
    assert perfil.limits.max_images_per_item == 10


def test_limites_del_perfil_se_leen_y_validan():
    from lot_bot.wallapop.access_profile import AccessProfile
    from lot_bot.wallapop.errors import ConfigurationError

    perfil = AccessProfile.from_dict({"limits": {"max_publications_per_day": "50",
                                                 "idempotency_header": "Idempotency-Key"}})
    assert perfil.limits.max_publications_per_day == 50
    assert perfil.limits.idempotency_header == "Idempotency-Key"
    with pytest.raises(ConfigurationError):
        AccessProfile.from_dict({"limits": {"max_requests_per_minute": "muchas"}})


def test_limite_de_peticiones_por_minuto():
    from lot_bot.integration.wallapop_client import RequestRateLimiter

    reloj = {"t": 0.0}
    esperas: list[float] = []

    def dormir(s):
        esperas.append(s)
        reloj["t"] += s

    limitador = RequestRateLimiter(3, clock=lambda: reloj["t"], sleep=dormir)
    for _ in range(4):
        limitador.acquire()
    assert len(esperas) == 1 and esperas[0] >= 59.9


def test_productos_del_catalogo_a_la_cola(app, tmp_path):
    from lot_bot.bootstrap import seed_demo_catalog
    from lot_bot.integration import enqueue_products

    # Los productos de ejemplo tienen errores de calidad (sin fotos, variables
    # sin rellenar): se omiten y se dice por qué, como en «Productos».
    seed_demo_catalog(app)
    skipped = enqueue_products(app)
    assert not skipped.added and skipped.invalid
    assert all("error" in line for line in skipped.invalid)

    app.save_business_settings({"whatsapp": "600000000", "precio_90": "230",
                                "precio_135": "270", "precio_150": "290"})
    product = app.catalog.create_product({
        "name": "Canapé abatible 135x190", "product_type": "Canapé abatible",
        "category": "Hogar y jardín", "subcategory": "Colchones y canapés",
        "size": "135x190", "color": "Gris", "material": "Madera",
        "condition": "Nuevo", "price": 270.0, "stock": 3,
        "description": "Canapé abatible con gran capacidad de almacenaje.",
    })
    app.catalog.add_image(product.id, {"path": _photo(tmp_path, "mesa.jpg")})
    refs = [a.internal_ref for a in app.accounts.list_accounts()][:2]
    app.catalog.assign_to_accounts(product.id, refs)
    result = enqueue_products(app, [product.id])
    assert len(result.added) == 2, result.invalid
    assert not enqueue_products(app, [product.id]).added  # duplicados
    app.direct.engine.clock = FakeClock()
    app.publication_engine.start()
    app.publication_engine.run_until_idle()
    assert app.queue_manager.counts()["published"] == 2


def test_cuenta_demo_no_publica_en_wallapop_real(direct):
    demo = direct.accounts.add_account("Demo", is_demo=True)
    direct.queue_manager.add(_ad(demo.internal_ref))
    direct.publication_engine.start()
    assert direct.publication_engine.run_until_idle() == 0
    assert direct.server.items == []
    assert direct.auth_manager.status(demo.internal_ref).auth_state is AuthState.NOT_CONNECTED


def test_idempotencia_si_el_perfil_la_declara(database, temp_paths, tmp_path):
    from lot_bot.bootstrap import create_application
    from lot_bot.config.settings import Settings

    perfil = dict(PROFILE, limits={"min_seconds_between_publications": 90,
                                   "idempotency_header": "Idempotency-Key"})
    path = _write_profile(tmp_path, perfil)
    settings = Settings(LOT_BOT_DEMO_MODE=False, LOT_BOT_AI_PROVIDER="rules",
                        LOT_BOT_LOG_LEVEL="WARNING", WALLAPOP_ACCESS_PROFILE=str(path),
                        LOT_BOT_WALLAPOP_INTEGRATION="perfil")
    app = create_application(settings=settings, database=database, start_scheduler=False)
    try:
        assert app.publication_engine.minimum_interval == 90
        server = FakeWallapop()
        _attach(app, server)
        app.direct.engine.clock = FakeClock()
        ref = _connected_account(app)
        app.queue_manager.add(_ad(ref))
        app.publication_engine.start()
        app.publication_engine.run_until_idle()
        create = [r for r in server.requests if r.method == "POST" and r.url.path == "/items"]
        assert create and create[0].headers.get("Idempotency-Key")
    finally:
        app.automations.shutdown()
