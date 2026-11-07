"""QueueManager: la cola persistente de anuncios del motor directo.

LA COLA NUNCA SE PIERDE
-----------------------
Cada anuncio es una fila de `ad_queue_items` y cada cambio de estado se
confirma en disco al momento. Ningún error borra un anuncio:

    error de autenticación  -> vuelve a PENDING (sin gastar intento) y espera
                               a que la cuenta se reconecte
    error de red            -> PENDING con espera creciente, o VERIFYING si la
                               petición pudo llegar a Wallapop
    cierre inesperado       -> al arrancar, PROCESSING pasa a VERIFYING
    datos rechazados        -> FAILED, con el motivo, para corregir y reintentar

DUPLICADOS
----------
Cada anuncio tiene una huella (cuenta + título + precio + descripción
normalizados). No se admite en la cola un anuncio con la misma huella que
otro pendiente o ya publicado, ni uno idéntico a un anuncio ya publicado en
esa cuenta. Además, una publicación interrumpida no se repite a ciegas: se
comprueba antes en Wallapop (VERIFYING) o se deja para que decida el usuario
(NEEDS_REVIEW).
"""

from __future__ import annotations

import csv
import hashlib
import logging
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from lot_bot.core.text import fold
from lot_bot.database.models import (
    Account,
    AdQueueEvent,
    AdQueueItem,
    AdQueueStatus,
    Listing,
    ListingStatus,
)
from lot_bot.integration.database_manager import DatabaseManager

logger = logging.getLogger(__name__)

MAX_TITLE = 200
MAX_DESCRIPTION = 10_000
DEFAULT_MAX_ATTEMPTS = 3

#: Estados en los que un anuncio «ocupa» su huella (no se admite otro igual).
ACTIVE_STATES = (
    AdQueueStatus.PENDING,
    AdQueueStatus.PROCESSING,
    AdQueueStatus.VERIFYING,
    AdQueueStatus.NEEDS_REVIEW,
    AdQueueStatus.PUBLISHED,
)

STATUS_LABELS = {
    AdQueueStatus.PENDING: "Pendiente",
    AdQueueStatus.PROCESSING: "Publicando",
    AdQueueStatus.VERIFYING: "Comprobando",
    AdQueueStatus.NEEDS_REVIEW: "Revisar",
    AdQueueStatus.PUBLISHED: "Publicado",
    AdQueueStatus.FAILED: "Error",
    AdQueueStatus.CANCELLED: "Cancelado",
}

#: Cabeceras admitidas en el CSV (en español, sin acentos ni mayúsculas).
CSV_COLUMNS = {
    "cuenta": "account",
    "titulo": "title",
    "descripcion": "description",
    "precio": "price",
    "categoria": "category",
    "estado": "condition",
    "fotos": "images",
    "prioridad": "priority",
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def fingerprint(account_ref: str, title: str, price: float, description: str = "") -> str:
    """Huella del contenido de un anuncio en una cuenta."""
    basis = "|".join(
        [account_ref, fold(title), f"{float(price):.2f}", fold(description)[:2000]]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class AdInput:
    """Datos de un anuncio a encolar."""

    account_ref: str
    title: str
    description: str
    price: float
    category: str | None = None
    condition: str | None = None
    image_paths: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    currency: str = "EUR"
    priority: int = 100
    batch: str | None = None
    source: str = "manual"
    product_id: int | None = None


@dataclass(slots=True)
class AddResult:
    added: list[int] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"{len(self.added)} añadido(s) a la cola"]
        if self.duplicates:
            parts.append(f"{len(self.duplicates)} duplicado(s) omitido(s)")
        if self.invalid:
            parts.append(f"{len(self.invalid)} con errores")
        return ", ".join(parts) + "."


@dataclass(slots=True)
class ClaimedItem:
    """Copia desconectada de la base de datos de lo que el motor va a publicar."""

    id: int
    account_ref: str
    title: str
    description: str
    price: float
    currency: str
    category: str | None
    condition: str | None
    attributes: dict[str, Any]
    image_paths: list[str]
    uploaded_images: dict[str, str]
    idempotency_key: str
    attempts: int
    max_attempts: int
    status: AdQueueStatus
    product_id: int | None


class QueueManager:
    """Altas, estados, historial y recuperación de la cola persistente."""

    def __init__(
        self,
        db: DatabaseManager,
        account_resolver: Callable[[str], str | None] | None = None,
        image_validator: Callable[[list[str]], list[str]] | None = None,
    ) -> None:
        self._db = db
        self._lock = threading.RLock()
        #: Traduce «Cuenta 1», un alias o un id a la referencia interna.
        self._resolve = account_resolver or (lambda value: value)
        self._validate_images = image_validator
        self.listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Altas
    # ------------------------------------------------------------------
    def validate(self, ad: AdInput) -> list[str]:
        errors: list[str] = []
        if not (ad.title or "").strip():
            errors.append("Falta el título.")
        elif len(ad.title.strip()) > MAX_TITLE:
            errors.append(f"El título supera {MAX_TITLE} caracteres.")
        if not (ad.description or "").strip():
            errors.append("Falta la descripción.")
        elif len(ad.description) > MAX_DESCRIPTION:
            errors.append(f"La descripción supera {MAX_DESCRIPTION} caracteres.")
        try:
            price = float(ad.price)
            if price <= 0:
                errors.append("El precio debe ser mayor que 0.")
        except (TypeError, ValueError):
            errors.append("El precio no es un número.")
        if not ad.account_ref:
            errors.append("Falta la cuenta.")
        if ad.image_paths and self._validate_images is not None:
            errors.extend(self._validate_images(ad.image_paths))
        return errors

    def add(self, ad: AdInput, *, allow_duplicates: bool = False) -> AddResult:
        return self.add_many([ad], allow_duplicates=allow_duplicates)

    def add_many(self, ads: Iterable[AdInput], *, allow_duplicates: bool = False) -> AddResult:
        result = AddResult()
        with self._lock, self._db.session() as session:
            seen_now: set[str] = set()
            for index, ad in enumerate(ads, start=1):
                label = f"#{index} «{(ad.title or '').strip()[:60]}»"
                resolved = self._resolve(ad.account_ref) if ad.account_ref else None
                if not resolved:
                    result.invalid.append(f"{label}: la cuenta «{ad.account_ref}» no existe.")
                    continue
                ad.account_ref = resolved
                errors = self.validate(ad)
                if errors:
                    result.invalid.append(f"{label}: {' '.join(errors)}")
                    continue
                title = ad.title.strip()
                description = ad.description.strip()
                price = round(float(ad.price), 2)
                print_ = fingerprint(resolved, title, price, description)
                if not allow_duplicates:
                    reason = self._duplicate_reason(session, resolved, print_, title, price)
                    if reason or print_ in seen_now:
                        result.duplicates.append(f"{label}: {reason or 'repetido en esta importación.'}")
                        continue
                seen_now.add(print_)
                item = AdQueueItem(
                    account_ref=resolved,
                    title=title[:MAX_TITLE],
                    description=description,
                    price=price,
                    currency=ad.currency or "EUR",
                    category=(ad.category or None),
                    condition=(ad.condition or None),
                    attributes=dict(ad.attributes or {}),
                    image_paths=list(ad.image_paths or []),
                    uploaded_images={},
                    fingerprint=print_,
                    idempotency_key=uuid.uuid4().hex,
                    status=AdQueueStatus.PENDING,
                    priority=int(ad.priority or 100),
                    max_attempts=DEFAULT_MAX_ATTEMPTS,
                    batch=ad.batch,
                    source=ad.source,
                    product_id=ad.product_id,
                )
                session.add(item)
                session.flush()
                session.add(AdQueueEvent(item_id=item.id, kind="queued", message=ad.source))
                result.added.append(item.id)
        self._notify()
        return result

    @staticmethod
    def _duplicate_reason(session, account_ref: str, print_: str, title: str, price: float) -> str:
        existing = session.scalar(
            select(AdQueueItem.status).where(
                AdQueueItem.fingerprint == print_, AdQueueItem.status.in_(ACTIVE_STATES)
            )
        )
        if existing is not None:
            return f"ya está en la cola ({STATUS_LABELS[existing].lower()})."
        # ¿Ya hay un anuncio activo idéntico en esa cuenta?
        rows = session.execute(
            select(Listing.title, Listing.price)
            .join(Account, Listing.account_id == Account.id)
            .where(
                Account.internal_ref == account_ref,
                Listing.status.in_([ListingStatus.ACTIVE, ListingStatus.PENDING]),
                Listing.price == price,
            )
        ).all()
        wanted = fold(title)
        if any(fold(t) == wanted for t, _p in rows):
            return "ya hay un anuncio igual publicado en esa cuenta."
        return ""

    # ------------------------------------------------------------------
    def import_csv(
        self,
        path: str | Path,
        default_account: str | None = None,
        *,
        allow_duplicates: bool = False,
    ) -> AddResult:
        """Importa anuncios desde un CSV (separado por «;» o «,»).

        Columnas: cuenta, titulo, descripcion, precio, categoria, estado,
        fotos (rutas separadas por «|»), prioridad. Solo titulo, descripcion y
        precio son obligatorias; sin «cuenta» se usa `default_account`.
        Las rutas relativas de las fotos se buscan junto al CSV.
        """
        file_path = Path(path)
        text = file_path.read_text(encoding="utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else ",", delimiters=";,\t")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ";"
        reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
        ads: list[AdInput] = []
        bad_rows: list[str] = []
        for line, row in enumerate(reader, start=2):
            data = {CSV_COLUMNS.get(fold(k or "").replace(" ", ""), ""): (v or "").strip()
                    for k, v in row.items()}
            account = data.get("account") or default_account or ""
            raw_price = data.get("price", "").replace("€", "").replace(" ", "")
            if "," in raw_price and "." in raw_price:
                raw_price = raw_price.replace(".", "").replace(",", ".")
            else:
                raw_price = raw_price.replace(",", ".")
            try:
                price = float(raw_price)
            except ValueError:
                bad_rows.append(f"Línea {line}: precio no válido «{data.get('price', '')}».")
                continue
            images = []
            for chunk in (data.get("images") or "").split("|"):
                chunk = chunk.strip()
                if chunk:
                    image = Path(chunk).expanduser()
                    if not image.is_absolute():
                        image = file_path.parent / image
                    images.append(str(image))
            try:
                priority = int(data.get("priority") or 100)
            except ValueError:
                priority = 100
            ads.append(
                AdInput(
                    account_ref=account,
                    title=data.get("title", ""),
                    description=data.get("description", ""),
                    price=price,
                    category=data.get("category") or None,
                    condition=data.get("condition") or None,
                    image_paths=images,
                    priority=priority,
                    batch=file_path.name,
                    source="csv",
                )
            )
        result = self.add_many(ads, allow_duplicates=allow_duplicates)
        result.invalid = bad_rows + result.invalid
        return result

    # ------------------------------------------------------------------
    # Estados (los usa el motor)
    # ------------------------------------------------------------------
    def claim_next(self, usable_account: Callable[[str], bool]) -> ClaimedItem | None:
        """Marca como PROCESSING el siguiente anuncio que se puede publicar.

        Primero los que hay que comprobar (VERIFYING), después los pendientes
        por prioridad y antigüedad. Se saltan los de cuentas no usables y los
        que esperan su hora (`next_attempt_at`).
        """
        now = _now()
        with self._lock, self._db.session() as session:
            candidates = session.scalars(
                select(AdQueueItem)
                .where(AdQueueItem.status.in_([AdQueueStatus.VERIFYING, AdQueueStatus.PENDING]))
                .where(
                    (AdQueueItem.next_attempt_at.is_(None)) | (AdQueueItem.next_attempt_at <= now)
                )
                .order_by(
                    (AdQueueItem.status == AdQueueStatus.PENDING),  # VERIFYING primero
                    AdQueueItem.priority,
                    AdQueueItem.id,
                )
                .limit(500)
            ).all()
            checked: dict[str, bool] = {}
            for item in candidates:
                ref = item.account_ref
                if ref not in checked:
                    checked[ref] = bool(usable_account(ref))
                if not checked[ref]:
                    continue
                previous = item.status
                if previous is AdQueueStatus.PENDING:
                    item.status = AdQueueStatus.PROCESSING
                item.started_at = now
                session.add(
                    AdQueueEvent(
                        item_id=item.id,
                        kind="verifying" if previous is AdQueueStatus.VERIFYING else "processing",
                        message=f"Intento {item.attempts + 1}"
                        if previous is AdQueueStatus.PENDING
                        else "Comprobando si llegó a publicarse",
                    )
                )
                claimed = ClaimedItem(
                    id=item.id,
                    account_ref=ref,
                    title=item.title,
                    description=item.description,
                    price=item.price,
                    currency=item.currency,
                    category=item.category,
                    condition=item.condition,
                    attributes=dict(item.attributes or {}),
                    image_paths=list(item.image_paths or []),
                    uploaded_images=dict(item.uploaded_images or {}),
                    idempotency_key=item.idempotency_key,
                    attempts=item.attempts,
                    max_attempts=item.max_attempts,
                    status=previous,
                    product_id=item.product_id,
                )
                break
            else:
                claimed = None
        if claimed is not None:
            self._notify()
        return claimed

    def blocked_accounts(self) -> list[str]:
        """Cuentas con anuncios esperando (para explicar por qué no avanza)."""
        with self._db.session() as session:
            return sorted(
                session.scalars(
                    select(AdQueueItem.account_ref)
                    .where(AdQueueItem.status.in_([AdQueueStatus.PENDING, AdQueueStatus.VERIFYING]))
                    .distinct()
                ).all()
            )

    def next_wakeup(self) -> datetime | None:
        """Hora más próxima a la que un anuncio en espera vuelve a estar listo."""
        with self._db.session() as session:
            return session.scalar(
                select(func.min(AdQueueItem.next_attempt_at)).where(
                    AdQueueItem.status == AdQueueStatus.PENDING,
                    AdQueueItem.next_attempt_at.is_not(None),
                )
            )

    def record_upload(self, item_id: int, image_hash: str, remote: str) -> None:
        with self._lock, self._db.session() as session:
            item = session.get(AdQueueItem, item_id)
            uploaded = dict(item.uploaded_images or {})
            uploaded[image_hash] = remote
            item.uploaded_images = uploaded

    def mark_published(self, item_id: int, remote_id: str | None, url: str | None, note: str = "") -> None:
        self._update(
            item_id,
            "published",
            note or "Publicado en Wallapop",
            status=AdQueueStatus.PUBLISHED,
            remote_id=remote_id,
            remote_url=url,
            published_at=_now(),
            last_error=None,
            last_error_code=None,
            next_attempt_at=None,
        )

    def mark_retry(self, item_id: int, code: str, message: str, delay_seconds: float,
                   *, count_attempt: bool = True) -> AdQueueStatus:
        """Vuelve a PENDING con espera; si se agotan los intentos, FAILED."""
        with self._lock, self._db.session() as session:
            item = session.get(AdQueueItem, item_id)
            attempts = item.attempts + (1 if count_attempt else 0)
            exhausted = count_attempt and attempts >= item.max_attempts
        if exhausted:
            self.mark_failed(item_id, code, f"{message} (sin más reintentos automáticos)",
                             count_attempt=True)
            return AdQueueStatus.FAILED
        self._update(
            item_id,
            "retry",
            f"{message} · nuevo intento en {int(delay_seconds)} s",
            status=AdQueueStatus.PENDING,
            attempts=attempts,
            last_error=message[:1000],
            last_error_code=code[:80],
            next_attempt_at=_now() + timedelta(seconds=max(0.0, delay_seconds)),
        )
        return AdQueueStatus.PENDING

    def mark_waiting(self, item_id: int, code: str, message: str,
                     until: datetime | None = None) -> None:
        """Vuelve a PENDING sin gastar intento (autenticación, límites, pausa)."""
        self._update(
            item_id,
            "waiting",
            message,
            status=AdQueueStatus.PENDING,
            last_error=message[:1000],
            last_error_code=code[:80],
            next_attempt_at=until,
        )

    def release(self, item_id: int) -> None:
        """Devuelve a PENDING un anuncio que se iba a publicar (pausa, cierre)."""
        with self._lock, self._db.session() as session:
            item = session.get(AdQueueItem, item_id)
            if item is not None and item.status is AdQueueStatus.PROCESSING:
                item.status = AdQueueStatus.PENDING
                item.started_at = None
        self._notify()

    def account_ready(self, account_ref: str) -> int:
        """La cuenta se ha reconectado: sus anuncios que esperaban por la
        autenticación vuelven a estar listos ya. Devuelve cuántos."""
        count = 0
        with self._lock, self._db.session() as session:
            for item in session.scalars(
                select(AdQueueItem).where(
                    AdQueueItem.account_ref == account_ref,
                    AdQueueItem.status == AdQueueStatus.PENDING,
                    AdQueueItem.last_error_code == "RECONECTAR",
                )
            ).all():
                item.next_attempt_at = None
                count += 1
        if count:
            self._notify()
        return count

    def defer_verification(self, item_id: int, message: str, delay_seconds: float) -> int:
        """Sigue en VERIFYING, pero se vuelve a comprobar más tarde.

        Devuelve cuántas comprobaciones fallidas lleva.
        """
        with self._lock, self._db.session() as session:
            item = session.get(AdQueueItem, item_id)
            checks = int((item.attributes or {}).get("_comprobaciones", 0)) + 1
            item.attributes = {**(item.attributes or {}), "_comprobaciones": checks}
            item.next_attempt_at = _now() + timedelta(seconds=max(0.0, delay_seconds))
            item.last_error = message[:1000]
            session.add(AdQueueEvent(item_id=item_id, kind="verify_deferred", message=message[:1000]))
        self._notify()
        return checks

    def mark_verifying(self, item_id: int, message: str) -> None:
        with self._lock, self._db.session() as session:
            attempts = session.get(AdQueueItem, item_id).attempts + 1
        self._update(
            item_id,
            "verifying",
            message,
            status=AdQueueStatus.VERIFYING,
            attempts=attempts,
            last_error=message[:1000],
            last_error_code="SIN_CONFIRMAR",
            next_attempt_at=_now() + timedelta(seconds=30),
        )

    def mark_needs_review(self, item_id: int, message: str) -> None:
        self._update(
            item_id,
            "needs_review",
            message,
            status=AdQueueStatus.NEEDS_REVIEW,
            last_error=message[:1000],
            last_error_code="REVISAR",
            next_attempt_at=None,
        )

    def mark_failed(self, item_id: int, code: str, message: str, *, count_attempt: bool = True) -> None:
        with self._lock, self._db.session() as session:
            attempts = session.get(AdQueueItem, item_id).attempts + (1 if count_attempt else 0)
        self._update(
            item_id,
            "failed",
            message,
            status=AdQueueStatus.FAILED,
            attempts=attempts,
            last_error=message[:1000],
            last_error_code=code[:80],
            next_attempt_at=None,
        )

    def _update(self, item_id: int, kind: str, message: str, **fields: Any) -> None:
        with self._lock, self._db.session() as session:
            item = session.get(AdQueueItem, item_id)
            if item is None:
                raise ValueError(f"No existe el anuncio {item_id} de la cola.")
            for key, value in fields.items():
                setattr(item, key, value)
            session.add(AdQueueEvent(item_id=item_id, kind=kind, message=(message or "")[:1000]))
        self._notify()

    # ------------------------------------------------------------------
    # Acciones del usuario
    # ------------------------------------------------------------------
    def retry(self, item_ids: list[int]) -> int:
        """Vuelve a poner en cola anuncios con error, cancelados o a revisar."""
        count = 0
        with self._lock, self._db.session() as session:
            for item in session.scalars(select(AdQueueItem).where(AdQueueItem.id.in_(item_ids))):
                if item.status not in (
                    AdQueueStatus.FAILED,
                    AdQueueStatus.CANCELLED,
                    AdQueueStatus.NEEDS_REVIEW,
                ):
                    continue
                item.status = AdQueueStatus.PENDING
                item.attempts = 0
                item.next_attempt_at = None
                item.last_error = None
                item.last_error_code = None
                session.add(AdQueueEvent(item_id=item.id, kind="requeued", message="Reintento manual"))
                count += 1
        self._notify()
        return count

    def retry_all_failed(self) -> int:
        with self._db.session() as session:
            ids = list(session.scalars(
                select(AdQueueItem.id).where(AdQueueItem.status == AdQueueStatus.FAILED)
            ))
        return self.retry(ids)

    def mark_reviewed_as_published(self, item_id: int) -> None:
        """El usuario confirma que un anuncio a revisar SÍ se publicó."""
        with self._db.session() as session:
            item = session.get(AdQueueItem, item_id)
            if item is None or item.status not in (AdQueueStatus.NEEDS_REVIEW, AdQueueStatus.VERIFYING):
                raise ValueError("Solo se pueden confirmar anuncios pendientes de revisión.")
        self.mark_published(item_id, None, None, "Confirmado por el usuario")

    def cancel(self, item_ids: list[int]) -> int:
        count = 0
        with self._lock, self._db.session() as session:
            for item in session.scalars(select(AdQueueItem).where(AdQueueItem.id.in_(item_ids))):
                if item.status in (AdQueueStatus.PENDING, AdQueueStatus.FAILED,
                                   AdQueueStatus.NEEDS_REVIEW):
                    item.status = AdQueueStatus.CANCELLED
                    session.add(AdQueueEvent(item_id=item.id, kind="cancelled", message="Por el usuario"))
                    count += 1
        self._notify()
        return count

    def remove(self, item_ids: list[int]) -> int:
        """Borra de la cola anuncios cancelados o con error (nunca publicados)."""
        count = 0
        with self._lock, self._db.session() as session:
            for item in session.scalars(select(AdQueueItem).where(AdQueueItem.id.in_(item_ids))):
                if item.status in (AdQueueStatus.CANCELLED, AdQueueStatus.FAILED):
                    session.delete(item)
                    count += 1
        self._notify()
        return count

    # ------------------------------------------------------------------
    # Recuperación tras un cierre inesperado
    # ------------------------------------------------------------------
    def recover_interrupted(self) -> int:
        """Lo que estaba «publicando» al cerrarse se comprueba antes de repetir."""
        count = 0
        with self._lock, self._db.session() as session:
            for item in session.scalars(
                select(AdQueueItem).where(AdQueueItem.status == AdQueueStatus.PROCESSING)
            ).all():
                item.status = AdQueueStatus.VERIFYING
                item.next_attempt_at = None
                item.last_error = "LOT Bot se cerró mientras se publicaba este anuncio."
                item.last_error_code = "INTERRUMPIDO"
                session.add(
                    AdQueueEvent(
                        item_id=item.id,
                        kind="interrupted",
                        message="Interrumpido: se comprobará si llegó a publicarse.",
                    )
                )
                count += 1
        if count:
            logger.info("Cola: %d anuncio(s) interrumpido(s) se comprobarán antes de repetir.", count)
        return count

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------
    def counts(self, account_ref: str | None = None) -> dict[str, int]:
        with self._db.session() as session:
            query = select(AdQueueItem.status, func.count()).group_by(AdQueueItem.status)
            if account_ref:
                query = query.where(AdQueueItem.account_ref == account_ref)
            raw = {status: n for status, n in session.execute(query).all()}
        counts = {s.value: raw.get(s, 0) for s in AdQueueStatus}
        counts["total"] = sum(raw.values())
        counts["pendientes"] = counts["pending"] + counts["processing"] + counts["verifying"]
        counts["errores"] = counts["failed"] + counts["needs_review"]
        return counts

    def published_today(self, account_ref: str) -> int:
        start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        with self._db.session() as session:
            return int(session.scalar(
                select(func.count()).select_from(AdQueueItem).where(
                    AdQueueItem.account_ref == account_ref,
                    AdQueueItem.status == AdQueueStatus.PUBLISHED,
                    AdQueueItem.published_at >= start,
                )
            ) or 0)

    def items(self, status: AdQueueStatus | None = None, limit: int = 500,
              account_ref: str | None = None) -> list[dict[str, Any]]:
        with self._db.session() as session:
            query = select(AdQueueItem).order_by(AdQueueItem.id.desc()).limit(limit)
            if status is not None:
                query = query.where(AdQueueItem.status == status)
            if account_ref:
                query = query.where(AdQueueItem.account_ref == account_ref)
            return [self._to_dict(i) for i in session.scalars(query).all()]

    def get(self, item_id: int) -> dict[str, Any] | None:
        with self._db.session() as session:
            item = session.get(AdQueueItem, item_id)
            return self._to_dict(item) if item else None

    def history(self, item_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._db.session() as session:
            query = (
                select(AdQueueEvent, AdQueueItem.title, AdQueueItem.account_ref)
                .join(AdQueueItem, AdQueueEvent.item_id == AdQueueItem.id)
                .order_by(AdQueueEvent.id.desc())
                .limit(limit)
            )
            if item_id is not None:
                query = query.where(AdQueueEvent.item_id == item_id)
            return [
                {
                    "fecha": event.at,
                    "anuncio_id": event.item_id,
                    "anuncio": title,
                    "cuenta_ref": ref,
                    "evento": event.kind,
                    "detalle": event.message,
                }
                for event, title, ref in session.execute(query).all()
            ]

    @staticmethod
    def _to_dict(item: AdQueueItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "cuenta_ref": item.account_ref,
            "titulo": item.title,
            "descripcion": item.description,
            "precio": item.price,
            "categoria": item.category,
            "estado_producto": item.condition,
            "fotos": list(item.image_paths or []),
            "estado": item.status.value,
            "estado_texto": STATUS_LABELS[item.status],
            "intentos": item.attempts,
            "error": item.last_error,
            "codigo_error": item.last_error_code,
            "id_wallapop": item.remote_id,
            "url": item.remote_url,
            "lote": item.batch,
            "creado": item.created_at,
            "publicado": item.published_at,
            "proximo_intento": item.next_attempt_at,
        }

    def _notify(self) -> None:
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:
                logger.debug("Aviso de la cola fallido", exc_info=True)
