"""Filtro de logging que impide que un secreto llegue nunca al disco."""

from __future__ import annotations

import logging
import re
import threading

_lock = threading.Lock()
_secrets: set[str] = set()

# Patrones de secretos que pueden aparecer aunque no los hayamos registrado.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"), "sk-ant-***REDACTED***"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE), "Bearer ***REDACTED***"),
    (re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}"), "***JWT-REDACTED***"),
    (
        re.compile(r"((?:client_secret|access_token|refresh_token|api_key|x-key|x_key)\"?\s*[:=]\s*\"?)[^\s\",}]+", re.IGNORECASE),
        r"\1***REDACTED***",
    ),
]

MIN_SECRET_LENGTH = 6


def register_secret(value: str | None) -> None:
    """Registra un valor concreto que nunca debe aparecer en los logs."""
    if not value or len(value) < MIN_SECRET_LENGTH:
        return
    with _lock:
        _secrets.add(value)


def clear_secrets() -> None:
    with _lock:
        _secrets.clear()


def redact(text: str) -> str:
    """Devuelve el texto con todos los secretos conocidos sustituidos."""
    if not text:
        return text
    with _lock:
        known = tuple(_secrets)
    for secret in known:
        if secret in text:
            text = text.replace(secret, "***REDACTED***")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Aplica `redact` al mensaje y a los argumentos de cada registro."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Se formatea primero y se redacta el texto final: si se redactara
            # la plantilla («x-key: %s») se perderia el marcador y el registro
            # fallaria al formatearse.
            record.msg = redact(record.getMessage())
            record.args = None
        except Exception:  # pragma: no cover - el logging nunca debe romper la app
            return True
        return True
