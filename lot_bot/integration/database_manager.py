"""DatabaseManager: persistencia del motor de publicación directa.

Envuelve a `Database` (el mismo SQLite que usa el resto de LOT Bot) y añade
lo que necesita una cola que no puede perderse nunca:

* esquema garantizado al arrancar (`ensure_schema`),
* copias de seguridad consistentes con la API de copia de SQLite,
* comprobación de integridad,
* acceso a los ajustes clave/valor del motor.

Cada operación usa una sesión corta (`session_scope`) que se confirma al
salir: si el programa se cierra de golpe, lo último confirmado queda en disco
(SQLite en modo WAL).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from lot_bot.database.engine import Database
from lot_bot.database.models import Setting

logger = logging.getLogger(__name__)

#: Tablas propias del motor directo (se crean solas con el resto).
ENGINE_TABLES = (
    "ad_queue_items",
    "ad_queue_events",
    "wallapop_connections",
    "wallapop_connection_events",
)


class DatabaseManager:
    """Punto único de acceso a la base de datos para el motor directo."""

    def __init__(self, database: Database) -> None:
        self._db = database

    @property
    def database(self) -> Database:
        return self._db

    # ------------------------------------------------------------------
    def ensure_schema(self) -> None:
        """Crea las tablas que falten (idempotente, sin perder datos)."""
        self._db.create_all()
        missing = [t for t in ENGINE_TABLES if t not in self._db.table_names()]
        if missing:  # pragma: no cover - solo si create_all fallara en silencio
            raise RuntimeError(f"No se han podido crear las tablas: {', '.join(missing)}")

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Sesión transaccional: confirma al salir, deshace si hay error."""
        with self._db.session_scope() as session:
            yield session

    # ------------------------------------------------------------------
    # Ajustes del motor (clave/valor en la tabla `settings`)
    # ------------------------------------------------------------------
    def get_setting(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.session() as session:
            row = session.get(Setting, key)
            return dict(row.value or {}) if row else dict(default or {})

    def set_setting(self, key: str, value: dict[str, Any]) -> None:
        with self.session() as session:
            row = session.get(Setting, key)
            if row is None:
                session.add(Setting(key=key, value=dict(value)))
            else:
                row.value = dict(value)

    def update_setting(self, key: str, **changes: Any) -> dict[str, Any]:
        merged = {**self.get_setting(key), **changes}
        self.set_setting(key, merged)
        return merged

    # ------------------------------------------------------------------
    # Salud y copias
    # ------------------------------------------------------------------
    def integrity_ok(self) -> bool:
        """`PRAGMA integrity_check` (solo SQLite; otros motores: True)."""
        if not str(self._db.engine.url).startswith("sqlite"):
            return True
        with self._db.engine.connect() as connection:
            result = connection.execute(text("PRAGMA integrity_check")).scalar()
        return str(result).lower() == "ok"

    def backup(self, destination_dir: Path) -> Path | None:
        """Copia consistente de la base de datos (aunque esté en uso).

        Devuelve la ruta de la copia, o None si la base de datos es en memoria.
        """
        source = self._db.file_path
        if source is None or not source.is_file():
            return None
        destination_dir.mkdir(parents=True, exist_ok=True)
        target = destination_dir / f"lot_bot-{datetime.now():%Y%m%d-%H%M%S}.db"
        src = sqlite3.connect(str(source))
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        logger.info("Copia de seguridad de la base de datos: %s", target.name)
        return target
