"""Asistente de respuestas y cierre de ventas.

REGLA INNEGOCIABLE
------------------
El asistente solo puede afirmar lo que consta en `SalesContext`, construido a
partir de datos REALES del catalogo, del anuncio y de la configuracion del
negocio. Si un dato no consta, responde exactamente:

    "No dispongo de esa información."

Nunca inventa stock, precios, medidas, condiciones, disponibilidad, plazos de
entrega ni promesas comerciales no configuradas.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

#: Respuesta obligatoria cuando falta un dato.
UNKNOWN_ANSWER = "No dispongo de esa información."


class BuyerIntent(str, Enum):
    AVAILABILITY = "disponibilidad"
    PRICE = "precio"
    SIZE = "medida"
    DELIVERY = "envio"
    ASSEMBLY = "montaje"
    PAYMENT = "pago"
    LOCATION = "ubicacion"
    PURCHASE = "compra"
    COMPLAINT = "incidencia"
    GREETING = "saludo"
    OTHER = "otros"


#: El orden importa: se devuelve la PRIMERA intencion que coincide.
#: Las incidencias van primero porque deben atenderlas personas, y frases como
#: "me ha llegado roto" contienen palabras de envio que, si no, ganarian.
_INTENT_PATTERNS: list[tuple[BuyerIntent, tuple[str, ...]]] = [
    (BuyerIntent.COMPLAINT, ("problema", "roto", "rota", "defecto", "defectuos",
                             "reclamacion", "devolver", "devolucion", "no funciona",
                             "mal estado", "danado", "incidencia")),
    (BuyerIntent.PURCHASE, ("lo quiero", "me lo quedo", "como lo compro", "quiero comprar",
                            "reservar", "lo reservo", "como hago el pedido", "pedido")),
    (BuyerIntent.AVAILABILITY, ("disponible", "queda", "quedan", "teneis", "tienes", "hay stock",
                                "sigue a la venta", "esta libre")),
    (BuyerIntent.PRICE, ("precio", "cuanto cuesta", "cuanto vale", "cuesta", "ultimo precio",
                         "rebaja", "descuento", "oferta")),
    (BuyerIntent.SIZE, ("medida", "medidas", "tamano", "x190", "x200", "90", "105", "135", "150", "160")),
    (BuyerIntent.DELIVERY, ("envio", "envias", "enviais", "transporte", "llega", "entrega",
                            "traer", "a domicilio", "portes")),
    (BuyerIntent.ASSEMBLY, ("montaje", "montar", "montado", "instalacion", "instalar")),
    (BuyerIntent.PAYMENT, ("pago", "pagar", "bizum", "transferencia", "tarjeta", "efectivo",
                           "financiar", "plazos")),
    (BuyerIntent.LOCATION, ("donde estais", "tienda", "recoger", "direccion", "ubicacion", "zona")),
    (BuyerIntent.GREETING, ("hola", "buenas", "buenos dias", "buenas tardes")),
]


def _normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def detect_intent(text: str) -> BuyerIntent:
    """Clasifica la pregunta del comprador."""
    normalized = _normalize(text)
    if not normalized:
        return BuyerIntent.OTHER
    for intent, patterns in _INTENT_PATTERNS:
        if any(pattern in normalized for pattern in patterns):
            return intent
    return BuyerIntent.OTHER


@dataclass(slots=True)
class SalesContext:
    """Hechos comprobados que el asistente puede afirmar.

    Un campo a `None` significa "no lo sabemos" y obliga a responder
    `No dispongo de esa información.` en vez de inventarlo.
    """

    product_name: str | None = None
    size: str | None = None
    color: str | None = None
    material: str | None = None
    condition: str | None = None
    price: float | None = None
    currency: str = "EUR"
    stock: int | None = None
    listing_title: str | None = None
    # --- Condiciones comerciales configuradas por el cliente ---
    delivery_policy: str | None = None
    assembly_policy: str | None = None
    payment_policy: str | None = None
    location_policy: str | None = None
    whatsapp: str | None = None
    business_name: str | None = None
    extra_facts: dict[str, str] = field(default_factory=dict)
    #: Ofertas por medida del anuncio principal: {"135x190": 270.0}.
    offers: dict[str, float] = field(default_factory=dict)

    @property
    def is_available(self) -> bool | None:
        if self.stock is None:
            return None
        return self.stock > 0

    def known_facts(self) -> dict[str, Any]:
        """Diccionario de hechos conocidos, tal cual se pasa al modelo de IA."""
        facts: dict[str, Any] = {}
        mapping = {
            "producto": self.product_name,
            "titulo_anuncio": self.listing_title,
            "medida": self.size,
            "color": self.color,
            "material": self.material,
            "estado": self.condition,
            "precio": f"{self.price:.2f} {self.currency}" if self.price is not None else None,
            "unidades_disponibles": self.stock,
            "envio": self.delivery_policy,
            "montaje": self.assembly_policy,
            "formas_de_pago": self.payment_policy,
            "recogida": self.location_policy,
            "whatsapp": self.whatsapp,
            "negocio": self.business_name,
        }
        facts.update({k: v for k, v in mapping.items() if v not in (None, "")})
        facts.update({k: v for k, v in self.extra_facts.items() if v})
        for medida, precio in self.offers.items():
            facts[f"oferta_{medida}"] = f"{precio:.2f} {self.currency}"
        return facts

    def unknown_fields(self) -> list[str]:
        candidates = {
            "precio": self.price,
            "medida": self.size,
            "unidades_disponibles": self.stock,
            "envio": self.delivery_policy,
            "montaje": self.assembly_policy,
            "formas_de_pago": self.payment_policy,
            "recogida": self.location_policy,
        }
        return [name for name, value in candidates.items() if value in (None, "")]


@dataclass(slots=True)
class SuggestedReply:
    """Respuesta propuesta. NUNCA se envia sola: la confirma el usuario."""

    text: str
    intent: BuyerIntent
    used_facts: dict[str, Any] = field(default_factory=dict)
    missing_facts: list[str] = field(default_factory=list)
    needs_human: bool = False
    generated_by: str = "reglas"

    def to_dict(self) -> dict[str, Any]:
        return {
            "respuesta": self.text,
            "intencion": self.intent.value,
            "datos_usados": self.used_facts,
            "datos_que_faltan": self.missing_facts,
            "requiere_persona": self.needs_human,
            "generado_por": self.generated_by,
        }


def _sentence(text: str | None) -> str:
    """Añade punto final si falta (las condiciones las escribe el cliente)."""
    text = (text or "").strip()
    if text and text[-1] not in ".!?…":
        text += "."
    return text


class SalesAssistant:
    """Genera respuestas basadas exclusivamente en datos reales."""

    def __init__(self, tone: str = "cercano") -> None:
        self.tone = tone

    # ------------------------------------------------------------------
    def suggest(self, buyer_message: str, context: SalesContext) -> SuggestedReply:
        intent = detect_intent(buyer_message)
        context = _focus_on_offer(buyer_message, context)
        builder = {
            BuyerIntent.AVAILABILITY: self._availability,
            BuyerIntent.PRICE: self._price,
            BuyerIntent.SIZE: self._size,
            BuyerIntent.DELIVERY: self._delivery,
            BuyerIntent.ASSEMBLY: self._assembly,
            BuyerIntent.PAYMENT: self._payment,
            BuyerIntent.LOCATION: self._location,
            BuyerIntent.PURCHASE: self._purchase,
            BuyerIntent.GREETING: self._greeting,
            BuyerIntent.COMPLAINT: self._complaint,
        }.get(intent, self._generic)

        text, used, missing = builder(context)
        needs_human = intent is BuyerIntent.COMPLAINT or (
            UNKNOWN_ANSWER in text and intent is not BuyerIntent.OTHER
        )
        return SuggestedReply(
            text=text.strip(),
            intent=intent,
            used_facts=used,
            missing_facts=missing,
            needs_human=needs_human,
        )

    # ------------------------------------------------------------------
    # Constructores por intencion. Cada uno declara que hechos ha usado.
    # ------------------------------------------------------------------
    def _availability(self, ctx: SalesContext) -> tuple[str, dict[str, Any], list[str]]:
        available = ctx.is_available
        if available is None:
            return (
                f"{UNKNOWN_ANSWER} Déjame confirmarlo y te digo enseguida.",
                {},
                ["unidades_disponibles"],
            )
        product = self._product_label(ctx)
        if not available:
            return (
                f"Ahora mismo no tenemos disponible {product}. "
                f"Si quieres, te aviso en cuanto vuelva a estar.",
                {"unidades_disponibles": ctx.stock},
                [],
            )
        text = f"Sí, tenemos disponible {product}."
        used: dict[str, Any] = {"unidades_disponibles": ctx.stock}
        if ctx.delivery_policy:
            text += f" {_sentence(ctx.delivery_policy)}"
            used["envio"] = ctx.delivery_policy
        text += self._closing(ctx)
        return text, used, []

    def _price(self, ctx: SalesContext) -> tuple[str, dict[str, Any], list[str]]:
        if ctx.price is None:
            return (UNKNOWN_ANSWER, {}, ["precio"])
        product = self._product_label(ctx)
        text = f"El precio de {product} es {ctx.price:.2f} {'€' if ctx.currency == 'EUR' else ctx.currency}."
        used = {"precio": ctx.price}
        if ctx.delivery_policy:
            text += f" {_sentence(ctx.delivery_policy)}"
            used["envio"] = ctx.delivery_policy
        text += self._closing(ctx)
        return text, used, []

    def _size(self, ctx: SalesContext) -> tuple[str, dict[str, Any], list[str]]:
        if not ctx.size:
            return (UNKNOWN_ANSWER, {}, ["medida"])
        text = f"La medida de este {ctx.product_name or 'producto'} es {ctx.size}."
        used = {"medida": ctx.size}
        if ctx.price is not None:
            text += f" El precio es {ctx.price:.2f} €."
            used["precio"] = ctx.price
        text += self._closing(ctx)
        return text, used, []

    def _delivery(self, ctx: SalesContext) -> tuple[str, dict[str, Any], list[str]]:
        if not ctx.delivery_policy:
            return (UNKNOWN_ANSWER, {}, ["envio"])
        text = _sentence(ctx.delivery_policy)
        used = {"envio": ctx.delivery_policy}
        if ctx.assembly_policy:
            text += f" {_sentence(ctx.assembly_policy)}"
            used["montaje"] = ctx.assembly_policy
        text += self._closing(ctx)
        return text, used, []

    def _assembly(self, ctx: SalesContext) -> tuple[str, dict[str, Any], list[str]]:
        if not ctx.assembly_policy:
            return (UNKNOWN_ANSWER, {}, ["montaje"])
        return _sentence(ctx.assembly_policy) + self._closing(ctx), {"montaje": ctx.assembly_policy}, []

    def _payment(self, ctx: SalesContext) -> tuple[str, dict[str, Any], list[str]]:
        if not ctx.payment_policy:
            return (UNKNOWN_ANSWER, {}, ["formas_de_pago"])
        return (
            _sentence(ctx.payment_policy) + self._closing(ctx),
            {"formas_de_pago": ctx.payment_policy},
            [],
        )

    def _location(self, ctx: SalesContext) -> tuple[str, dict[str, Any], list[str]]:
        if not ctx.location_policy:
            return (UNKNOWN_ANSWER, {}, ["recogida"])
        return (
            _sentence(ctx.location_policy) + self._closing(ctx),
            {"recogida": ctx.location_policy},
            [],
        )

    def _purchase(self, ctx: SalesContext) -> tuple[str, dict[str, Any], list[str]]:
        """Intencion de compra: se deriva al canal del negocio, sin prometer nada."""
        product = self._product_label(ctx)
        used: dict[str, Any] = {}
        parts = [f"¡Perfecto! Te confirmo los datos de {product}"]
        if ctx.price is not None:
            parts.append(f"precio {ctx.price:.2f} €")
            used["precio"] = ctx.price
        text = ", ".join(parts) + "."
        if ctx.delivery_policy:
            text += f" {_sentence(ctx.delivery_policy)}"
            used["envio"] = ctx.delivery_policy
        if ctx.whatsapp:
            text += f" Para cerrar el pedido escríbenos por WhatsApp al {ctx.whatsapp} y lo gestionamos."
            used["whatsapp"] = ctx.whatsapp
        else:
            text += " Dime y te indico cómo continuar con el pedido."
        return text, used, ([] if ctx.whatsapp else ["whatsapp"])

    def _greeting(self, ctx: SalesContext) -> tuple[str, dict[str, Any], list[str]]:
        product = self._product_label(ctx)
        return (
            f"¡Hola! Gracias por tu interés en {product}. ¿En qué puedo ayudarte?",
            {},
            [],
        )

    def _complaint(self, ctx: SalesContext) -> tuple[str, dict[str, Any], list[str]]:
        """Las incidencias no las resuelve la IA: las atiende una persona."""
        text = (
            "Lamento el problema. Para revisarlo bien y darte una solución, "
            "prefiero que lo vea una persona del equipo."
        )
        used: dict[str, Any] = {}
        if ctx.whatsapp:
            text += f" Escríbenos por WhatsApp al {ctx.whatsapp} y lo atendemos personalmente."
            used["whatsapp"] = ctx.whatsapp
        return text, used, []

    def _generic(self, ctx: SalesContext) -> tuple[str, dict[str, Any], list[str]]:
        product = self._product_label(ctx)
        text = f"Gracias por tu mensaje sobre {product}."
        used: dict[str, Any] = {}
        if ctx.price is not None:
            text += f" El precio es {ctx.price:.2f} €."
            used["precio"] = ctx.price
        if ctx.size:
            text += f" Medida: {ctx.size}."
            used["medida"] = ctx.size
        text += " Si necesitas cualquier otro dato, dímelo y te lo confirmo."
        return text, used, []

    # ------------------------------------------------------------------
    @staticmethod
    def _product_label(ctx: SalesContext) -> str:
        parts = [p for p in (ctx.product_name, ctx.size) if p]
        return " ".join(parts) if parts else (ctx.listing_title or "este producto")

    @staticmethod
    def _closing(ctx: SalesContext) -> str:
        """Cierre comercial: solo deriva al canal configurado, sin prometer nada."""
        if ctx.whatsapp:
            return f" Si quieres, puedo indicarte cómo hacer el pedido por WhatsApp ({ctx.whatsapp})."
        return " Si quieres, te indico cómo realizar el pedido."


def _focus_on_offer(message: str, context: SalesContext) -> SalesContext:
    """Si el comprador pregunta por una medida con oferta, se responde con
    ESA oferta (precio y medida reales), no con el precio genérico."""
    if not context.offers:
        return context
    normalized = _normalize(message).replace(" ", "")
    for medida, precio in context.offers.items():
        short = medida.split("x")[0]
        if medida in normalized or re.search(rf"(?<!\d){short}(?!\d)", normalized):
            from dataclasses import replace

            # «Canapé + colchón» es literalmente como el cliente llama a la
            # oferta en su descripción.
            return replace(context, size=medida, price=precio, product_name="canapé + colchón")
    return context


def build_context_from_data(
    *,
    product: dict[str, Any] | None,
    listing: dict[str, Any] | None,
    business: dict[str, Any] | None,
    master: Any | None = None,
) -> SalesContext:
    """Construye el contexto a partir de datos reales del catalogo/anuncio."""
    product = product or {}
    listing = listing or {}
    business = dict(business or {})
    attributes = listing.get("caracteristicas") or {}
    if not isinstance(attributes, dict):
        attributes = {}
    offers: dict[str, float] = {}
    if master is not None:
        for variant in master.variants:
            try:
                offers[str(variant["medida"])] = float(variant["precio"])
            except (KeyError, TypeError, ValueError):
                continue
        # Datos que el cliente ha escrito en su propio anuncio.
        business.setdefault("whatsapp", None)
        if not business.get("whatsapp") and master.contact_whatsapp:
            business["whatsapp"] = master.contact_whatsapp
        if not business.get("delivery") and master.delivery_note:
            business["delivery"] = master.delivery_note
    return SalesContext(
        offers=offers,
        product_name=product.get("tipo") or product.get("nombre") or listing.get("titulo"),
        size=product.get("medida") or attributes.get("medida"),
        color=product.get("color") or attributes.get("color"),
        material=product.get("material") or attributes.get("material"),
        condition=product.get("estado") or attributes.get("estado"),
        price=product.get("precio") if product.get("precio") is not None else listing.get("precio"),
        stock=product.get("stock"),
        listing_title=listing.get("titulo"),
        delivery_policy=business.get("delivery"),
        assembly_policy=business.get("assembly"),
        payment_policy=business.get("payment"),
        location_policy=business.get("location"),
        whatsapp=business.get("whatsapp"),
        business_name=business.get("name"),
    )
