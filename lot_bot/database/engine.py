"""Motor de base de datos y gestion de sesiones."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from lot_bot.config.paths import get_paths
from lot_bot.database.models import Base

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5

#: Columnas añadidas después de la primera versión. Se crean solas al
#: arrancar en bases de datos antiguas, sin perder datos.
LIGHT_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "accounts": [
        ("auth_method", "VARCHAR(40)"),
        ("credential_enc", "TEXT"),
    ],
    "master_ads": [
        ("attributes", "JSON"),
        ("locked", "BOOLEAN NOT NULL DEFAULT 1"),
    ],
    "listings": [
        ("master_ad_id", "INTEGER REFERENCES master_ads(id) ON DELETE SET NULL"),
        ("overrides", "JSON"),
        ("url", "TEXT"),
        ("meta", "JSON"),
    ],
    "generated_images": [
        ("operation", "VARCHAR(40)"),
        ("scene", "JSON"),
        ("source_image", "TEXT"),
    ],
}


class Database:
    """Encapsula el engine de SQLAlchemy y la factoria de sesiones."""

    def __init__(self, url: str | None = None, echo: bool = False) -> None:
        self.url = url or get_paths().database_url
        self.engine: Engine = create_engine(
            self.url,
            echo=echo,
            future=True,
            connect_args={"check_same_thread": False} if self.url.startswith("sqlite") else {},
        )
        if self.url.startswith("sqlite"):
            event.listen(self.engine, "connect", _enable_sqlite_pragmas)
        self._session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, future=True
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._apply_light_migrations()
        logger.info("Esquema de base de datos listo (%d tablas)", len(Base.metadata.tables))

    def _apply_light_migrations(self) -> None:
        """Anade columnas nuevas a tablas que ya existian.

        `create_all` crea tablas nuevas pero no modifica las existentes. Para
        columnas opcionales basta con un ALTER TABLE, que SQLite acepta sin
        reescribir la tabla ni perder datos.
        """
        inspector = inspect(self.engine)
        tables = set(inspector.get_table_names())
        with self.engine.begin() as connection:
            for table, columns in LIGHT_MIGRATIONS.items():
                if table not in tables:
                    continue
                existing = {column["name"] for column in inspector.get_columns(table)}
                for name, sql_type in columns:
                    if name in existing:
                        continue
                    logger.info("Migración: añadiendo %s.%s", table, name)
                    connection.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
                    )

    def table_names(self) -> list[str]:
        return inspect(self.engine).get_table_names()

    def session(self) -> Session:
        return self._session_factory()

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """Sesion transaccional: commit al salir, rollback ante cualquier error."""
        session = self.session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()

    @property
    def file_path(self) -> Path | None:
        if self.url.startswith("sqlite:///"):
            return Path(self.url.replace("sqlite:///", "", 1))
        return None


def _enable_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


_database: Database | None = None


def get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
        _database.create_all()
    return _database


def set_database(database: Database | None) -> Database | None:
    """Sustituye la base de datos activa (tests, modo portable)."""
    global _database
    _database = database
    return _database


@contextmanager
def session_scope() -> Iterator[Session]:
    with get_database().session_scope() as session:
        yield session
