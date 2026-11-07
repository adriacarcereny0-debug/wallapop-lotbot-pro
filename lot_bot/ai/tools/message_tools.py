"""Herramientas de mensajeria y cierre de ventas."""

from __future__ import annotations

from typing import Any

from lot_bot.ai.tools.base import (
    Tool,
    ToolCategory,
    ToolContext,
    ToolResult,
    confirm_first,
    fail,
    ok,
)
from lot_bot.messages.sales_assistant import SalesAssistant, build_context_from_data


def _get_messages(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    if not context.app.messages.messaging_available:
        return ToolResult(
            ok=False,
            summary=(
                "NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS: los mensajes sin leer están "
                "desactivados. LOT Bot no puede leer el buzón de Wallapop de forma fiable con "
                "la integración actual (no hay API de mensajes) y no muestra datos que no "
                "sean reales. Consulta los mensajes en Wallapop."
            ),
            unavailable=True,
        )
    account_ref = None
    if args.get("cuenta"):
        account_ref = context.app.accounts.resolve_ref(str(args["cuenta"]))
    conversations = context.app.messages.list_conversations(
        account_ref=account_ref,
        only_unread=bool(args.get("solo_sin_leer")),
        limit=int(args.get("limite") or 50),
    )
    unread = context.app.messages.unread_count()
    label = "sin leer" if args.get("solo_sin_leer") else "en total"
    result = ok(
        f"{len(conversations)} conversación(es) {label}. Mensajes sin leer: {unread}.",
        conversaciones=[c.to_dict() for c in conversations[:50]],
        sin_leer=unread,
    )
    if conversations:
        # La primera (la más reciente) pasa a ser «este cliente».
        result.focus = {"conversation_id": conversations[0].id}
    return result


def _get_conversation(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    conversation_id = args.get("conversacion")
    if conversation_id is None:
        return fail("Indica el identificador de la conversación.")
    view = context.app.messages.get_conversation(int(conversation_id))
    if view is None:
        return fail(f"No se encuentra la conversación {conversation_id}.")
    return ok(
        f"Conversación con {view.buyer_name or 'comprador'} ({view.account_alias}).",
        conversacion=view.to_dict(),
        mensajes=[
            {"de": "comprador" if m.is_from_buyer else "nosotros", "texto": m.body}
            for m in view.messages[-20:]
        ],
    )


def _sales_context(context: ToolContext, view) -> Any:
    """Construye el contexto de venta SOLO con datos reales.

    Si el anuncio procede del anuncio principal, se añaden las ofertas por
    medida, el WhatsApp y la nota de transporte que figuran en la plantilla:
    son datos que el cliente ha escrito en su propio anuncio.
    """
    listing = None
    product = None
    master = None
    if view.listing_id:
        listing_view = context.app.listings.get(view.listing_id)
        if listing_view is not None:
            listing = listing_view.to_dict()
            if listing_view.product_sku:
                product_view = context.app.catalog.get_product(listing_view.product_sku)
                if product_view is not None:
                    product = product_view.to_dict()
            if listing_view.master_ad_id:
                master = context.app.master_ads.get(str(listing_view.master_ad_id))
    return build_context_from_data(
        product=product,
        listing=listing,
        business=context.app.business_settings,
        master=master,
    )


def _prepare_message_response(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Prepara una respuesta SIN enviarla. Nunca inventa datos."""
    if not context.app.messages.messaging_available:
        return _get_messages(context, args)
    conversation_id = args.get("conversacion") or context.focus.get("conversation_id")
    if conversation_id is None:
        # «Este cliente» sin haber abierto ninguna conversación: la más
        # reciente con mensajes sin leer, y se dice cuál es.
        pending = context.app.messages.list_conversations(only_unread=True, limit=1)
        if not pending:
            return fail(
                "No hay ninguna conversación seleccionada ni mensajes sin leer. "
                "Pídeme antes los mensajes o indica el número de conversación."
            )
        conversation_id = pending[0].id
    view = context.app.messages.get_conversation(int(conversation_id))
    if view is None:
        return fail(f"No se encuentra la conversación {conversation_id}.")

    buyer_message = args.get("mensaje_comprador")
    if not buyer_message:
        last = view.last_buyer_message()
        buyer_message = last.body if last else ""
    if not buyer_message:
        return fail("No hay ningún mensaje del comprador al que responder.")

    sales_context = _sales_context(context, view)
    assistant = SalesAssistant()
    suggestion = assistant.suggest(buyer_message, sales_context)
    context.app.messages.save_draft(int(conversation_id), suggestion.text, generated_by_ai=True)

    result = ok(
        f"Respuesta preparada para {view.buyer_name or 'el comprador'} "
        f"({view.account_alias}) — intención detectada: {suggestion.intent.value}. "
        f"NO se ha enviado: revísala y confírmala.",
        respuesta=suggestion.text,
        datos_usados=suggestion.used_facts,
        datos_que_faltan=suggestion.missing_facts,
        requiere_persona=suggestion.needs_human,
        pregunta_comprador=buyer_message,
        hechos_disponibles=sales_context.known_facts(),
    )
    result.focus = {"conversation_id": view.id}
    return result


def _send_message(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    conversation_id = args.get("conversacion") or context.focus.get("conversation_id")
    body = args.get("texto")
    if conversation_id is None or not body:
        return fail("Indica la conversación y el texto del mensaje.")
    view = context.app.messages.get_conversation(int(conversation_id))
    if view is None:
        return fail(f"No se encuentra la conversación {conversation_id}.")

    if not context.confirmed:
        return confirm_first(
            "send_message",
            args,
            "Voy a enviar un mensaje a un comprador",
            [
                f"Cuenta: {view.account_alias}",
                f"Comprador: {view.buyer_name or '—'}",
                f"Anuncio: {view.listing_title or '—'}",
                f"Mensaje: {body}",
            ],
            affected=1,
        )
    result = context.app.messages.send(
        int(conversation_id), str(body), confirmed=True, actor=context.actor
    )
    return ToolResult(ok=bool(result.get("correcto")), summary=result.get("mensaje", ""), data=result)


def _sales_facts(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Devuelve los hechos comprobados de un anuncio o producto.

    Es la herramienta que debe usar la IA ANTES de responder a un comprador:
    lo que no salga aqui, no se puede afirmar.
    """
    conversation_id = args.get("conversacion")
    if conversation_id is not None:
        view = context.app.messages.get_conversation(int(conversation_id))
        if view is None:
            return fail(f"No se encuentra la conversación {conversation_id}.")
        sales_context = _sales_context(context, view)
    else:
        identifier = args.get("producto")
        if not identifier:
            return fail("Indica una conversación o un producto.")
        product_view = context.app.catalog.get_product(str(identifier))
        if product_view is None:
            return fail(f"No se encuentra el producto '{identifier}'.")
        sales_context = build_context_from_data(
            product=product_view.to_dict(), listing=None, business=context.app.business_settings
        )
    return ok(
        "Datos comprobados. Solo puedes afirmar lo que aparece en 'hechos'; "
        "para el resto responde: No dispongo de esa información.",
        hechos=sales_context.known_facts(),
        desconocidos=sales_context.unknown_fields(),
    )


MESSAGE_TOOLS: list[Tool] = [
    Tool(
        name="get_messages",
        description="Consulta la bandeja de entrada: conversaciones con compradores.",
        parameters={
            "properties": {
                "cuenta": {"type": "string"},
                "solo_sin_leer": {"type": "boolean"},
                "limite": {"type": "integer"},
            },
            "required": [],
        },
        handler=_get_messages,
        category=ToolCategory.MESSAGES,
        capability="list_conversations",
    ),
    Tool(
        name="get_conversation",
        description="Lee el historial completo de una conversación con un comprador.",
        parameters={
            "properties": {"conversacion": {"type": "integer"}},
            "required": ["conversacion"],
        },
        handler=_get_conversation,
        category=ToolCategory.MESSAGES,
    ),
    Tool(
        name="get_sales_facts",
        description=(
            "OBLIGATORIA antes de responder a un comprador. Devuelve los datos "
            "comprobados del producto y del anuncio (precio, medida, stock, envío...). "
            "Solo puedes afirmar lo que devuelva esta herramienta."
        ),
        parameters={
            "properties": {
                "conversacion": {"type": "integer"},
                "producto": {"type": "string"},
            },
            "required": [],
        },
        handler=_sales_facts,
        category=ToolCategory.MESSAGES,
    ),
    Tool(
        name="prepare_message_response",
        description=(
            "Prepara (sin enviar) una respuesta para un comprador usando únicamente "
            "datos reales del producto. La guarda como borrador."
        ),
        parameters={
            "properties": {
                "conversacion": {
                    "type": "integer",
                    "description": "Omítelo para usar la conversación de la que se está hablando.",
                },
                "mensaje_comprador": {"type": "string"},
            },
            "required": [],
        },
        handler=_prepare_message_response,
        category=ToolCategory.MESSAGES,
    ),
    Tool(
        name="send_message",
        description=(
            "Envía un mensaje a un comprador en Wallapop. Requiere confirmación "
            "explícita del usuario."
        ),
        parameters={
            "properties": {"conversacion": {"type": "integer"}, "texto": {"type": "string"}},
            "required": ["texto"],
        },
        handler=_send_message,
        category=ToolCategory.MESSAGES,
        requires_confirmation=True,
        capability="send_message",
    ),
]
