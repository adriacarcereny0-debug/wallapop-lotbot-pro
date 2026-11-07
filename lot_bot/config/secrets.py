"""Almacen cifrado de secretos locales.

Los tokens de Wallapop se guardan cifrados con Fernet (AES-128-CBC + HMAC).
La clave maestra vive en el llavero del sistema operativo (Windows Credential
Manager / Keychain / Secret Service) y NUNCA en el repositorio ni en la base de
datos. Si el llavero no esta disponible se usa un fichero con permisos
restringidos dentro de la carpeta de datos del usuario.
"""

from __future__ import annotations

import base64
import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from lot_bot.config.paths import get_paths

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "LOT Bot"
KEYRING_USERNAME = "master-key"
_KEY_FILENAME = "master.key"


class SecretsError(RuntimeError):
    """Error al cifrar o descifrar un secreto local."""


def _keyring():
    try:
        import keyring

        return keyring
    except Exception:  # pragma: no cover - depende del sistema
        return None


def _read_key_from_keyring() -> str | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception as exc:  # pragma: no cover - backend no disponible
        logger.debug("Llavero del sistema no disponible: %s", type(exc).__name__)
        return None


def _write_key_to_keyring(key: str) -> bool:
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key)
        return True
    except Exception as exc:  # pragma: no cover - backend no disponible
        logger.debug("No se pudo escribir en el llavero: %s", type(exc).__name__)
        return False


def _key_file() -> Path:
    return get_paths().config / _KEY_FILENAME


def _read_key_from_file() -> str | None:
    path = _key_file()
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def _write_key_to_file(key: str) -> None:
    path = _key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:  # pragma: no cover - sistemas sin permisos POSIX
        pass


def _normalise_key(raw: str) -> bytes:
    """Acepta una clave Fernet valida o deriva una a partir de texto libre."""
    candidate = raw.strip().encode("utf-8")
    try:
        Fernet(candidate)
        return candidate
    except (ValueError, TypeError):
        digest = base64.urlsafe_b64encode(
            __import__("hashlib").sha256(candidate).digest()
        )
        return digest


def get_master_key() -> bytes:
    """Obtiene la clave maestra, creandola en el primer arranque."""
    env_key = os.environ.get("LOT_BOT_MASTER_KEY", "").strip()
    if env_key:
        return _normalise_key(env_key)

    stored = _read_key_from_keyring() or _read_key_from_file()
    if stored:
        return _normalise_key(stored)

    generated = Fernet.generate_key().decode("utf-8")
    if not _write_key_to_keyring(generated):
        _write_key_to_file(generated)
        logger.warning(
            "Llavero del sistema no disponible: la clave maestra se ha guardado "
            "en un fichero local con permisos restringidos."
        )
    return generated.encode("utf-8")


class SecretBox:
    """Cifra y descifra cadenas con la clave maestra local."""

    def __init__(self, key: bytes | None = None) -> None:
        self._fernet = Fernet(key or get_master_key())

    def encrypt(self, plaintext: str | None) -> str | None:
        if plaintext is None or plaintext == "":
            return None
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str | None) -> str | None:
        if not ciphertext:
            return None
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretsError(
                "No se ha podido descifrar un token guardado. "
                "Es probable que la clave maestra haya cambiado: vuelve a conectar la cuenta."
            ) from exc


_box: SecretBox | None = None


def get_secret_box() -> SecretBox:
    global _box
    if _box is None:
        _box = SecretBox()
    return _box


def reset_secret_box() -> None:
    """Fuerza a recrear la caja de secretos (tests / cambio de clave)."""
    global _box
    _box = None


def mask(value: str | None, visible: int = 4) -> str:
    """Enmascara un secreto para mostrarlo en pantalla o en un log."""
    if not value:
        return "(no configurado)"
    if len(value) <= visible:
        return "*" * len(value)
    return f"{'*' * (len(value) - visible)}{value[-visible:]}"
