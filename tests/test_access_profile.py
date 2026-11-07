"""Pruebas del perfil de acceso: separa autenticacion de transporte."""

from __future__ import annotations

import pytest

from lot_bot.config.settings import Settings
from lot_bot.wallapop.access_profile import AccessProfile
from lot_bot.wallapop.auth import AuthKind
from lot_bot.wallapop.factory import build_backend

PERFIL_COMPLETO = {
    "meta": {"authorized_by": "Contacto de Wallapop", "authorization_ref": "ACUERDO-1"},
    "auth": {
        "method": "session_handoff",
        "session_handoff": {
            "login_url": "https://ejemplo.invalid/autorizar",
            "return_uri": "http://127.0.0.1:8799/callback",
            "session_fields": ["session_token"],
            "header_template": {"Authorization": "Bearer {session_token}"},
        },
    },
    "api": {"base_url": "https://api.invalid"},
    "operations": {
        "list_items": {"method": "GET", "path": "/items"},
        "update_item_price": {"method": "PATCH", "path": "/items/{item_id}"},
    },
}


def escribir_perfil(tmp_path, data) -> str:
    import yaml

    ruta = tmp_path / "perfil.yaml"
    ruta.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(ruta)


# ---------------------------------------------------------------------------
# Las dos piezas son independientes
# ---------------------------------------------------------------------------
def test_autenticacion_sin_transporte_no_basta():
    perfil = AccessProfile.from_dict({"auth": PERFIL_COMPLETO["auth"]})
    assert perfil.has_auth
    assert not perfil.has_transport
    assert not perfil.is_complete
    assert any("dirección base" in m for m in perfil.missing_pieces())


def test_transporte_sin_autenticacion_no_basta():
    perfil = AccessProfile.from_dict(
        {"api": PERFIL_COMPLETO["api"], "operations": PERFIL_COMPLETO["operations"]}
    )
    assert perfil.has_transport
    assert not perfil.has_auth
    assert not perfil.is_complete
    assert any("mecanismo de autenticación" in m for m in perfil.missing_pieces())


def test_las_dos_piezas_juntas_completan_el_perfil():
    perfil = AccessProfile.from_dict(PERFIL_COMPLETO)
    assert perfil.is_complete
    assert perfil.auth.method is AuthKind.SESSION_HANDOFF
    assert len(perfil.transport.operations) == 2
    assert perfil.authorized_by == "Contacto de Wallapop"


# ---------------------------------------------------------------------------
# Elección del backend
# ---------------------------------------------------------------------------
def test_sin_perfil_se_mantiene_demo():
    backend = build_backend(Settings(LOT_BOT_DEMO_MODE=False))
    assert backend.demo
    assert backend.label == "MODO DEMO"
    assert backend.missing


def test_con_perfil_incompleto_se_mantiene_demo_y_se_explica(tmp_path):
    ruta = escribir_perfil(tmp_path, {"auth": PERFIL_COMPLETO["auth"]})
    backend = build_backend(
        Settings(LOT_BOT_DEMO_MODE=False, WALLAPOP_ACCESS_PROFILE=ruta)
    )
    assert backend.demo
    assert backend.missing
    assert any("dirección base" in m for m in backend.missing)


def test_con_perfil_completo_se_activa_el_acceso_real(tmp_path, database):
    from cryptography.fernet import Fernet

    from lot_bot.config.secrets import SecretBox
    from lot_bot.wallapop.account_manager import AccountManager

    ruta = escribir_perfil(tmp_path, PERFIL_COMPLETO)
    manager = AccountManager(database, SecretBox(Fernet.generate_key()))
    backend = build_backend(
        Settings(LOT_BOT_DEMO_MODE=False, WALLAPOP_ACCESS_PROFILE=ruta), manager
    )

    assert not backend.demo
    assert backend.label == "WALLAPOP REAL"
    assert not backend.service.is_mock
    assert backend.auth_method.kind is AuthKind.SESSION_HANDOFF
    # El mecanismo queda instalado en el gestor de cuentas
    assert manager.auth_method.kind is AuthKind.SESSION_HANDOFF


def test_el_acceso_real_no_exige_client_id(tmp_path, database, monkeypatch):
    """El punto del cambio: sin API key también se puede conectar."""
    from cryptography.fernet import Fernet

    from lot_bot.config.secrets import SecretBox
    from lot_bot.wallapop.account_manager import AccountManager

    monkeypatch.delenv("WALLAPOP_CLIENT_ID", raising=False)
    monkeypatch.delenv("WALLAPOP_CLIENT_SECRET", raising=False)

    ruta = escribir_perfil(tmp_path, PERFIL_COMPLETO)
    ajustes = Settings(LOT_BOT_DEMO_MODE=False, WALLAPOP_ACCESS_PROFILE=ruta)
    assert not ajustes.has_oauth_client_credentials

    backend = build_backend(ajustes, AccountManager(database, SecretBox(Fernet.generate_key())))
    assert not backend.demo, "Debe poder conectar sin client_id ni client_secret"


def test_si_el_mecanismo_no_esta_listo_se_mantiene_demo(tmp_path, database, monkeypatch):
    """Perfil con transporte pero con autenticación a medias."""
    from cryptography.fernet import Fernet

    from lot_bot.config.secrets import SecretBox
    from lot_bot.wallapop.account_manager import AccountManager

    monkeypatch.delenv("WALLAPOP_CLIENT_ID", raising=False)
    incompleto = dict(PERFIL_COMPLETO)
    incompleto["auth"] = {
        "method": "oauth",
        "oauth": {"authorize_url": "https://ejemplo.invalid/a", "token_url": "https://ejemplo.invalid/t"},
    }
    ruta = escribir_perfil(tmp_path, incompleto)
    backend = build_backend(
        Settings(LOT_BOT_DEMO_MODE=False, WALLAPOP_ACCESS_PROFILE=ruta),
        AccountManager(database, SecretBox(Fernet.generate_key())),
    )
    assert backend.demo
    assert any("Identificador de cliente" in m for m in backend.missing)


def test_operaciones_no_declaradas_se_listan_como_no_disponibles(tmp_path, database):
    from cryptography.fernet import Fernet

    from lot_bot.config.secrets import SecretBox
    from lot_bot.wallapop.account_manager import AccountManager

    ruta = escribir_perfil(tmp_path, PERFIL_COMPLETO)
    backend = build_backend(
        Settings(LOT_BOT_DEMO_MODE=False, WALLAPOP_ACCESS_PROFILE=ruta),
        AccountManager(database, SecretBox(Fernet.generate_key())),
    )
    assert not backend.demo
    no_disponibles = [m for m in backend.missing if "no autorizada" in m]
    assert any("delete_item" in m for m in no_disponibles)
    assert any("send_message" in m for m in no_disponibles)


def test_el_nombre_antiguo_de_la_variable_sigue_funcionando(tmp_path):
    ruta = escribir_perfil(tmp_path, PERFIL_COMPLETO)
    ajustes = Settings(LOT_BOT_DEMO_MODE=False, WALLAPOP_ENDPOINT_MAP=ruta)
    assert ajustes.access_profile_path is not None
    assert ajustes.can_use_real_wallapop


def test_demo_explicito_manda_sobre_el_perfil(tmp_path):
    ruta = escribir_perfil(tmp_path, PERFIL_COMPLETO)
    backend = build_backend(
        Settings(LOT_BOT_DEMO_MODE=True, WALLAPOP_ACCESS_PROFILE=ruta)
    )
    assert backend.demo


def test_el_servicio_real_no_se_construye_sin_gestor_de_cuentas(tmp_path):
    ruta = escribir_perfil(tmp_path, PERFIL_COMPLETO)
    with pytest.raises(ValueError):
        build_backend(Settings(LOT_BOT_DEMO_MODE=False, WALLAPOP_ACCESS_PROFILE=ruta), None)
