"""Configuracion del sistema de logging (consola + fichero rotatorio)."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from lot_bot.config.paths import get_paths
from lot_bot.logs.redaction import RedactingFilter, register_secret

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_configured = False


def get_log_file() -> Path:
    return get_paths().logs / "lot_bot.log"


def configure_logging(level: str = "INFO", secrets: dict[str, str] | None = None) -> Path:
    """Inicializa el logging global. Idempotente."""
    global _configured

    for value in (secrets or {}).values():
        register_secret(value)

    log_file = get_log_file()
    if _configured:
        logging.getLogger().setLevel(level)
        return log_file

    log_file.parent.mkdir(parents=True, exist_ok=True)
    redactor = RedactingFilter()
    formatter = logging.Formatter(_LOG_FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redactor)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console)

    # Librerias de terceros: solo avisos, para no llenar el fichero.
    for noisy in ("httpx", "httpcore", "apscheduler", "anthropic", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    logging.getLogger(__name__).info("Logging inicializado en %s", log_file)
    return log_file


def tail_log(lines: int = 300, path: Path | None = None) -> list[str]:
    """Devuelve las ultimas lineas del log, para la pantalla de Logs/errores."""
    log_file = path or get_log_file()
    if not log_file.is_file():
        return []
    with log_file.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.read().splitlines()[-lines:]
