"""Claves de API de servicios externos (p. ej. FLUX.2 Pro), guardadas cifradas.

Se reutiliza el almacén seguro del proyecto: la clave se cifra con la clave
maestra local (`SecretBox`, que vive en el llavero del sistema) y se guarda en
la base de datos local. No hace falta ningún `.env`.

Reglas:
  * La clave no aparece nunca en el código, en Git ni en los registros: al
    cargarla se registra en el filtro de redacción.
  * La interfaz solo la ve enmascarada, salvo que el usuario pulse «Mostrar».
"""

from __future__ import annotations

import logging

from lot_bot.config.secrets import SecretBox, SecretsError, get_secret_box
from lot_bot.database.engine import Database
from lot_bot.database.models import Setting
from lot_bot.logs.redaction import register_secret

logger = logging.getLogger(__name__)

_SETTING_KEY = "claves_api_cifradas"

#: Servicios admitidos. Solo los que la aplicación sabe usar.
FLUX = "flux"
KNOWN_SERVICES = {FLUX: "FLUX.2 Pro (Black Forest Labs)"}


class ApiKeyStore:
    def __init__(self, database: Database, secret_box: SecretBox | None = None) -> None:
        self._db = database
        self._box = secret_box

    @property
    def box(self) -> SecretBox:
        if self._box is None:
            self._box = get_secret_box()
        return self._box

    def _check(self, service: str) -> None:
        if service not in KNOWN_SERVICES:
            raise ValueError(f"Servicio desconocido: {service}")

    def get(self, service: str) -> str | None:
        self._check(service)
        with self._db.session_scope() as session:
            row = session.get(Setting, _SETTING_KEY)
            encrypted = (row.value or {}).get(service) if row else None
        if not encrypted:
            return None
        try:
            value = self.box.decrypt(encrypted)
        except SecretsError:
            logger.warning("No se ha podido descifrar la clave de %s.", KNOWN_SERVICES[service])
            return None
        if value:
            register_secret(value)
        return value

    def has(self, service: str) -> bool:
        return bool(self.get(service))

    def set(self, service: str, value: str) -> None:
        self._check(service)
        value = (value or "").strip()
        if not value:
            raise ValueError("La clave está vacía.")
        if any(c.isspace() for c in value):
            raise ValueError("La clave no puede contener espacios.")
        register_secret(value)
        encrypted = self.box.encrypt(value)
        with self._db.session_scope() as session:
            row = session.get(Setting, _SETTING_KEY)
            if row is None:
                session.add(Setting(key=_SETTING_KEY, value={service: encrypted}))
            else:
                row.value = {**(row.value or {}), service: encrypted}
        logger.info("Clave de %s guardada (cifrada).", KNOWN_SERVICES[service])

    def delete(self, service: str) -> bool:
        self._check(service)
        with self._db.session_scope() as session:
            row = session.get(Setting, _SETTING_KEY)
            if row is None or service not in (row.value or {}):
                return False
            row.value = {k: v for k, v in row.value.items() if k != service}
        logger.info("Clave de %s eliminada.", KNOWN_SERVICES[service])
        return True
