"""Catalogo de tareas automatizables.

Principio: una automatizacion NUNCA publica, modifica ni elimina nada en
Wallapop sin que el usuario lo haya autorizado explicitamente al activarla.
Las tareas marcadas `writes=True` solo se ejecutan si el usuario desactiva
`requires_confirmation` de forma consciente desde la pantalla de
Automatizaciones; en caso contrario preparan el trabajo y lo dejan pendiente
de revision.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from lot_bot.bootstrap import Application

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class JobResult:
    """Resultado de una ejecucion."""

    ok: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    pending_review: int = 0


@dataclass(slots=True)
class JobDefinition:
    """Definicion de una tarea programable."""

    key: str
    name: str
    description: str
    default_interval_minutes: int
    writes: bool
    run: Callable[[Application, dict[str, Any]], JobResult]

    @property
    def safety_note(self) -> str:
        if not self.writes:
            return "Solo lectura: no modifica nada en Wallapop."
        return "Modifica información en Wallapop: requiere autorización explícita."


# ---------------------------------------------------------------------------
# Implementacion de las tareas
# ---------------------------------------------------------------------------
def _connected_accounts(app: Application) -> list[str]:
    return [a.internal_ref for a in app.accounts.list_accounts() if a.is_connected]


def job_sync_listings(app: Application, options: dict[str, Any]) -> JobResult:
    """Descarga los anuncios de todas las cuentas conectadas."""
    refs = _connected_accounts(app)
    if not refs:
        return JobResult(False, "No hay ninguna cuenta conectada.")
    results = app.listings.sync_all(refs)
    total_new = sum(r.get("nuevos", 0) for r in results.values())
    total_updated = sum(r.get("actualizados", 0) for r in results.values())
    failures = [ref for ref, r in results.items() if r.get("error")]
    return JobResult(
        ok=not failures,
        summary=f"{total_new} anuncios nuevos, {total_updated} actualizados en {len(refs)} cuenta(s).",
        details={"por_cuenta": results, "fallos": failures},
    )


def job_sync_messages(app: Application, options: dict[str, Any]) -> JobResult:
    """Descarga las conversaciones nuevas."""
    if not app.messages.messaging_available:
        return JobResult(False, "La mensajería no está disponible con los permisos actuales.")
    refs = _connected_accounts(app)
    results = app.messages.sync_all(refs)
    new_messages = sum(r.get("mensajes", 0) for r in results.values())
    return JobResult(
        ok=True,
        summary=f"{new_messages} mensajes nuevos en {len(refs)} cuenta(s).",
        details={"por_cuenta": results},
    )


def job_quality_review(app: Application, options: dict[str, Any]) -> JobResult:
    """Revisa la calidad de los anuncios publicados y avisa de incidencias."""
    reports = app.listings.quality_reports()
    blocked = [r for r in reports if not r.can_publish]
    warnings = [r for r in reports if r.can_publish and r.warnings]
    return JobResult(
        ok=True,
        summary=f"{len(reports)} anuncios revisados: {len(blocked)} con errores, {len(warnings)} con avisos.",
        details={
            "con_errores": [r.subject for r in blocked[:50]],
            "con_avisos": [r.subject for r in warnings[:50]],
        },
        pending_review=len(blocked),
    )


def job_catalog_quality(app: Application, options: dict[str, Any]) -> JobResult:
    """Revisa los productos del catalogo con informacion incompleta."""
    reports = app.catalog.validate_all()
    incomplete = [r for r in reports if not r.can_publish]
    return JobResult(
        ok=True,
        summary=f"{len(reports)} productos revisados: {len(incomplete)} incompletos.",
        details={"incompletos": [r.subject for r in incomplete[:50]]},
        pending_review=len(incomplete),
    )


def job_detect_duplicates(app: Application, options: dict[str, Any]) -> JobResult:
    """Busca anuncios y productos duplicados. No elimina nada."""
    listing_groups = app.listings.find_duplicates()
    product_groups = app.catalog.find_duplicate_products()
    total = len(listing_groups) + len(product_groups)
    return JobResult(
        ok=True,
        summary=f"{total} grupo(s) de duplicados detectados (no se elimina nada automáticamente).",
        details={
            "anuncios": [g.describe() for g in listing_groups[:30]],
            "productos": [g.describe() for g in product_groups[:30]],
        },
        pending_review=total,
    )


def job_inventory_sync(app: Application, options: dict[str, Any]) -> JobResult:
    """Compara el stock del catalogo con los anuncios activos."""
    from lot_bot.catalog.service import ProductFilter

    products = app.catalog.list_products(ProductFilter(limit=2000))
    out_of_stock_published = [
        p for p in products if p.stock <= 0 and p.status == "published"
    ]
    return JobResult(
        ok=True,
        summary=(
            f"{len(products)} productos revisados. "
            f"{len(out_of_stock_published)} publicados sin stock."
        ),
        details={"publicados_sin_stock": [p.sku for p in out_of_stock_published[:50]]},
        pending_review=len(out_of_stock_published),
    )


def job_prepare_publications(app: Application, options: dict[str, Any]) -> JobResult:
    """Prepara vistas previas de los productos listos, SIN publicarlos."""
    from lot_bot.catalog.service import ProductFilter
    from lot_bot.database.models import ProductStatus

    products = app.catalog.list_products(ProductFilter(status=ProductStatus.READY, limit=200))
    ready = 0
    blocked: list[str] = []
    for product in products:
        if not product.accounts:
            blocked.append(f"{product.sku}: sin cuentas asignadas")
            continue
        try:
            previews = app.publishing.build_previews(product.id)
        except ValueError as exc:
            blocked.append(f"{product.sku}: {exc}")
            continue
        if all(p.can_publish for p in previews):
            ready += 1
        else:
            reasons = {
                issue.message
                for preview in previews
                for issue in (preview.quality.errors if preview.quality else [])
            }
            blocked.append(f"{product.sku}: {'; '.join(sorted(reasons))[:180]}")
    return JobResult(
        ok=True,
        summary=f"{ready} producto(s) listos para publicar; {len(blocked)} necesitan revisión.",
        details={"bloqueados": blocked[:50]},
        pending_review=len(blocked),
    )


def job_master_ad_review(app: Application, options: dict[str, Any]) -> JobResult:
    """Revisa el anuncio principal: si se puede publicar y en qué cuentas falta.

    Solo lee. Publicar sigue requiriendo que el usuario lo confirme.
    """
    master = app.master_ads.get()
    if master is None:
        return JobResult(False, "No hay ningún anuncio principal configurado.")
    demo = app.demo_mode
    refs = [
        a.internal_ref
        for a in app.accounts.list_accounts()
        if a.is_connected and (demo or not a.is_demo)
    ]
    previews = app.master_ads.build_previews(master.key, refs) if refs else []
    blocked = sorted(
        {
            i.message
            for p in previews
            if not p.can_publish
            for i in (p.quality.errors if p.quality else [])
        }
    )
    published_in = {
        p.account_ref for p in app.master_ads.publications(master.key) if p.status == "active"
    }
    missing = [ref for ref in refs if ref not in published_in]
    parts = [f"{len(published_in)} cuenta(s) con el anuncio activo"]
    if missing:
        parts.append(f"{len(missing)} sin él")
    if blocked:
        parts.append("no se puede publicar: " + "; ".join(blocked))
    return JobResult(
        ok=not blocked,
        summary=f"Anuncio principal: {', '.join(parts)}.",
        details={"cuentas_sin_anuncio": missing, "bloqueos": blocked},
        pending_review=len(missing) + len(blocked),
    )


def job_error_review(app: Application, options: dict[str, Any]) -> JobResult:
    """Revisa los errores recientes del historial."""
    entries = app.audit.recent(limit=200, only_errors=True)
    return JobResult(
        ok=not entries,
        summary=f"{len(entries)} error(es) registrados recientemente.",
        details={"ultimos": [f"{e.action}: {e.error}" for e in entries[:20]]},
        pending_review=len(entries),
    )


JOB_DEFINITIONS: list[JobDefinition] = [
    JobDefinition(
        key="sync_listings",
        name="Sincronizar anuncios",
        description="Descarga el estado actual de los anuncios de cada cuenta.",
        default_interval_minutes=60,
        writes=False,
        run=job_sync_listings,
    ),
    JobDefinition(
        key="sync_messages",
        name="Sincronizar mensajes",
        description="Descarga las conversaciones y mensajes nuevos.",
        default_interval_minutes=15,
        writes=False,
        run=job_sync_messages,
    ),
    JobDefinition(
        key="quality_review",
        name="Revisar anuncios",
        description="Comprueba que los anuncios publicados no tengan información incompleta.",
        default_interval_minutes=720,
        writes=False,
        run=job_quality_review,
    ),
    JobDefinition(
        key="catalog_quality",
        name="Revisar catálogo",
        description="Detecta productos con información incorrecta o incompleta.",
        default_interval_minutes=720,
        writes=False,
        run=job_catalog_quality,
    ),
    JobDefinition(
        key="detect_duplicates",
        name="Comprobar duplicados",
        description="Busca productos, anuncios e imágenes repetidos. No elimina nada.",
        default_interval_minutes=1440,
        writes=False,
        run=job_detect_duplicates,
    ),
    JobDefinition(
        key="inventory_sync",
        name="Sincronizar inventario",
        description="Compara el stock del catálogo con los anuncios publicados.",
        default_interval_minutes=360,
        writes=False,
        run=job_inventory_sync,
    ),
    JobDefinition(
        key="prepare_publications",
        name="Preparar publicaciones",
        description="Genera las vistas previas de los productos listos. No publica nada.",
        default_interval_minutes=1440,
        writes=False,
        run=job_prepare_publications,
    ),
    JobDefinition(
        key="master_ad_review",
        name="Revisar anuncio principal",
        description=(
            "Comprueba si el anuncio de canapés se puede publicar y en qué cuentas no "
            "está activo. No publica nada."
        ),
        default_interval_minutes=720,
        writes=False,
        run=job_master_ad_review,
    ),
    JobDefinition(
        key="error_review",
        name="Revisar errores",
        description="Repasa los errores recientes y los resume en el historial.",
        default_interval_minutes=360,
        writes=False,
        run=job_error_review,
    ),
]

JOBS_BY_KEY: dict[str, JobDefinition] = {job.key: job for job in JOB_DEFINITIONS}
