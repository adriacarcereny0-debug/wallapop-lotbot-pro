"""Deteccion de duplicados: productos, anuncios e imagenes.

Criterios combinados (el primero que coincide manda):
  1. SKU identico            -> duplicado seguro
  2. Identificador Wallapop  -> mismo anuncio
  3. Hash de imagen          -> misma fotografia
  4. Titulo + caracteristicas -> similitud textual

LOT Bot NUNCA elimina nada automaticamente: devuelve grupos de candidatos
para que el usuario decida.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from rapidfuzz import fuzz

#: Umbral de similitud (0-100) a partir del cual dos titulos se consideran iguales.
TITLE_SIMILARITY_THRESHOLD = 88
#: Umbral mas bajo para marcar "parecidos" (aviso, no duplicado).
TITLE_SIMILAR_THRESHOLD = 75


class MatchReason(str, Enum):
    SKU = "sku"
    REMOTE_ID = "identificador_wallapop"
    IMAGE = "imagen"
    TITLE = "titulo"
    ATTRIBUTES = "caracteristicas"


@dataclass(slots=True)
class DuplicateGroup:
    """Conjunto de elementos considerados duplicados entre si."""

    reason: MatchReason
    key: str
    members: list[dict[str, Any]] = field(default_factory=list)
    confidence: int = 100

    @property
    def size(self) -> int:
        return len(self.members)

    def describe(self) -> str:
        labels = {
            MatchReason.SKU: "mismo SKU",
            MatchReason.REMOTE_ID: "mismo anuncio en Wallapop",
            MatchReason.IMAGE: "misma fotografía",
            MatchReason.TITLE: "título casi idéntico",
            MatchReason.ATTRIBUTES: "mismas características",
        }
        return f"{self.size} elementos con {labels[self.reason]} ({self.confidence}% de confianza)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "motivo": self.reason.value,
            "clave": self.key,
            "confianza": self.confidence,
            "elementos": self.members,
        }


def normalize_text(value: str | None) -> str:
    """Minusculas, sin acentos, sin signos y con espacios normalizados."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9x\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_similarity(a: str | None, b: str | None) -> int:
    """Similitud 0-100 entre dos titulos, tolerante al orden de las palabras.

    `token_set_ratio` da 100 cuando las palabras de un título están todas
    contenidas en el otro. Eso es útil («Canapé gris 135x190» frente a
    «Canapé 135x190 gris»), pero da falsos positivos enormes cuando un título
    tiene muy pocas palabras distintas: «Canapé canapé canapé» saldría igual a
    CUALQUIER anuncio que diga «canapé». Por eso solo se usa cuando los dos
    títulos comparten una proporción razonable de su vocabulario.
    """
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0
    sort_score = fuzz.token_sort_ratio(na, nb)
    words_a, words_b = set(na.split()), set(nb.split())
    coverage = min(len(words_a), len(words_b)) / max(len(words_a), len(words_b))
    if coverage < 0.6:
        return int(sort_score)
    return int(max(sort_score, fuzz.token_set_ratio(na, nb)))


def _group_by_key(
    records: list[dict[str, Any]], key_field: str, reason: MatchReason
) -> tuple[list[DuplicateGroup], set[int]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        value = record.get(key_field)
        if value in (None, ""):
            continue
        buckets.setdefault(str(value).strip().lower(), []).append(record)

    groups: list[DuplicateGroup] = []
    consumed: set[int] = set()
    for key, members in buckets.items():
        if len(members) > 1:
            groups.append(DuplicateGroup(reason=reason, key=key, members=members, confidence=100))
            consumed.update(id(m) for m in members)
    return groups, consumed


def find_duplicates(
    records: list[dict[str, Any]],
    *,
    title_threshold: int = TITLE_SIMILARITY_THRESHOLD,
    compare_attributes: tuple[str, ...] = ("medida", "color", "material"),
) -> list[DuplicateGroup]:
    """Agrupa registros duplicados.

    Cada registro es un diccionario con, al menos, `id` y `titulo`. Campos
    opcionales reconocidos: `sku`, `wallapop_item_id`, `image_hashes`,
    `caracteristicas`, `cuenta`.
    """
    groups: list[DuplicateGroup] = []

    for key_field, reason in (
        ("sku", MatchReason.SKU),
        ("wallapop_item_id", MatchReason.REMOTE_ID),
    ):
        found, _used = _group_by_key(records, key_field, reason)
        groups.extend(found)

    # --- Imagenes identicas ---
    image_buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        for image_hash in record.get("image_hashes") or []:
            if image_hash:
                image_buckets.setdefault(str(image_hash), []).append(record)
    for image_hash, members in image_buckets.items():
        unique = {id(m): m for m in members}
        if len(unique) > 1:
            groups.append(
                DuplicateGroup(
                    reason=MatchReason.IMAGE,
                    key=image_hash[:16],
                    members=list(unique.values()),
                    confidence=95,
                )
            )

    # --- Similitud de titulo + caracteristicas ---
    # Se compara sobre TODOS los registros (tambien los ya agrupados por SKU),
    # porque un anuncio sin SKU puede ser duplicado de otro que si lo tiene.
    # Despues se descartan los grupos que ya cubre otro criterio mas fiable.
    visited: set[int] = set()
    title_groups: list[DuplicateGroup] = []
    for index, record in enumerate(records):
        if id(record) in visited:
            continue
        cluster = [record]
        for other in records[index + 1 :]:
            if id(other) in visited:
                continue
            score = title_similarity(record.get("titulo"), other.get("titulo"))
            if score >= title_threshold and _attributes_match(record, other, compare_attributes):
                cluster.append(other)
                visited.add(id(other))
        if len(cluster) > 1:
            visited.add(id(record))
            title_groups.append(
                DuplicateGroup(
                    reason=MatchReason.TITLE,
                    key=normalize_text(record.get("titulo"))[:60],
                    members=cluster,
                    confidence=title_threshold,
                )
            )

    existing = [_member_keys(g) for g in groups]
    for group in title_groups:
        keys = _member_keys(group)
        if any(keys <= previous for previous in existing):
            continue
        groups.append(group)
    return groups


def _member_keys(group: DuplicateGroup) -> frozenset:
    """Identidad estable de los miembros de un grupo, para comparar grupos."""
    return frozenset(
        m.get("id") if m.get("id") is not None else id(m) for m in group.members
    )


def _attributes_match(
    a: dict[str, Any], b: dict[str, Any], keys: tuple[str, ...]
) -> bool:
    """Dos registros con caracteristicas contradictorias no son duplicados."""
    attrs_a = {k.lower(): normalize_text(v) for k, v in (a.get("caracteristicas") or {}).items()}
    attrs_b = {k.lower(): normalize_text(v) for k, v in (b.get("caracteristicas") or {}).items()}
    for key in keys:
        va, vb = attrs_a.get(key), attrs_b.get(key)
        if va and vb and va != vb:
            return False
    return True


def find_similar(
    records: list[dict[str, Any]],
    *,
    low: int = TITLE_SIMILAR_THRESHOLD,
    high: int = TITLE_SIMILARITY_THRESHOLD,
) -> list[tuple[dict[str, Any], dict[str, Any], int]]:
    """Parejas parecidas pero no identicas (para revision manual)."""
    pairs: list[tuple[dict[str, Any], dict[str, Any], int]] = []
    for index, record in enumerate(records):
        for other in records[index + 1 :]:
            score = title_similarity(record.get("titulo"), other.get("titulo"))
            if low <= score < high:
                pairs.append((record, other, score))
    return sorted(pairs, key=lambda item: item[2], reverse=True)


def find_duplicate_images(images: list[dict[str, Any]]) -> list[DuplicateGroup]:
    """Agrupa imagenes identicas por hash de contenido o hash perceptual."""
    groups: list[DuplicateGroup] = []
    for key_field, confidence in (("content_hash", 100), ("perceptual_hash", 92)):
        buckets: dict[str, list[dict[str, Any]]] = {}
        for image in images:
            value = image.get(key_field)
            if value:
                buckets.setdefault(str(value), []).append(image)
        for key, members in buckets.items():
            if len(members) > 1:
                groups.append(
                    DuplicateGroup(
                        reason=MatchReason.IMAGE,
                        key=key[:16],
                        members=members,
                        confidence=confidence,
                    )
                )
    return groups
