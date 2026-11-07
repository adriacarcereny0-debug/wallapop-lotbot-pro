"""Modelo de datos local de LOT Bot.

Aislamiento multicuenta: todas las entidades que pertenecen a una cuenta de
Wallapop llevan `account_id` obligatorio y las consultas de la capa de servicio
filtran SIEMPRE por ese campo. Nunca se mezclan anuncios, mensajes ni
credenciales entre cuentas.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Enumeraciones
# ---------------------------------------------------------------------------
class AccountStatus(str, enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    EXPIRED = "expired"
    ERROR = "error"


class ProductStatus(str, enum.Enum):
    DRAFT = "draft"
    READY = "ready"
    PUBLISHED = "published"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ListingStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"
    SOLD = "sold"
    REMOVED = "removed"
    ERROR = "error"


class AutomationStatus(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"


class ActionResult(str, enum.Enum):
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Cuentas
# ---------------------------------------------------------------------------
class Account(Base, TimestampMixin):
    """Una cuenta de Wallapop conectada.

    Nunca se guarda la contrasena de Wallapop. Solo tokens OAuth cifrados.
    """

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    internal_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    alias: Mapped[str] = mapped_column(String(120), nullable=False)
    wallapop_user_id: Mapped[str | None] = mapped_column(String(120))
    wallapop_login: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus), default=AccountStatus.DISCONNECTED, nullable=False
    )
    status_detail: Mapped[str | None] = mapped_column(Text)

    #: Mecanismo con el que se conectó esta cuenta (ver wallapop/auth).
    #: Cada cuenta puede usar uno distinto.
    auth_method: Mapped[str | None] = mapped_column(String(40))
    #: Credencial completa (cabeceras, cookies, caducidad), cifrada con la
    #: clave maestra local. Es el campo que usan todos los mecanismos.
    credential_enc: Mapped[str | None] = mapped_column(Text)

    # --- Campos anteriores, solo para migrar instalaciones ya existentes ---
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    scopes: Mapped[str | None] = mapped_column(Text)

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    listings: Mapped[list[Listing]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    assignments: Mapped[list[ProductAssignment]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Account {self.internal_ref} {self.alias} {self.status.value}>"


# ---------------------------------------------------------------------------
# Catalogo
# ---------------------------------------------------------------------------
class Product(Base, TimestampMixin):
    """Producto del catalogo interno (fuente de verdad del cliente)."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False)
    product_type: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(120))
    subcategory: Mapped[str | None] = mapped_column(String(120))
    size: Mapped[str | None] = mapped_column(String(80))
    color: Mapped[str | None] = mapped_column(String(80))
    material: Mapped[str | None] = mapped_column(String(80))
    condition: Mapped[str | None] = mapped_column(String(50), default="Nuevo")
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    price: Mapped[float | None] = mapped_column(Float)
    stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    title_override: Mapped[str | None] = mapped_column(String(250))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[ProductStatus] = mapped_column(
        Enum(ProductStatus), default=ProductStatus.DRAFT, nullable=False
    )
    quality_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("templates.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)

    images: Mapped[list[ProductImage]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductImage.position",
    )
    assignments: Mapped[list[ProductAssignment]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    listings: Mapped[list[Listing]] = relationship(back_populates="product")
    template: Mapped[Template | None] = relationship()

    __table_args__ = (
        Index("ix_products_type_size", "product_type", "size"),
        Index("ix_products_status", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Product {self.sku} {self.name}>"


class ProductImage(Base, TimestampMixin):
    """Fotografia asociada a un producto."""

    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(250))
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    file_format: Mapped[str | None] = mapped_column(String(10))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    perceptual_hash: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(30), default="import", nullable=False)

    product: Mapped[Product] = relationship(back_populates="images")

    __table_args__ = (Index("ix_images_product_pos", "product_id", "position"),)


class ProductAssignment(Base, TimestampMixin):
    """Regla de distribucion: que producto se publica en que cuenta."""

    __tablename__ = "product_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    price_override: Mapped[float | None] = mapped_column(Float)

    product: Mapped[Product] = relationship(back_populates="assignments")
    account: Mapped[Account] = relationship(back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("product_id", "account_id", name="uq_assignment_product_account"),
    )


class Template(Base, TimestampMixin):
    """Plantilla de anuncio con variables ({producto}, {medida}, ...)."""

    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    title_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    description_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    features_pattern: Mapped[list] = mapped_column(JSON, default=list)
    tags_pattern: Mapped[list] = mapped_column(JSON, default=list)
    variables: Mapped[dict] = mapped_column(JSON, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# ---------------------------------------------------------------------------
# Anuncio principal (plantilla maestra del negocio)
# ---------------------------------------------------------------------------
class MasterAd(Base, TimestampMixin):
    """Anuncio que el negocio publica de forma recurrente.

    Es la plantilla MAESTRA: publicar desde ella crea anuncios nuevos pero
    nunca la modifica. Solo cambia cuando el usuario lo pide expresamente
    («actualiza la plantilla») o la edita en su pantalla.

    Los textos se guardan tal cual los proporciona el cliente. LOT Bot no los
    corrige ni los reinterpreta.
    """

    __tablename__ = "master_ads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    title: Mapped[str] = mapped_column(String(250), nullable=False, default="")
    #: Caracteristicas en el orden en que se muestran («Nuevo · ...»).
    features: Mapped[list] = mapped_column(JSON, default=list)
    price: Mapped[float | None] = mapped_column(Float)
    #: Descripcion con variables ({whatsapp}, {precio_135x190}...).
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Ofertas por medida: [{"medida": "135x190", "precio": 270}, ...]
    variants: Mapped[list] = mapped_column(JSON, default=list)
    contact_whatsapp: Mapped[str | None] = mapped_column(String(40))
    delivery_note: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(120))
    subcategory: Mapped[str | None] = mapped_column(String(120))
    condition: Mapped[str | None] = mapped_column(String(50))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    #: Fotografias: [{"path": ..., "position": 0, "is_primary": True, ...}]
    images: Mapped[list] = mapped_column(JSON, default=list)
    #: Expresiones con las que el usuario se refiere a este anuncio.
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    #: Variables adicionales de la descripcion.
    variables: Mapped[dict] = mapped_column(JSON, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Características estructuradas (estado, uso, color, material): van a
    #: los campos del formulario, no a la descripción.
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Plantilla única activa: los anuncios automáticos usan exactamente sus
    #: valores; no se admiten cambios por publicación.
    locked: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Copia de los datos originales del cliente, para poder restaurarlos.
    original: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# Anuncios publicados
# ---------------------------------------------------------------------------
class Listing(Base, TimestampMixin):
    """Anuncio en Wallapop, siempre ligado a UNA cuenta."""

    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    #: Anuncio principal del que procede esta publicacion (si procede de uno).
    master_ad_id: Mapped[int | None] = mapped_column(
        ForeignKey("master_ads.id", ondelete="SET NULL")
    )
    #: Cambios aplicados SOLO a esta publicacion (no tocan la plantilla).
    overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    wallapop_item_id: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(250), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="EUR", nullable=False)
    category: Mapped[str | None] = mapped_column(String(120))
    condition: Mapped[str | None] = mapped_column(String(50))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    image_urls: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[ListingStatus] = mapped_column(
        Enum(ListingStatus), default=ListingStatus.DRAFT, nullable=False
    )
    status_detail: Mapped[str | None] = mapped_column(Text)
    views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    favorites: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    #: Dirección pública del anuncio en Wallapop (si Wallapop la mostró).
    url: Mapped[str | None] = mapped_column(Text)
    #: Cómo se creó: imagen usada (habitación, luz, ángulo...), para analizar
    #: qué funciona mejor. Nunca datos de Wallapop inventados.
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    account: Mapped[Account] = relationship(back_populates="listings")
    product: Mapped[Product | None] = relationship(back_populates="listings")
    master_ad: Mapped[MasterAd | None] = relationship()

    __table_args__ = (
        UniqueConstraint("account_id", "wallapop_item_id", name="uq_listing_account_item"),
        Index("ix_listings_account_status", "account_id", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Listing acc={self.account_id} {self.title!r} {self.price}>"


# ---------------------------------------------------------------------------
# Mensajeria
# ---------------------------------------------------------------------------
class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    wallapop_conversation_id: Mapped[str | None] = mapped_column(String(120))
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"))
    buyer_name: Mapped[str | None] = mapped_column(String(150))
    subject: Mapped[str | None] = mapped_column(String(250))
    #: Mensajes del comprador sin leer en esta conversacion.
    unread: Mapped[int] = mapped_column("unread_count", Integer, default=0, nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    account: Mapped[Account] = relationship(back_populates="conversations")
    listing: Mapped[Listing | None] = relationship()
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sent_at",
    )

    __table_args__ = (
        UniqueConstraint(
            "account_id", "wallapop_conversation_id", name="uq_conv_account_remote"
        ),
    )


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    wallapop_message_id: Mapped[str | None] = mapped_column(String(120))
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # in | out
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    generated_by_ai: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------
# Automatizaciones, auditoria y ajustes
# ---------------------------------------------------------------------------
class Automation(Base, TimestampMixin):
    __tablename__ = "automations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[AutomationStatus] = mapped_column(
        Enum(AutomationStatus), default=AutomationStatus.IDLE, nullable=False
    )
    last_result: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    """Historial de acciones. Nunca contiene tokens ni secretos."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), default="usuario", nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    account_ref: Mapped[str | None] = mapped_column(String(120))
    target: Mapped[str | None] = mapped_column(String(250))
    result: Mapped[ActionResult] = mapped_column(
        Enum(ActionResult), default=ActionResult.OK, nullable=False
    )
    detail: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (Index("ix_audit_ts", "timestamp"),)


class Setting(Base, TimestampMixin):
    """Ajustes de usuario guardados en la base de datos (no secretos)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# Cola de publicación automática
# ---------------------------------------------------------------------------
class PublishJobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PublishTaskStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    WAITING = "waiting"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    #: Se pulsó «Publicar» pero Wallapop no lo confirmó: NO se da por publicado
    #: y NO se reintenta solo (podría duplicarse).
    UNCONFIRMED = "unconfirmed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PublishJob(Base, TimestampMixin):
    """Una orden de publicación (p. ej. «publica 10 canapés»)."""

    __tablename__ = "publish_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[PublishJobStatus] = mapped_column(
        Enum(PublishJobStatus), default=PublishJobStatus.PENDING, nullable=False
    )
    #: Intervalo mínimo aplicado a esta cola (nunca inferior a 60 s).
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    generate_images: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), default="usuario", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    pause_reason: Mapped[str | None] = mapped_column(Text)

    tasks: Mapped[list[PublishTask]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="PublishTask.position"
    )


class PublishTask(Base, TimestampMixin):
    """Un anuncio concreto dentro de una cola, con la cuenta que lo publica."""

    __tablename__ = "publish_tasks"
    __table_args__ = (Index("ix_publish_tasks_job", "job_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("publish_jobs.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    account_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Qué publicar: {"master_key": ..., "overrides": {...}}.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[PublishTaskStatus] = mapped_column(
        Enum(PublishTaskStatus), default=PublishTaskStatus.PENDING, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    image_path: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    remote_id: Mapped[str | None] = mapped_column(String(120))
    remote_url: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(80))

    job: Mapped[PublishJob] = relationship(back_populates="tasks")


# ---------------------------------------------------------------------------
# Imágenes generadas (registro para evitar repeticiones)
# ---------------------------------------------------------------------------
class GeneratedImage(Base, TimestampMixin):
    __tablename__ = "generated_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Hash perceptual (dHash de 64 bits en hexadecimal).
    perceptual_hash: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(200))
    account_ref: Mapped[str | None] = mapped_column(String(64))
    task_id: Mapped[int | None] = mapped_column(Integer)
    #: generar, mejorar, estilo, habitacion, referencia
    operation: Mapped[str | None] = mapped_column(String(40))
    #: Escena elegida (habitación, luz, ángulo, estilo) para poder analizarla.
    scene: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Imagen de partida en mejoras, cambios de estilo o de habitación.
    source_image: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Estadísticas: histórico de mediciones REALES
# ---------------------------------------------------------------------------
class ListingStat(Base):
    """Una medición de un anuncio en un momento dado.

    `views` y `favorites` son None cuando el dato no se ha podido leer de
    verdad: nunca se guardan ceros ni estimaciones en su lugar.
    """

    __tablename__ = "listing_stats"
    __table_args__ = (Index("ix_listing_stats_listing", "listing_id", "captured_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    account_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    views: Mapped[int | None] = mapped_column(Integer)
    favorites: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(40))
    #: «navegador» (leído en la web) o «demo» (simulado).
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
