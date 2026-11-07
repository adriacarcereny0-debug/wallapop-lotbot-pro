"""Pruebas de los mecanismos de acceso autorizado.

Cubren lo esencial del cambio de arquitectura: LOT Bot ya no exige una API
key, pero tampoco se inventa un mecanismo que Wallapop no haya declarado.
"""

from __future__ import annotations

import pytest

from lot_bot.wallapop.access_profile import AccessProfile, empty_profile
from lot_bot.wallapop.auth import (
    AuthCredential,
    AuthKind,
    DelegatedCredentialAuthMethod,
    DemoAuthMethod,
    OAuthAuthMethod,
    SessionHandoffAuthMethod,
    available_methods,
    build_auth_method,
)
from lot_bot.wallapop.auth.base import RequirementSource
from lot_bot.wallapop.auth.config import DelegatedCredentialConfig, SessionHandoffConfig


# ---------------------------------------------------------------------------
# Sin configuración: no se inventa nada
# ---------------------------------------------------------------------------
def test_sin_perfil_ningun_mecanismo_esta_listo():
    for method in available_methods(empty_profile().auth):
        assert not method.is_ready, f"{method.describe()} no debería estar listo"


def test_sin_perfil_el_mecanismo_por_defecto_es_demo():
    """LOT Bot no supone un mecanismo: si no hay ninguno declarado, es DEMO."""
    method = build_auth_method(empty_profile().auth)
    assert method.kind is AuthKind.DEMO


def test_los_datos_que_faltan_indican_quien_los_proporciona():
    method = SessionHandoffAuthMethod(SessionHandoffConfig())
    missing = method.missing_requirements()
    assert missing
    assert all(r.source is RequirementSource.WALLAPOP for r in missing)
    assert all(r.description and r.where for r in missing)


def test_el_perfil_de_ejemplo_no_concede_acceso():
    """El fichero del repositorio está vacío a propósito."""
    from pathlib import Path

    ruta = Path(__file__).resolve().parents[1] / "config" / "access_profile.example.yaml"
    profile = AccessProfile.load(ruta)
    assert not profile.has_auth
    assert not profile.has_transport
    assert not profile.is_complete
    assert len(profile.missing_pieces()) == 3


def test_metodo_desconocido_es_rechazado():
    from lot_bot.wallapop.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        AccessProfile.from_dict({"auth": {"method": "telepatia"}})


# ---------------------------------------------------------------------------
# Inicio de sesión autorizado
# ---------------------------------------------------------------------------
@pytest.fixture()
def session_config() -> SessionHandoffConfig:
    return SessionHandoffConfig(
        login_url="https://ejemplo.invalid/autorizar",
        return_uri="http://127.0.0.1:8799/callback",
        session_fields=["session_token", "user_id"],
        header_template={"Authorization": "Bearer {session_token}"},
        expires_after_minutes=60,
    )


def test_sesion_configurada_esta_lista(session_config):
    assert SessionHandoffAuthMethod(session_config).is_ready


def test_sin_datos_no_se_abre_el_navegador(monkeypatch):
    """Si falta información, el flujo ni siquiera se inicia."""
    abierto = []
    monkeypatch.setattr(
        "lot_bot.wallapop.auth.session_method.open_browser",
        lambda url: abierto.append(url) or True,
    )
    resultado = SessionHandoffAuthMethod(SessionHandoffConfig()).authenticate("acc-1")
    assert not resultado.success
    assert resultado.blocked_by_missing_data
    assert abierto == [], "No debe abrirse el navegador sin datos"


def test_la_sesion_se_compone_con_lo_que_devuelve_el_flujo(session_config):
    method = SessionHandoffAuthMethod(session_config)
    credential = method._build_credential(
        {"session_token": "VALOR-SESION", "user_id": "u-7", "state": "abc"}
    )
    assert credential is not None
    assert credential.kind is AuthKind.SESSION_HANDOFF
    assert credential.headers == {"Authorization": "Bearer VALOR-SESION"}
    assert credential.metadata["user_id"] == "u-7"
    assert credential.expires_at is not None


def test_un_campo_que_no_llega_no_se_inventa(session_config):
    """Si el flujo no devuelve el campo esperado, no se rellena con nada."""
    method = SessionHandoffAuthMethod(session_config)
    assert method._build_credential({"otra_cosa": "x"}) is None


def test_la_sesion_no_se_renueva_sola(session_config):
    method = SessionHandoffAuthMethod(session_config)
    credential = AuthCredential(kind=AuthKind.SESSION_HANDOFF, headers={"A": "b"})
    assert method.renew(credential) is None, "Debe pedirse al usuario que reautentique"


# ---------------------------------------------------------------------------
# Credencial delegada
# ---------------------------------------------------------------------------
def test_credencial_delegada_requiere_declaracion_explicita():
    """El formato por defecto es una plantilla nuestra, no un dato de Wallapop."""
    assert not DelegatedCredentialAuthMethod(DelegatedCredentialConfig()).is_ready
    declarada = DelegatedCredentialConfig(declared=True)
    assert DelegatedCredentialAuthMethod(declarada).is_ready


def test_credencial_delegada_se_aplica_a_la_cabecera():
    config = DelegatedCredentialConfig(declared=True, header_format="Bearer {credential}")
    resultado = DelegatedCredentialAuthMethod(config).authenticate("acc-1", credential="ABC123")
    assert resultado.success
    assert resultado.credential.headers == {"Authorization": "Bearer ABC123"}


def test_credencial_delegada_vacia_se_rechaza():
    config = DelegatedCredentialConfig(declared=True)
    resultado = DelegatedCredentialAuthMethod(config).authenticate("acc-1", credential="   ")
    assert not resultado.success


# ---------------------------------------------------------------------------
# OAuth (sigue existiendo, pero ya no es obligatorio)
# ---------------------------------------------------------------------------
def test_oauth_declara_que_le_falta_el_client_id(monkeypatch):
    monkeypatch.delenv("WALLAPOP_CLIENT_ID", raising=False)
    profile = AccessProfile.from_dict(
        {
            "auth": {
                "method": "oauth",
                "oauth": {
                    "authorize_url": "https://ejemplo.invalid/a",
                    "token_url": "https://ejemplo.invalid/t",
                },
            }
        }
    )
    method = OAuthAuthMethod(profile.auth, redirect_uri="http://127.0.0.1:8723/callback")
    faltan = {r.key for r in method.missing_requirements()}
    assert faltan == {"client_id"}


def test_oauth_sin_datos_no_inicia_el_flujo():
    from lot_bot.wallapop.auth.config import AuthConfig

    resultado = OAuthAuthMethod(AuthConfig()).authenticate("acc-1")
    assert not resultado.success
    assert resultado.blocked_by_missing_data


# ---------------------------------------------------------------------------
# DEMO
# ---------------------------------------------------------------------------
def test_demo_conecta_sin_pedir_nada():
    resultado = DemoAuthMethod().authenticate("demo-1")
    assert resultado.success
    assert resultado.credential.kind is AuthKind.DEMO
    assert DemoAuthMethod().requirements() == []
