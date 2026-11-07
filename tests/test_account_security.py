"""Pruebas de seguridad y aislamiento de las credenciales por cuenta.

Cubren los tres riesgos del nuevo mecanismo de acceso:
  1. Que una credencial se filtre (logs, pantalla, IA, errores).
  2. Que se mezclen datos o credenciales entre cuentas.
  3. Que una sesión caducada siga usándose como si fuera válida.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from lot_bot.config.secrets import SecretBox
from lot_bot.database.models import Account, AccountStatus
from lot_bot.logs.redaction import RedactingFilter, clear_secrets, redact
from lot_bot.wallapop.account_manager import AccountManager
from lot_bot.wallapop.auth import AuthCredential, AuthKind
from lot_bot.wallapop.auth.base import AuthMethod, AuthOutcome
from lot_bot.wallapop.errors import AuthenticationError

SECRETO_SESION = "SESION-SUPERSECRETA-123456"
SECRETO_COOKIE = "COOKIE-SUPERSECRETA-abcdef"


class MecanismoDePrueba(AuthMethod):
    """Mecanismo controlado, para probar el ciclo completo sin red."""

    kind = AuthKind.SESSION_HANDOFF
    display_name = "Sesión de prueba"

    def __init__(self, renovable: bool = False) -> None:
        self.renovable = renovable
        self.revocaciones: list[str] = []

    def requirements(self):
        return []

    def authenticate(self, account_ref: str, **context):
        return AuthOutcome(
            success=True,
            credential=AuthCredential(
                kind=AuthKind.SESSION_HANDOFF,
                headers={"Authorization": f"Bearer {SECRETO_SESION}-{account_ref}"},
                cookies={"sid": f"{SECRETO_COOKIE}-{account_ref}"},
                expires_at=context.get("expires_at"),
                metadata={"user_id": f"u-{account_ref}"},
            ),
            message="Sesión de prueba creada.",
        )

    def renew(self, credential):
        if not self.renovable:
            return None
        return AuthCredential(
            kind=AuthKind.SESSION_HANDOFF,
            headers={"Authorization": "Bearer RENOVADA"},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    def revoke(self, credential):
        self.revocaciones.append("revocada")
        return True


@pytest.fixture()
def manager(database):
    return AccountManager(database, SecretBox(Fernet.generate_key()), MecanismoDePrueba())


# ---------------------------------------------------------------------------
# 1. Las credenciales no se filtran
# ---------------------------------------------------------------------------
def test_la_credencial_no_se_guarda_en_claro(database, manager):
    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.connect("c1")

    with database.session_scope() as session:
        fila = session.scalar(select(Account).where(Account.internal_ref == "c1"))
        guardado = fila.credential_enc or ""
        assert guardado
        assert SECRETO_SESION not in guardado
        assert SECRETO_COOKIE not in guardado
        # Los campos antiguos quedan vacíos
        assert not fila.access_token_enc
        assert not fila.refresh_token_enc


def test_la_credencial_no_se_puede_imprimir():
    credencial = AuthCredential(
        kind=AuthKind.SESSION_HANDOFF,
        headers={"Authorization": f"Bearer {SECRETO_SESION}"},
        cookies={"sid": SECRETO_COOKIE},
    )
    for texto in (repr(credencial), str(credencial), f"{credencial}"):
        assert SECRETO_SESION not in texto
        assert SECRETO_COOKIE not in texto


def test_la_vista_de_cuenta_no_expone_la_credencial(manager):
    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.connect("c1")
    info = manager.get_account("c1")
    volcado = f"{info} {vars(info) if hasattr(info, '__dict__') else ''}"
    assert SECRETO_SESION not in volcado
    assert SECRETO_COOKIE not in volcado
    assert not hasattr(info, "credential_enc")


def test_la_credencial_se_tacha_en_los_logs(manager, caplog):
    clear_secrets()
    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.connect("c1")
    # Al conectarse, los valores quedan registrados como secretos a tachar.
    manager.get_credential("c1")

    texto = f"Cabecera enviada: Bearer {SECRETO_SESION}-c1 cookie sid={SECRETO_COOKIE}-c1"
    limpio = redact(texto)
    assert SECRETO_SESION not in limpio
    assert SECRETO_COOKIE not in limpio
    assert "REDACTED" in limpio


def test_el_filtro_de_logging_tacha_el_registro(manager):
    clear_secrets()
    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.connect("c1")
    manager.get_credential("c1")

    registro = logging.LogRecord(
        name="prueba",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="token=%s",
        args=(f"{SECRETO_SESION}-c1",),
        exc_info=None,
    )
    RedactingFilter().filter(registro)
    assert SECRETO_SESION not in registro.getMessage()


def test_el_historial_no_guarda_la_credencial(app_with_data):
    """Ni siquiera si alguien la pasara por error en el payload."""
    app_with_data.audit.record(
        "Prueba",
        detail=f"Authorization: Bearer {SECRETO_SESION}",
        payload={"credential": SECRETO_SESION, "cookies": {"sid": SECRETO_COOKIE}},
    )
    entrada = app_with_data.audit.recent(limit=1)[0]
    assert SECRETO_SESION not in (entrada.detail or "")


# ---------------------------------------------------------------------------
# 2. Aislamiento entre cuentas
# ---------------------------------------------------------------------------
def test_cada_cuenta_tiene_su_propia_credencial(manager):
    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.add_account("Cuenta 2", internal_ref="c2")
    manager.connect("c1")
    manager.connect("c2")

    cred1 = manager.get_credential("c1")
    cred2 = manager.get_credential("c2")
    assert cred1.headers != cred2.headers
    assert cred1.cookies != cred2.cookies
    assert cred1.metadata["user_id"] == "u-c1"
    assert cred2.metadata["user_id"] == "u-c2"


def test_desconectar_una_cuenta_no_afecta_a_la_otra(manager):
    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.add_account("Cuenta 2", internal_ref="c2")
    manager.connect("c1")
    manager.connect("c2")

    manager.disconnect("c1")

    with pytest.raises(AuthenticationError):
        manager.get_credential("c1")
    assert manager.get_credential("c2") is not None
    assert manager.get_account("c2").is_connected


def test_eliminar_una_cuenta_no_afecta_a_la_otra(manager):
    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.add_account("Cuenta 2", internal_ref="c2")
    manager.connect("c1")
    manager.connect("c2")

    manager.remove_account("c1")
    assert manager.get_account("c1") is None
    assert manager.get_credential("c2").headers


def test_cada_cuenta_recuerda_su_mecanismo(manager):
    from lot_bot.wallapop.auth.demo import DemoAuthMethod

    manager.add_account("Real", internal_ref="c1")
    manager.connect("c1")
    manager.add_account("Demo", internal_ref="c2")
    manager.connect("c2", method=DemoAuthMethod())

    assert manager.get_account("c1").auth_method == AuthKind.SESSION_HANDOFF.value
    assert manager.get_account("c2").auth_method == AuthKind.DEMO.value


def test_los_anuncios_siguen_aislados_por_cuenta(app_with_data):
    from lot_bot.publishing.listings import ListingFilter

    refs = [a.internal_ref for a in app_with_data.accounts.list_accounts()]
    vistos: set[int] = set()
    for ref in refs:
        anuncios = app_with_data.listings.search(ListingFilter(account_ref=ref))
        identificadores = {v.id for v in anuncios}
        assert all(v.account_ref == ref for v in anuncios)
        assert not (identificadores & vistos), "Un anuncio aparece en dos cuentas"
        vistos |= identificadores


# ---------------------------------------------------------------------------
# 3. Sesión caducada y desconexión
# ---------------------------------------------------------------------------
def test_una_credencial_caducada_no_desconecta_la_cuenta(manager):
    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.connect("c1", expires_at=datetime.now(UTC) - timedelta(minutes=1))

    with pytest.raises(AuthenticationError) as error:
        manager.get_credential("c1")
    assert "caducado" in error.value.detail.lower()
    # Las cuentas no caducan en LOT Bot: siguen conectadas hasta que el usuario las elimina.
    assert manager.get_account("c1").status is AccountStatus.CONNECTED


def test_una_sesion_caducada_renovable_se_renueva_sola(database):
    manager = AccountManager(
        database, SecretBox(Fernet.generate_key()), MecanismoDePrueba(renovable=True)
    )
    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.connect("c1", expires_at=datetime.now(UTC) - timedelta(minutes=1))

    credencial = manager.get_credential("c1")
    assert credencial.headers["Authorization"] == "Bearer RENOVADA"
    assert manager.get_account("c1").is_connected


def test_reautenticar_sustituye_la_credencial(manager):
    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.connect("c1", expires_at=datetime.now(UTC) - timedelta(minutes=1))
    with pytest.raises(AuthenticationError):
        manager.get_credential("c1")

    resultado = manager.reauthenticate("c1")
    assert resultado.success
    assert manager.get_account("c1").is_connected
    assert manager.get_credential("c1").headers


def test_desconectar_intenta_revocar(database):
    mecanismo = MecanismoDePrueba()
    manager = AccountManager(database, SecretBox(Fernet.generate_key()), mecanismo)
    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.connect("c1")
    manager.disconnect("c1", revoke=True)
    assert mecanismo.revocaciones == ["revocada"]


def test_una_cuenta_sin_conectar_no_da_credencial(manager):
    manager.add_account("Cuenta 1", internal_ref="c1")
    with pytest.raises(AuthenticationError):
        manager.get_credential("c1")


def test_una_credencial_demo_nunca_sale_a_la_red():
    """Blindaje: aunque se colara una cuenta DEMO, no se usa contra Wallapop."""
    from lot_bot.wallapop.authorized_service import AuthorizedWallapopService
    from lot_bot.wallapop.endpoint_map import EndpointMap

    mapa = EndpointMap.from_dict(
        {
            "api": {"base_url": "https://api.invalid"},
            "operations": {"list_items": {"method": "GET", "path": "/items"}},
        }
    )
    demo = AuthCredential(kind=AuthKind.DEMO, headers={"X": "y"})
    servicio = AuthorizedWallapopService(mapa, lambda ref: demo)
    with pytest.raises(AuthenticationError) as error:
        servicio.list_items("demo-1")
    assert "demostración" in error.value.user_message


def test_la_credencial_sobrevive_al_cifrado(manager, database):
    """Lo guardado se recupera intacto, y el fichero no contiene el secreto."""
    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.connect("c1")
    recuperada = manager.get_credential("c1")
    assert recuperada.headers["Authorization"].endswith("-c1")
    assert recuperada.cookies["sid"].endswith("-c1")

    with database.session_scope() as session:
        fila = session.scalar(select(Account).where(Account.internal_ref == "c1"))
        with pytest.raises(ValueError):
            json.loads(fila.credential_enc)  # está cifrado, no es JSON legible


# ---------------------------------------------------------------------------
# 4. DEMO nunca se presenta como Wallapop real
# ---------------------------------------------------------------------------
def test_una_cuenta_demo_no_figura_como_conectada_en_modo_real():
    """Regla: jamás mostrar algo DEMO como si fuera Wallapop real."""
    from lot_bot.ui.views.accounts import AccountsView

    class CuentaFalsa:
        is_demo = True
        is_connected = True
        status_label = "Conectada"

        class status:  # noqa: N801
            value = "connected"

    cuenta = CuentaFalsa()
    # En DEMO sí cuenta como conectada...
    assert AccountsView._really_connected(cuenta, demo_mode=True)
    assert AccountsView._status_text(cuenta, demo_mode=True) == "Conectada"
    # ...pero en modo real, no.
    assert not AccountsView._really_connected(cuenta, demo_mode=False)
    assert AccountsView._status_text(cuenta, demo_mode=False) == "Solo demostración"


def test_la_barra_de_estado_distingue_demo_de_real(app):
    """La interfaz debe decir en qué modo está, sin ambigüedad."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from lot_bot.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    ventana = MainWindow(app)
    assert "MODO DEMO" in ventana.status_label.text()
    assert ventana.brand_sub.text() == "MODO DEMO"
    ventana.close()


def test_cuentas_caducadas_de_versiones_anteriores_vuelven_a_estar_conectadas(manager, database):
    from lot_bot.database.models import Account

    manager.add_account("Cuenta 1", internal_ref="c1")
    manager.connect("c1")
    with database.session_scope() as session:
        session.query(Account).filter_by(internal_ref="c1").one().status = AccountStatus.EXPIRED
    assert manager.restore_expired_accounts() == 1
    assert manager.get_account("c1").status is AccountStatus.CONNECTED
