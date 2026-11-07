"""Bandeja de entrada: conversaciones y mensajes, aislados por cuenta."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from lot_bot.core.audit import AuditService
from lot_bot.database.engine import Database
from lot_bot.database.models import Account, Conversation, Listing, Message
from lot_bot.wallapop.capabilities import Capability
from lot_bot.wallapop.errors import WallapopError
from lot_bot.wallapop.service import WallapopService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MessageView:
    id: int
    direction: str
    body: str
    sent_at: datetime | None
    is_draft: bool
    generated_by_ai: bool

    @property
    def is_from_buyer(self) -> bool:
        return self.direction == "in"


@dataclass(slots=True)
class ConversationView:
    id: int
    account_ref: str
    account_alias: str
    remote_id: str | None
    buyer_name: str | None
    subject: str | None
    unread: int
    last_message_at: datetime | None
    listing_id: int | None
    listing_title: str | None
    messages: list[MessageView] = field(default_factory=list)

    def last_buyer_message(self) -> MessageView | None:
        for message in reversed(self.messages):
            if message.is_from_buyer:
                return message
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cuenta": self.account_alias,
            "comprador": self.buyer_name,
            "anuncio": self.listing_title,
            "sin_leer": self.unread,
            "mensajes": len(self.messages),
            "ultimo": self.messages[-1].body if self.messages else None,
        }


class MessageService:
    """Sincroniza, consulta y responde conversaciones."""

    def __init__(
        self, database: Database, wallapop: WallapopService, audit: AuditService
    ) -> None:
        self._db = database
        self._wallapop = wallapop
        self._audit = audit

    def set_backend(self, wallapop: WallapopService) -> None:
        self._wallapop = wallapop

    @property
    def messaging_available(self) -> bool:
        """Solo con una integración que entregue los mensajes de forma fiable.

        La integración por navegador NO lee el buzón (sería extraer datos de
        la web del chat, frágil y poco fiable) y los mensajes del modo DEMO son
        inventados: en ninguno de los dos casos se muestra la función, para no
        aparentar que funciona sin datos reales.
        """
        if getattr(self._wallapop, "is_mock", False):
            return False
        return self._wallapop.supports(Capability.LIST_CONVERSATIONS)

    @property
    def sending_available(self) -> bool:
        return self._wallapop.supports(Capability.SEND_MESSAGE)

    # ------------------------------------------------------------------
    # Sincronizacion
    # ------------------------------------------------------------------
    def sync_account(self, account_ref: str, limit: int = 50) -> dict[str, int]:
        self._wallapop.require(Capability.LIST_CONVERSATIONS)
        conversations = self._wallapop.list_conversations(account_ref, limit=limit)
        created = updated = new_messages = 0

        with self._db.session_scope() as session:
            account = session.scalar(select(Account).where(Account.internal_ref == account_ref))
            if account is None:
                raise ValueError(f"Cuenta '{account_ref}' no encontrada.")

            for summary in conversations:
                conversation = session.scalar(
                    select(Conversation)
                    .where(Conversation.account_id == account.id)
                    .where(Conversation.wallapop_conversation_id == summary.conversation_id)
                )
                if conversation is None:
                    conversation = Conversation(
                        account_id=account.id,
                        wallapop_conversation_id=summary.conversation_id,
                    )
                    session.add(conversation)
                    created += 1
                else:
                    updated += 1

                conversation.buyer_name = summary.buyer_name
                conversation.subject = summary.subject
                conversation.unread = summary.unread
                conversation.last_message_at = _naive(summary.last_message_at)
                if summary.item_id:
                    listing_id = session.scalar(
                        select(Listing.id)
                        .where(Listing.account_id == account.id)
                        .where(Listing.wallapop_item_id == summary.item_id)
                    )
                    conversation.listing_id = listing_id
                session.flush()

                if self._wallapop.supports(Capability.GET_CONVERSATION):
                    new_messages += self._sync_messages(
                        session, account_ref, conversation, summary.conversation_id
                    )

        return {"nuevas": created, "actualizadas": updated, "mensajes": new_messages}

    def _sync_messages(
        self, session, account_ref: str, conversation: Conversation, remote_id: str
    ) -> int:
        try:
            remote_messages = self._wallapop.get_conversation_messages(account_ref, remote_id)
        except WallapopError as exc:
            logger.warning("No se han podido leer los mensajes de %s: %s", remote_id, exc.detail)
            return 0

        existing = {
            message_id
            for (message_id,) in session.execute(
                select(Message.wallapop_message_id).where(
                    Message.conversation_id == conversation.id
                )
            ).all()
        }
        added = 0
        for remote in remote_messages:
            if remote.message_id in existing:
                continue
            session.add(
                Message(
                    conversation_id=conversation.id,
                    wallapop_message_id=remote.message_id,
                    direction=remote.direction,
                    body=remote.body,
                    sent_at=_naive(remote.sent_at) or datetime.now(UTC).replace(tzinfo=None),
                )
            )
            added += 1
        return added

    def sync_all(self, account_refs: list[str]) -> dict[str, dict[str, int]]:
        results: dict[str, dict[str, int]] = {}
        for ref in account_refs:
            try:
                results[ref] = self.sync_account(ref)
            except Exception as exc:
                logger.warning("Fallo sincronizando mensajes de '%s': %s", ref, exc)
                results[ref] = {"error": 1}
        return results

    # ------------------------------------------------------------------
    # Consulta local
    # ------------------------------------------------------------------
    def list_conversations(
        self, account_ref: str | None = None, only_unread: bool = False, limit: int = 200
    ) -> list[ConversationView]:
        with self._db.session_scope() as session:
            stmt = (
                select(Conversation, Account)
                .join(Account, Account.id == Conversation.account_id)
                .where(Conversation.is_archived.is_(False))
            )
            if account_ref:
                stmt = stmt.where(Account.internal_ref == account_ref)
            if only_unread:
                stmt = stmt.where(Conversation.unread > 0)
            stmt = stmt.order_by(Conversation.last_message_at.desc().nullslast()).limit(limit)

            views: list[ConversationView] = []
            for conversation, account in session.execute(stmt).all():
                views.append(self._to_view(session, conversation, account))
            return views

    def get_conversation(self, conversation_id: int) -> ConversationView | None:
        with self._db.session_scope() as session:
            row = session.execute(
                select(Conversation, Account)
                .join(Account, Account.id == Conversation.account_id)
                .where(Conversation.id == conversation_id)
            ).first()
            if row is None:
                return None
            return self._to_view(session, row[0], row[1])

    @staticmethod
    def _to_view(session, conversation: Conversation, account: Account) -> ConversationView:
        listing_title = None
        if conversation.listing_id:
            listing_title = session.scalar(
                select(Listing.title).where(Listing.id == conversation.listing_id)
            )
        return ConversationView(
            id=conversation.id,
            account_ref=account.internal_ref,
            account_alias=account.alias,
            remote_id=conversation.wallapop_conversation_id,
            buyer_name=conversation.buyer_name,
            subject=conversation.subject,
            unread=conversation.unread,
            last_message_at=conversation.last_message_at,
            listing_id=conversation.listing_id,
            listing_title=listing_title,
            messages=[
                MessageView(
                    id=message.id,
                    direction=message.direction,
                    body=message.body,
                    sent_at=message.sent_at,
                    is_draft=message.is_draft,
                    generated_by_ai=message.generated_by_ai,
                )
                for message in conversation.messages
            ],
        )

    def unread_count(self) -> int:
        with self._db.session_scope() as session:
            return int(session.scalar(select(func.sum(Conversation.unread))) or 0)

    # ------------------------------------------------------------------
    # Respuestas
    # ------------------------------------------------------------------
    def save_draft(self, conversation_id: int, body: str, generated_by_ai: bool = True) -> int:
        """Guarda una respuesta preparada SIN enviarla."""
        with self._db.session_scope() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ValueError(f"Conversación {conversation_id} no encontrada.")
            # Solo un borrador por conversacion.
            for message in conversation.messages:
                if message.is_draft:
                    session.delete(message)
            draft = Message(
                conversation_id=conversation_id,
                direction="out",
                body=body,
                is_draft=True,
                generated_by_ai=generated_by_ai,
            )
            session.add(draft)
            session.flush()
            return draft.id

    def send(
        self, conversation_id: int, body: str, *, confirmed: bool, actor: str = "usuario"
    ) -> dict[str, Any]:
        """Envia un mensaje. Requiere confirmacion explicita del usuario."""
        if not confirmed:
            raise PermissionError(
                "Enviar un mensaje a un comprador requiere confirmación del usuario."
            )
        if not body.strip():
            raise ValueError("El mensaje está vacío.")

        self._wallapop.require(Capability.SEND_MESSAGE)
        view = self.get_conversation(conversation_id)
        if view is None or not view.remote_id:
            raise ValueError(f"Conversación {conversation_id} no encontrada o sin identificador.")

        try:
            result = self._wallapop.send_message(view.account_ref, view.remote_id, body)
        except WallapopError as exc:
            self._audit.record_error(
                "Envío de mensaje",
                error=f"{type(exc).__name__}: {exc.detail}",
                account_ref=view.account_ref,
                target=view.buyer_name,
                actor=actor,
            )
            return {"correcto": False, "mensaje": exc.user_message, "codigo": type(exc).__name__}

        with self._db.session_scope() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is not None:
                for message in list(conversation.messages):
                    if message.is_draft:
                        session.delete(message)
                session.add(
                    Message(
                        conversation_id=conversation_id,
                        wallapop_message_id=result.data.get("message_id"),
                        direction="out",
                        body=body,
                        generated_by_ai=False,
                    )
                )
                conversation.unread = 0
                conversation.last_message_at = datetime.now(UTC).replace(tzinfo=None)

        self._audit.record_success(
            "Envío de mensaje",
            account_ref=view.account_ref,
            target=view.buyer_name or view.listing_title,
            detail=body[:200],
            actor=actor,
        )
        return {"correcto": True, "mensaje": result.message}

    def mark_read(self, conversation_id: int) -> None:
        with self._db.session_scope() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is not None:
                conversation.unread = 0

    def archive(self, conversation_id: int, archived: bool = True) -> None:
        with self._db.session_scope() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is not None:
                conversation.is_archived = archived


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
