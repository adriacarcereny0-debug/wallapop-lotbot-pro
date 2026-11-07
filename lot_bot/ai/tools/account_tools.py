"""Herramientas de cuentas (solo lectura: conectar/desconectar es manual)."""

from __future__ import annotations

from typing import Any

from lot_bot.ai.tools.base import Tool, ToolCategory, ToolContext, ToolResult, ok


def _get_account_status(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    accounts = context.app.accounts.list_accounts()
    wanted = args.get("cuenta")
    if wanted:
        ref = context.app.accounts.resolve_ref(wanted)
        accounts = [a for a in accounts if a.internal_ref == ref]
        if not accounts:
            return ToolResult(False, f"No se encuentra la cuenta '{wanted}'.")

    listings_by_account = context.app.listings.count_by_account()
    data = [
        {
            "referencia": a.internal_ref,
            "nombre": a.alias,
            "estado": a.status_label,
            "detalle": a.status_detail,
            "usuario_wallapop": a.wallapop_login,
            "ultima_sincronizacion": a.last_sync_at.strftime("%d/%m/%Y %H:%M") if a.last_sync_at else "nunca",
            "anuncios_locales": listings_by_account.get(a.internal_ref, 0),
            "demo": a.is_demo,
        }
        for a in accounts
    ]
    connected = sum(1 for a in accounts if a.is_connected)
    return ok(
        f"{len(accounts)} cuenta(s), {connected} conectada(s). "
        f"Modo actual: {context.app.backend_label}.",
        cuentas=data,
        modo=context.app.backend_label,
    )


def _list_capabilities(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    from lot_bot.wallapop.capabilities import CAPABILITY_LABELS, Capability

    granted = context.app.wallapop.capabilities()
    available = [CAPABILITY_LABELS[c] for c in Capability if c in granted]
    missing = [CAPABILITY_LABELS[c] for c in Capability if c not in granted]
    return ok(
        f"{len(available)} operaciones autorizadas y {len(missing)} no disponibles.",
        disponibles=available,
        no_disponibles=missing,
        backend=context.app.backend_label,
    )


ACCOUNT_TOOLS: list[Tool] = [
    Tool(
        name="get_account_status",
        description=(
            "Consulta el estado de las cuentas de Wallapop configuradas: si están "
            "conectadas, cuándo se sincronizaron por última vez y cuántos anuncios tienen."
        ),
        parameters={
            "properties": {
                "cuenta": {
                    "type": "string",
                    "description": "Nombre o referencia de una cuenta concreta. Omítelo para ver todas.",
                }
            },
            "required": [],
        },
        handler=_get_account_status,
        category=ToolCategory.ACCOUNTS,
    ),
    Tool(
        name="get_available_operations",
        description=(
            "Indica qué operaciones de Wallapop están autorizadas en esta instalación "
            "y cuáles no. Úsala cuando el usuario pida algo y dudes si es posible."
        ),
        parameters={"properties": {}, "required": []},
        handler=_list_capabilities,
        category=ToolCategory.ACCOUNTS,
    ),
]
