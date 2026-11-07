"""Registro de eventos tecnicos, con redaccion automatica de secretos."""

from lot_bot.logs.redaction import RedactingFilter, register_secret
from lot_bot.logs.setup import configure_logging, get_log_file, tail_log

__all__ = [
    "configure_logging",
    "get_log_file",
    "tail_log",
    "RedactingFilter",
    "register_secret",
]
