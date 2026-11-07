"""Utilidades de texto en espanol.

Las busquedas deben funcionar aunque el usuario escriba «canapé» y el anuncio
diga «canape» (o al reves): en castellano es lo habitual.
"""

from __future__ import annotations

import re
import unicodedata


def fold(value: str | None) -> str:
    """Minusculas, sin acentos y con espacios normalizados."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def contains(haystack: str | None, needle: str | None) -> bool:
    """True si `needle` aparece en `haystack`, ignorando acentos y mayusculas."""
    folded_needle = fold(needle)
    if not folded_needle:
        return True
    return folded_needle in fold(haystack)
