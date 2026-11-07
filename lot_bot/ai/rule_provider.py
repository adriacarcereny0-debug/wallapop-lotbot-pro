"""Proveedor de ordenes directas, sin conexion a ningun servicio de IA.

Reconoce las ordenes habituales del cliente en espanol y las traduce a llamadas
a herramientas. Es 100% local y determinista: util cuando no hay clave de API,
no hay internet, o el cliente prefiere no usar un servicio externo.

No pretende entender lenguaje libre: cuando no reconoce una orden lo dice y
ofrece lo que si sabe hacer, en vez de improvisar.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from lot_bot.ai.provider import AIProvider, ProviderReply, ToolCall

logger = logging.getLogger(__name__)

_SIZE_RE = re.compile(r"(\d{2,3})\s*[x×]\s*(\d{2,3})", re.IGNORECASE)
_PRICE_RE = re.compile(r"(\d{1,6}(?:[.,]\d{1,2})?)\s*(?:€|eur|euros)", re.IGNORECASE)
_BARE_NUMBER_RE = re.compile(r"\b(\d{2,6}(?:[.,]\d{1,2})?)\b")


def _normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def _extract_size(text: str) -> str | None:
    match = _SIZE_RE.search(text)
    return f"{match.group(1)}x{match.group(2)}" if match else None


def _extract_price(text: str) -> float | None:
    match = _PRICE_RE.search(text)
    if not match:
        # "ponlo a 269" sin simbolo de moneda
        candidates = [m for m in _BARE_NUMBER_RE.finditer(text)]
        size = _SIZE_RE.search(text)
        for candidate in candidates:
            if size and size.start() <= candidate.start() < size.end():
                continue
            match = candidate
            break
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _extract_quoted(text: str) -> str | None:
    match = re.search(r"[\"'«]([^\"'»]{2,})[\"'»]", text)
    return match.group(1).strip() if match else None


def _extract_accounts(text: str) -> list[str] | None:
    """«la cuenta 1», «cuentas 1 y 3» -> ["Cuenta 1", "Cuenta 3"]."""
    normalized = _normalize(text)
    match = re.search(r"\bcuentas?\s+((?:\d+\s*(?:,|y)?\s*)+)", normalized)
    if match:
        numbers = re.findall(r"\d+", match.group(1))
        return [f"Cuenta {n}" for n in numbers] or None
    return None


def _extract_copies(normalized: str) -> int | None:
    """«publica 10 canapés», «sube 3 anuncios» -> 10 / 3.

    No confunde el número con una medida (135x190) ni con un precio (12 €).
    """
    match = re.search(
        r"(?<![\dx.,])(\d{1,3})\s+(?:canape|canapes|anuncio|anuncios|copia|copias|publicacion|publicaciones|veces)\b",
        normalized,
    )
    if match:
        return int(match.group(1))
    return None


def _extract_product_term(normalized: str) -> str | None:
    """Extrae el tipo de producto mencionado ('canape', 'colchon'...)."""
    for term in ("canape", "colchon", "sofa", "cabecero", "somier", "almohada"):
        if term in normalized:
            return term
    return None


class RuleBasedProvider(AIProvider):
    """Traduce ordenes escritas a llamadas de herramientas, sin IA externa."""

    name = "Órdenes directas (sin IA externa)"
    natural_language = False

    def describe(self) -> str:
        return self.name

    # ------------------------------------------------------------------
    def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ProviderReply:
        # Si la ultima vuelta ya trajo resultados de herramientas, resumimos.
        last = messages[-1] if messages else {}
        if last.get("role") == "user" and isinstance(last.get("content"), list):
            return ProviderReply(text=self._summarize_tool_results(last["content"]))

        text = _last_user_text(messages)
        if not text:
            return ProviderReply(text=self._help())

        call = self._match(text)
        if call is None:
            return ProviderReply(text=self._help(text))
        return ProviderReply(text="", tool_calls=[call], finished=False)

    # ------------------------------------------------------------------
    def _match(self, text: str) -> ToolCall | None:
        """Traduce una orden en español a UNA llamada de herramienta.

        El orden de las reglas importa: primero lo más específico (plantilla,
        mensajes) y después lo general, para que «prepara una respuesta para
        este cliente» no se confunda con «prepara el anuncio de canapé».
        """
        normalized = _normalize(text)
        size = _extract_size(normalized)
        term = _extract_product_term(normalized)
        accounts = _extract_accounts(text)
        quoted = _extract_quoted(text)
        copies = _extract_copies(normalized)

        def call(name: str, **args: Any) -> ToolCall:
            return ToolCall(
                id=f"rule-{name}",
                name=name,
                arguments={k: v for k, v in args.items() if v not in (None, [], {})},
            )

        mentions_master = bool(term == "canape") or bool(
            re.search(r"\b(anuncio principal|plantilla)\b", normalized)
        )
        explicit_template = bool(
            re.search(r"\b(plantilla|anuncio principal|anuncio maestro)\b", normalized)
        )

        # --- 00. Empezar a subir anuncios (flujo guiado: cuenta → cuántos) ---
        if re.search(
            r"\b(empieza|empezar|empezamos|comienza|comenzar|inicia|iniciar|arranca|arrancar|quiero|vamos a)\b.*\b(subir|publicar)\b",
            normalized,
        ) or re.search(r"\bcada\s+60\s+segundos\b", normalized) or re.search(
            r"\b(subir|sube|publicar|publica)\s+anuncios\b", normalized
        ):
            return call(
                "start_publishing",
                cuenta=accounts[0] if accounts else None,
                copias=copies,
                texto=text,
            )

        # --- 0a. Estadísticas, análisis y recomendaciones ---
        if re.search(r"\b(recomendacion|recomendaciones|recomiendas|que me recomiendas|sugerencias)\b", normalized):
            return call("get_recommendations", cuenta=accounts[0] if accounts else None)
        if re.search(r"\b(bajo rendimiento|funcionan peor|rinden peor|peores anuncios)\b", normalized):
            return call("get_low_performers")
        performance = re.search(r"\b(funciona|funcionan|rinde|rinden|mejor resultado|mejores resultados)\b", normalized)
        if re.search(r"\b(estadistica|estadisticas|visualizaciones|visitas|favoritos|rendimiento)\b", normalized) or (
            performance and re.search(r"\b(habitacion|luz|estilo|imagen|imagenes|dia|dias|horario|hora|titulo|descripcion)\b", normalized)
        ):
            if re.search(r"\b(actualiza|actualizar|lee|leer|refresca|refrescar|recoge|obtener|obten)\b", normalized):
                return call("refresh_statistics", cuenta=accounts[0] if accounts else None)
            aspect = None
            for word, key in (
                ("habitacion", "habitacion"), ("luz", "luz"), ("estilo", "estilo"),
                ("imagen", "origen_imagen"), ("imagenes", "origen_imagen"), ("dia", "dia"),
                ("dias", "dia"), ("horario", "franja"), ("hora", "franja"), ("titulo", "titulo"),
                ("descripcion", "descripcion"),
            ):
                if re.search(rf"\b{word}\b", normalized):
                    aspect = key
                    break
            if aspect or re.search(r"\b(analiza|analizar|analisis|patron|patrones|funciona mejor|funcionan mejor)\b", normalized):
                return call("analyze_statistics", aspecto=aspect, cuenta=accounts[0] if accounts else None)
            order = "favoritos" if "favorito" in normalized else "visualizaciones"
            return call("get_statistics", ordenar_por=order, cuenta=accounts[0] if accounts else None)

        # --- 0b. Imágenes ---
        image_number = re.search(r"\bimagen\s+(?:numero\s+|n\s*)?(\d+)\b", normalized)
        if re.search(r"\b(lista|listar|muestra|ensename|ver)\b.*\bimagenes\b", normalized):
            return call("list_images")
        if image_number and re.search(r"\b(mejora|mejorar|calidad|resolucion)\b", normalized):
            return call("enhance_image", imagen=int(image_number.group(1)))
        if image_number and re.search(r"\b(habitacion|dormitorio)\b", normalized):
            return call("change_image_room", imagen=int(image_number.group(1)), habitacion=_extract_after(normalized, "habitacion"))
        if image_number and re.search(r"\bestilo\b", normalized):
            return call("change_image_style", imagen=int(image_number.group(1)), estilo=_extract_after(normalized, "estilo"))
        if image_number and re.search(r"\b(referencia|basada|basandote)\b", normalized):
            return call("image_from_reference", imagen=int(image_number.group(1)))
        if re.search(r"\b(genera|generar|crea|crear|haz)\b.*\b(imagen|foto|fotografia)\b", normalized):
            return call("generate_image")

        # --- 0. Cola de publicación automática ---
        if re.search(r"\b(cola|publicacion automatica|publicaciones automaticas)\b", normalized) or re.search(
            r"\b(reintenta|reintentar|vuelve a intentar)\b.*\b(fallid|error)", normalized
        ):
            if re.search(r"\b(reintenta|reintentar|vuelve a intentar)\b", normalized):
                return call("retry_failed_publications")
            if re.search(r"\b(pausa|pausar|para|parar|deten|detener)\b", normalized):
                return call("pause_publish_queue")
            if re.search(r"\b(reanuda|reanudar|continua|continuar|sigue|seguir)\b", normalized):
                return call("resume_publish_queue")
            if re.search(r"\b(cancela|cancelar|anula|anular)\b", normalized):
                return call("cancel_publish_queue")
            return call("get_publish_queue")

        # --- 1. Cambiar la PLANTILLA (solo si lo dice expresamente) ---
        if explicit_template and re.search(
            r"\b(actualiza|actualizar|cambia|cambiar|modifica|modificar|pon|poner|edita)\b",
            normalized,
        ):
            changes: dict[str, Any] = {}
            if "precio" in normalized:
                price = _extract_price(normalized)
                if price is not None and not size:
                    changes["precio"] = price
            if "titulo" in normalized and quoted:
                changes["titulo"] = quoted
            elif "descripcion" in normalized and quoted:
                changes["descripcion"] = quoted
            whatsapp = re.search(r"whatsapp\D*(\d[\d ]{7,})", normalized)
            if whatsapp:
                changes["whatsapp"] = whatsapp.group(1).replace(" ", "")
            if changes:
                return call("update_master_ad", cambios=changes)
            return call("get_master_ad")

        # --- 2. Mensajes y compradores ---
        if re.search(r"\b(mensaje|mensajes|comprador|compradores|cliente|clientes|conversacion)", normalized):
            if re.search(r"\b(responde|responder|contesta|contestar|respuesta|prepara|genera)\b", normalized):
                number = re.search(r"\b(?:conversacion|numero|n)\s*(\d+)\b", normalized)
                return call(
                    "prepare_message_response",
                    conversacion=int(number.group(1)) if number else None,
                )
            unread = bool(re.search(r"\b(nuevo|nuevos|sin leer|pendiente|pendientes)\b", normalized))
            return call("get_messages", solo_sin_leer=unread or None)

        # --- 3. Cambio de precio ---
        if re.search(r"\b(cambia|cambiar|pon|poner|actualiza|sube|baja|modifica|deja)\b.*\bprecio\b", normalized) or (
            re.search(r"\bprecio\b", normalized) and re.search(r"\ba\s+\d", normalized)
        ):
            number = re.search(r"\banuncio\s+(?:n\s*)?(\d+)\b", normalized)
            without_id = re.sub(r"\banuncio\s+(?:n\s*)?\d+\b", "anuncio", normalized)
            price = _extract_price(without_id)
            if price is None:
                return None
            singular = bool(
                re.search(r"\b(este anuncio|esta publicacion|ese anuncio|el anuncio)\b", normalized)
            )
            return call(
                "update_price",
                precio=price,
                anuncios=[int(number.group(1))] if number else None,
                medida=size,
                texto=term,
                cuentas=accounts,
                solo_uno=True if singular and not number else None,
            )

        # --- 4. Publicar / preparar el anuncio principal ---
        publish_verb = re.search(
            r"\b(publica|publicar|sube|subir|crea|crear|lanza|lanzar|pon a la venta)\b", normalized
        )
        prepare_verb = re.search(r"\b(prepara|preparar|vista previa|previsualiza)\b", normalized)
        if (publish_verb or prepare_verb) and (
            mentions_master or re.search(r"\beste anuncio\b", normalized)
        ) and not quoted:
            if prepare_verb and not publish_verb:
                return call("preview_master_ad", copias=copies, cuentas=accounts)
            return call("publish_master_ad", copias=copies, cuentas=accounts)

        # --- 5. Análisis de precios / mercado ---
        if re.search(r"\b(analiza|analizar|compara|comparar)\b.*\b(precio|precios|mercado)\b", normalized):
            if "mercado" in normalized:
                return call("get_market_data", consulta=term or "", medida=size)
            return call("analyze_prices", texto=term, medida=size, cuentas=accounts)

        # --- 6. Duplicados ---
        if "duplicad" in normalized or "repetid" in normalized:
            if "imagen" in normalized or "foto" in normalized:
                return call("find_duplicate_images")
            ambito = "anuncios" if "anuncio" in normalized else ("productos" if "producto" in normalized else "todo")
            return call("detect_duplicates", ambito=ambito, texto=term)

        # --- 7. Revisión de calidad ---
        if re.search(r"\b(revisa|revisar|comprueba|comprobar|dime)\b", normalized) and re.search(
            r"\b(incomplet|incorrect|falta|error|mal)\w*", normalized
        ):
            if "producto" in normalized or "catalogo" in normalized:
                return call("validate_products", texto=term, medida=size)
            return call("validate_listings", texto=term, cuentas=accounts)

        show_verb = re.search(
            r"\b(busca|buscar|encuentra|muestra|muestrame|ensena|ensename|ver|lista|listar|dame|dime|cuales|que)\b",
            normalized,
        )

        # --- 8. Publicaciones del anuncio principal ---
        if show_verb and mentions_master and re.search(r"\bpublicacion", normalized):
            return call("list_master_publications")

        # --- 9. Búsqueda de anuncios ---
        if show_verb and ("anuncios" in normalized or "publicad" in normalized or accounts):
            return call("search_listings", texto=term, medida=size, cuentas=accounts)

        # --- 10. Ver el anuncio principal ---
        if mentions_master and (show_verb or re.search(r"\b(el anuncio|la plantilla)\b", normalized)):
            return call("get_master_ad")

        # --- 11. Búsqueda de productos ---
        if show_verb and ("producto" in normalized or "catalogo" in normalized):
            return call("search_products", texto=term, medida=size)

        # --- 12. Inventario / stock ---
        if "inventario" in normalized or "stock" in normalized:
            return call("get_inventory", texto=term, medida=size)

        # --- 13. Cuentas ---
        if "cuenta" in normalized and re.search(r"\b(estado|conectad|cuantas|lista|ver)\b", normalized):
            return call("get_account_status")

        # --- 14. Generación de contenido ---
        if re.search(r"\b(genera|generar|crea|redacta|escribe|mejora)\b.*\b(titulo|descripcion|etiqueta)\w*", normalized):
            if "descripcion" in normalized:
                return call("generate_description", producto=quoted)
            if "etiqueta" in normalized:
                return call("generate_tags", producto=quoted) if quoted else None
            return call("generate_title", producto=quoted)

        # --- 15. Vista previa / publicación de un producto concreto (SKU) ---
        if prepare_verb and quoted:
            return call("preview_listing", producto=quoted)
        if publish_verb and quoted:
            return call("create_listing", producto=quoted)

        # --- 16. Sincronizar ---
        if re.search(r"\b(sincroniza|sincronizar|descarga)\b", normalized):
            return call("sync_listings")

        # --- 17. Operaciones disponibles ---
        if re.search(r"\b(que puedes hacer|ayuda|funciones|operaciones)\b", normalized):
            return call("get_available_operations")

        return None

    # ------------------------------------------------------------------
    @staticmethod
    def _summarize_tool_results(content: list[Any]) -> str:
        """Convierte los resultados de las herramientas en texto legible."""
        import json

        lines: list[str] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            payload = block.get("content")
            if isinstance(payload, list):
                payload = " ".join(
                    str(p.get("text", "")) for p in payload if isinstance(p, dict)
                )
            try:
                data = json.loads(str(payload))
            except (ValueError, TypeError):
                lines.append(str(payload))
                continue
            lines.append(_format_tool_payload(data))
        return "\n\n".join(linea for linea in lines if linea) or "Hecho."

    @staticmethod
    def _help(attempted: str = "") -> str:
        prefix = ""
        if attempted:
            prefix = (
                f"No he entendido la orden «{attempted.strip()[:120]}».\n"
                f"Estoy funcionando en modo de órdenes directas (sin IA externa), "
                f"así que necesito instrucciones concretas.\n\n"
            )
        return prefix + (
            "Puedo hacer, por ejemplo:\n"
            "• «Publica el anuncio de canapé» / «Publica 10 canapés»\n"
            "• «¿Cómo va la cola?» / «Pausa la cola» / «Reanuda la cola» / «Reintenta los fallidos»\n"
            "• «Estadísticas» / «¿Qué anuncios tienen más favoritos?» / «Actualiza las estadísticas»\n"
            "• «¿Qué habitación funciona mejor?» / «Recomendaciones»\n"
            "• «Genera una imagen» / «Mejora la imagen 3» / «Cambia la habitación de la imagen 3 a dormitorio beige»\n"
            "• «Prepara el anuncio de canapé» (vista previa, sin publicar)\n"
            "• «Muéstrame los anuncios de la cuenta 1»\n"
            "• «Cambia el precio de los canapés de 135x190 a 270 €»\n"
            "• «Para este anuncio pon el precio a 12 €»\n"
            "• «Actualiza la plantilla: precio a 12 €»\n"
            "• «¿Qué mensajes nuevos hay?» / «Prepara una respuesta para este cliente»\n"
            "• «Busca duplicados» / «Revisa qué anuncios tienen información incompleta»\n"
            "• «Muéstrame el inventario» / «Estado de las cuentas»\n\n"
            "Para entender lenguaje natural libre, configura una clave de IA en "
            "Ajustes → IA."
        )


def _extract_after(normalized: str, word: str) -> str | None:
    """«cambia la habitación de la imagen 3 a dormitorio beige» → «dormitorio beige»."""
    match = re.search(rf"\b{word}\b.*?\b(?:a|al|por|en)\s+(?:un|una|el|la)?\s*([a-z ]+)$", normalized)
    return match.group(1).strip() if match else None


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            if texts:
                return " ".join(texts)
    return ""


# ---------------------------------------------------------------------------
# Formateo legible de los resultados
# ---------------------------------------------------------------------------
#: Claves de listas que sabemos mostrar y el campo que las identifica.
_LIST_FIELDS: dict[str, tuple[str, ...]] = {
    "anuncios": ("cuenta", "titulo", "precio"),
    "productos": ("sku", "nombre", "precio", "stock"),
    "cuentas": ("nombre", "estado", "anuncios_locales"),
    "inventario": ("sku", "nombre", "stock"),
    "conversaciones": ("cuenta", "comprador", "anuncio"),
    "incompletos": ("subject", "score"),
    "incorrectos": ("subject", "score"),
    "vistas_previas": ("cuenta", "titulo", "precio", "publicable"),
}

MAX_ROWS = 15


def _format_tool_payload(data: Any) -> str:
    """Convierte la respuesta de una herramienta en texto para el usuario."""
    if not isinstance(data, dict):
        return str(data)

    lines: list[str] = []
    summary = data.get("resumen")
    if summary:
        lines.append(str(summary))

    if data.get("estado") == "PENDIENTE_DE_CONFIRMACION":
        for item in data.get("plan") or []:
            lines.append(f"  • {item}")
        return "\n".join(lines)

    payload = data.get("datos") or {}
    if not isinstance(payload, dict):
        return "\n".join(lines)

    for key, fields in _LIST_FIELDS.items():
        rows = payload.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        lines.append(f"\n{key.capitalize()}:")
        for row in rows[:MAX_ROWS]:
            if isinstance(row, dict):
                parts = [
                    f"{_pretty(row.get(field))}" for field in fields if row.get(field) is not None
                ]
                lines.append("  · " + " · ".join(parts) if parts else f"  · {row}")
            else:
                lines.append(f"  · {row}")
        if len(rows) > MAX_ROWS:
            lines.append(f"  … y {len(rows) - MAX_ROWS} más.")

    for key in ("por_cuenta", "estadisticas", "resumen_calidad"):
        value = payload.get(key)
        if isinstance(value, dict) and value:
            lines.append(f"\n{key.replace('_', ' ').capitalize()}:")
            for name, count in list(value.items())[:MAX_ROWS]:
                lines.append(f"  · {name}: {_pretty(count)}")

    for key in ("grupos", "etiquetas", "sin_stock", "faltan"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            lines.append(f"\n{key.capitalize()}: " + ", ".join(str(v) for v in value[:20]))

    for key in ("titulo", "descripcion", "respuesta"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            lines.append(f"\n{key.capitalize()}:\n{value}")

    return "\n".join(lines)


def _pretty(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in list(value.items())[:5])
    return str(value)
