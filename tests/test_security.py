"""Pruebas de seguridad: cifrado, redaccion de secretos y manejo de tokens."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from lot_bot.config.secrets import SecretBox, SecretsError, mask
from lot_bot.core.audit import AuditService
from lot_bot.database.models import Account, AccountStatus
from lot_bot.logs.redaction import clear_secrets, redact, register_secret
from lot_bot.wallapop.account_manager import AccountManager
from lot_bot.wallapop.dto import OAuthTokens
from lot_bot.wallapop.errors import AuthenticationError


def test_cifrado_y_descifrado(secret_box):
    cifrado = secret_box.encrypt("token-super-secreto")
    assert "token-super-secreto" not in cifrado
    assert secret_box.decrypt(cifrado) == "token-super-secreto"


def test_descifrar_con_otra_clave_falla(secret_box):
    cifrado = secret_box.encrypt("token")
    otra_caja = SecretBox(Fernet.generate_key())
    with pytest.raises(SecretsError):
        otra_caja.decrypt(cifrado)


def test_enmascarado_de_secretos():
    assert mask("sk-ant-1234567890") == "*" * 13 + "7890"
    assert mask("abc") == "***"
    assert mask("") == "(no configurado)"


def test_redaccion_de_secretos_conocidos():
    clear_secrets()
    register_secret("mi-token-secretisimo")
    assert "mi-token-secretisimo" not in redact("Cabecera: mi-token-secretisimo")
    clear_secrets()


@pytest.mark.parametrize(
    "texto",
    [
        "Authorization: Bearer abcdefghijklmnop",
        'client_secret="valor-secreto"',
        "sk-ant-api03-ABCdefGHIjklMNO",
        "token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9xxxx",
    ],
)
def test_redaccion_por_patron(texto):
    assert "REDACTED" in redact(texto)


def test_los_tokens_se_guardan_cifrados_en_la_base_de_datos(database, secret_box):
    from sqlalchemy import select

    gestor = AccountManager(database, secret_box)
    gestor.add_account("Cuenta 1", internal_ref="c1")
    gestor.store_tokens("c1", OAuthTokens(access_token="ACCESO-123", refresh_token="REFRESCO-456"))

    with database.session_scope() as sesion:
        fila = sesion.scalar(select(Account).where(Account.internal_ref == "c1"))
        assert "ACCESO-123" not in (fila.access_token_enc or "")
        assert "REFRESCO-456" not in (fila.refresh_token_enc or "")
        assert fila.status is AccountStatus.CONNECTED

    assert gestor.get_access_token("c1") == "ACCESO-123"


def test_la_vista_de_cuenta_nunca_expone_tokens(database, secret_box):
    gestor = AccountManager(database, secret_box)
    gestor.add_account("Cuenta 1", internal_ref="c1")
    gestor.store_tokens("c1", OAuthTokens(access_token="ACCESO-123"))
    info = gestor.get_account("c1")
    assert "ACCESO" not in str(info)
    assert not hasattr(info, "access_token_enc")


def test_desconectar_borra_los_tokens(database, secret_box):
    gestor = AccountManager(database, secret_box)
    gestor.add_account("Cuenta 1", internal_ref="c1")
    gestor.store_tokens("c1", OAuthTokens(access_token="ACCESO-123"))
    assert gestor.disconnect("c1", revoke=False)
    with pytest.raises(AuthenticationError):
        gestor.get_access_token("c1")


def test_cuenta_sin_conectar_no_da_token(database, secret_box):
    gestor = AccountManager(database, secret_box)
    gestor.add_account("Cuenta 1", internal_ref="c1")
    with pytest.raises(AuthenticationError):
        gestor.get_access_token("c1")


def test_el_historial_no_guarda_secretos(database):
    auditoria = AuditService(database)
    auditoria.record(
        "Prueba",
        detail="Bearer abcdefghijklmnop",
        payload={"access_token": "secreto", "precio": 10},
    )
    entrada = auditoria.recent(limit=1)[0]
    assert "abcdefghijklmnop" not in (entrada.detail or "")


def test_pkce_genera_valores_distintos():
    from lot_bot.wallapop.oauth import generate_pkce_pair

    v1, c1 = generate_pkce_pair()
    v2, c2 = generate_pkce_pair()
    assert v1 != v2 and c1 != c2
    assert len(c1) == 43  # SHA-256 en base64url sin relleno


def test_el_repositorio_no_contiene_secretos():
    """Ningun fichero versionado debe llevar credenciales reales."""
    import re
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    # Los patrones NO deben cruzar saltos de linea: `CLAVE=` seguida de una
    # linea nueva es un ejemplo vacio, no un secreto.
    patrones = [
        re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
        re.compile(r"WALLAPOP_CLIENT_SECRET[ \t]*=[ \t]*[^\s\"'#]{8,}"),
        re.compile(r"ANTHROPIC_API_KEY[ \t]*=[ \t]*[^\s\"'#]{8,}"),
        re.compile(r"LOT_BOT_MASTER_KEY[ \t]*=[ \t]*[^\s\"'#]{8,}"),
    ]
    revisados = 0
    extensiones = ("*.py", "*.yaml", "*.yml", "*.md", "*.ps1", "*.sh", "*.bat", "*.spec", "*.txt", "*.toml", "*.example")
    ficheros = [f for patron in extensiones for f in raiz.rglob(patron)]
    for fichero in ficheros:
        if any(parte in {".venv", ".git", "node_modules"} for parte in fichero.parts):
            continue
        if fichero == Path(__file__).resolve():
            continue  # este fichero contiene claves falsas a proposito
        texto = fichero.read_text(encoding="utf-8", errors="ignore")
        revisados += 1
        for patron in patrones:
            assert not patron.search(texto), f"Posible secreto en {fichero}"
    assert revisados > 10


def test_no_hay_ficheros_env_versionados():
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    assert not (raiz / ".env").exists() or ".env" in (raiz / ".gitignore").read_text()


def test_gitignore_cubre_los_ficheros_sensibles():
    from pathlib import Path

    reglas = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    for obligatorio in (".env", "*.local.yaml", "*.db", "*.key", "logs/", ".venv-build/", "dist/"):
        assert obligatorio in reglas, f"Falta «{obligatorio}» en .gitignore"


def test_la_ia_no_recibe_credenciales(app):
    """El prompt del sistema no debe contener ningún secreto."""
    from lot_bot.wallapop.auth import AuthCredential, AuthKind

    app.accounts.store_credential(
        "demo-1",
        AuthCredential(kind=AuthKind.SESSION_HANDOFF, headers={"Authorization": "Bearer SECRETO-IA-123"}),
    )
    prompt = app.agent._system_prompt()
    assert "SECRETO-IA-123" not in prompt
    esquemas = str(app.agent._tool_schemas())
    assert "SECRETO-IA-123" not in esquemas
