"""Formulario de «Subir producto» de Wallapop, rellenado por LOT Bot.

Aquí está el flujo COMPLETO de publicación. Cada paso es un método con
nombre propio y todos los selectores vienen de `wallapop_browser.yaml`
(sección `publicar.formulario`): este fichero no contiene ninguno.

    open_create_listing → choose_listing_type → fill_title → select_category →
    fill_attributes → fill_description → fill_price → upload_photos →
    verify_form → submit_listing → wait_for_publish_confirmation →
    get_published_listing_url

Reglas:
  * Nunca se resuelve ni se salta un CAPTCHA o verificación. Si aparece, se
    guarda captura, se avisa al usuario y se ESPERA a que la complete en la
    ventana y pulse «Continuar»; entonces se sigue desde el mismo paso.
  * Antes de pulsar «Publicar» se relee el formulario. Si algo no coincide
    (título, precio, descripción, características, fotos o cuenta), NO se
    publica y se dice qué campo falla.
  * Solo se da por publicado si Wallapop lo confirma (URL del anuncio o
    mensaje de éxito). Si no, el resultado es «no confirmado».
  * Cada error guarda captura y contexto en logs/navegador (sin cookies,
    tokens ni contraseñas) para poder corregir los selectores.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from lot_bot.wallapop.browser.category import (
    CategoryPicker,
    CategorySelectionError,
    debug_enabled,
)
from lot_bot.wallapop.browser.config import ATTRIBUTE_KEYS, BrowserSiteConfig, FormField
from lot_bot.wallapop.browser.driver import (
    BrowserPage,
    contains_text,
    only_as_province,
    safe_url,
    same_text,
)
from lot_bot.wallapop.browser.driver import _inside as _inside_box
from lot_bot.wallapop.errors import (
    BrowserStepError,
    FormMismatchError,
    ImageUploadError,
    VerificationRequiredError,
    WallapopError,
)

logger = logging.getLogger(__name__)

#: gate(account_ref, mensaje) -> True si el usuario pulsa «Continuar»,
#: False si cancela o se agota el tiempo.
UserGate = Callable[[str, str], bool]

ATTRIBUTE_LABELS = {
    "estado": "Estado", "uso": "Uso", "color": "Color", "material": "Material", "marca": "Marca",
}


def normalize(text: str) -> str:
    """Para comparar: sin mayúsculas, espacios repetidos ni formas Unicode distintas."""
    text = unicodedata.normalize("NFC", text or "")
    return re.sub(r"\s+", " ", text).strip().casefold()


def parse_price(text: str) -> float | None:
    match = re.search(r"\d+(?:[.,]\d{1,2})?", (text or "").replace(" ", " "))
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


@dataclass(slots=True)
class ListingData:
    """Lo que se va a publicar, EXACTAMENTE como está en la plantilla."""

    title: str
    description: str
    price: float
    price_text: str
    category: str = ""
    subcategory: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    #: Ciudad del Anuncio principal («» = la que ponga Wallapop).
    location: str = ""
    #: «Activar envío» del Anuncio principal (None = lo que diga el YAML).
    shipping: bool | None = None


@dataclass(slots=True)
class PublishReport:
    confirmed: bool
    url: str | None
    item_id: str | None
    steps: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    signal: str = ""


class ListingForm:
    def __init__(
        self,
        page: BrowserPage,
        site: BrowserSiteConfig,
        data: ListingData,
        *,
        account_ref: str,
        profile_dir: Path | None = None,
        shots_dir: Path | None = None,
        gate: UserGate | None = None,
        gate_timeout_s: float = 1800.0,
    ) -> None:
        self.page = page
        self.site = site
        self.form = site.form
        self.data = data
        self.account_ref = account_ref
        self.profile_dir = Path(profile_dir) if profile_dir else None
        self.shots_dir = shots_dir
        self.gate = gate
        self.gate_timeout_s = gate_timeout_s
        self.steps: list[str] = []
        self.skipped: list[str] = []
        self.attributes_filled: dict[str, str] = {}
        #: Campos en los que ya se ha pulsado una opción (no se repiten).
        self.clicked_steps: set[str] = set()
        self._photos_before = 0

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    @property
    def timeout(self) -> int:
        return self.site.timeout_ms

    _last_shot: str | None = None

    def save_error_context(
        self, step: str, error: str, extra: dict | None = None
    ) -> str | None:
        """Captura + contexto en logs/navegador. Nunca cookies ni tokens."""
        if self.shots_dir is None:
            return None
        slug = re.sub(r"\W+", "_", step).strip("_") or "paso"
        base = self.shots_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{self.account_ref}-{slug}"
        shot: str | None = None
        try:
            self.page.screenshot(base.with_suffix(".png"))
            shot = str(base.with_suffix(".png"))
        except Exception:
            shot = None
        try:
            url = safe_url(self.page.current_url())
        except Exception:
            url = "desconocida"
        try:
            diagnostics = self.page.diagnostics()
        except Exception:
            diagnostics = {}
        context = {
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "cuenta": self.account_ref,
            "paso": step,
            "error": error,
            "url": url,
            "pasos_completados": list(self.steps),
            "captura": Path(shot).name if shot else None,
            "configuracion": str(self.site.source) if self.site.source else None,
            "diagnostico": diagnostics,
            "nota": "Pasa esta captura y este fichero a Claude para actualizar los "
            "selectores de wallapop_browser.yaml.",
            **(extra or {}),
        }
        try:
            base.parent.mkdir(parents=True, exist_ok=True)
            base.with_suffix(".json").write_text(
                json.dumps(context, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
        except OSError:
            pass
        logger.error("Publicación: fallo en «%s» (%s). Contexto: %s", step, error, base.with_suffix(".json"))
        self._last_shot = shot
        return shot

    def _fail(self, step: str, detail: str) -> BrowserStepError:
        shot = self.save_error_context(step, detail)
        return BrowserStepError(step, detail, shot)

    def _verification_visible(self) -> bool:
        return bool(self.site.verification and self.page.first_visible(self.site.verification, 0))

    def guard(self, step: str) -> None:
        """Si Wallapop pide una verificación: esperar al usuario, NUNCA resolverla."""
        if not self._verification_visible():
            return
        self.save_error_context(f"verificacion-{step}", "Wallapop pide una verificación")
        deadline = time.monotonic() + self.gate_timeout_s
        while self._verification_visible():
            if self.gate is None or time.monotonic() > deadline:
                raise VerificationRequiredError(f"Verificación en el paso «{step}».")
            if not self.gate(self.account_ref, VerificationRequiredError.user_message):
                raise VerificationRequiredError(f"Verificación en el paso «{step}» (no completada).")
        logger.info("Verificación completada por el usuario; se sigue en «%s».", step)

    def _check_wallapop_error(self, step: str) -> None:
        if self.form.errors:
            found = self.page.first_visible(self.form.errors, 0)
            if found:
                try:
                    text = self.page.text_of(found)[:200]
                except Exception:
                    text = found
                raise self._fail(step, f"Wallapop muestra un error: {text}")

    #: Espera corta antes de probar con «Continuar» (formulario por pasos).
    QUICK_MS = 1000

    def _find(self, step: str, spec: FormField, *, wait: bool = True) -> str | None:
        """Busca el campo. Si no está en la pantalla actual del formulario y
        hay un botón «Continuar» del propio formulario, lo pulsa y lo vuelve a
        buscar (Wallapop pide los datos en varias pantallas)."""
        target = self.page.first_visible(spec.targets, min(self.timeout, self.QUICK_MS) if wait else 0)
        if target is None and wait:
            self.guard(step)  # ¿apareció una verificación mientras esperábamos?
            if self.continue_if_present(f"Continuar (antes de {step})", wait_ms=0):
                target = self.page.first_visible(spec.targets, self.timeout)
            else:
                target = self.page.first_visible(spec.targets, self.timeout)
        if target is None:
            self.guard(step)
            target = self.page.first_visible(spec.targets, 0)
        return target

    def continue_if_present(self, step: str = "Continuar", *, wait_ms: int = 2000) -> bool:
        """Pulsa el «Continuar» DEL FORMULARIO si aparece (nunca menús de la web)."""
        spec = self.form.next_button
        if not spec.targets:
            return False
        target = self.page.first_visible(spec.targets, wait_ms)
        if target is None:
            return False
        self._do(step, lambda: self.page.click(target))
        return True

    def _require(self, step: str, spec: FormField) -> str:
        target = self._find(step, spec)
        if target is None:
            raise self._fail(step, f"No aparece ningún elemento de: {spec.targets}")
        return target

    def _do(self, step: str, action: Callable[[], Any]) -> None:
        """Ejecuta un paso con control de verificación y errores."""
        self.guard(step)
        try:
            action()
        except WallapopError:
            raise
        except Exception as exc:
            raise self._fail(step, f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}") from exc
        self._check_wallapop_error(step)
        self.steps.append(step)
        logger.info("Publicación [%s]: paso «%s» hecho.", self.account_ref, step)

    def _type(self, step: str, spec: FormField, text: str) -> None:
        def action() -> None:
            target = self._require(step, spec)
            self.page.fill(target, text)

        self._do(step, action)

    def _choose(
        self, step: str, spec: FormField, option: str, alternatives: list[str] | None = None
    ) -> str | None:
        """Abre un desplegable y elige la opción (o la primera equivalencia
        que exista). Devuelve lo elegido, o None si el campo opcional no está."""
        if not option:
            self.skipped.append(f"{step} (sin valor en la plantilla)")
            return None
        if spec.optional and self._find(step, spec, wait=False) is None:
            # Un campo opcional puede tardar un poco en aparecer tras elegir la categoría.
            if self.page.first_visible(spec.targets, min(self.timeout, self.QUICK_MS)) is None:
                self.skipped.append(f"{step} (el formulario no tiene este campo)")
                return None
        candidates = [option, *[a for a in (alternatives or []) if a and a != option]]
        chosen: list[str] = []
        # Si Wallapop (o su IA) ya lo ha puesto, NO se vuelve a pulsar: en las
        # listas de varias opciones (Color) pulsarlo otra vez lo QUITA.
        current_target = self._find(step, spec, wait=False)
        if current_target is not None:
            try:
                current = self._field_text(current_target)
            except Exception:
                current = ""
            already = next((c for c in candidates if contains_text(c, current)), None)
            if already:
                self.steps.append(f"{step} (ya estaba: {already})")
                return already

        def option_showing(candidate: str, box: dict | None) -> bool:
            """¿Se ve YA la opción en una lista abierta? (visible, no es un texto
            que estaba antes, y no es el propio campo)."""
            for c in self.page.text_candidates(candidate):
                if not c.get("visible") or c.get("previo") or not c.get("box"):
                    continue
                if box and _inside_box(c["box"], box):
                    continue
                return True
            return False

        def pick(target: str, box: dict | None) -> str | None:
            """Abre la lista (si no se ve ya la opción) y pulsa la opción."""
            self.page.mark_existing(candidates)
            try:
                cleared = False
                for candidate in candidates:
                    if not option_showing(candidate, box):
                        self.page.click(target)
                        cleared = False  # lista abierta de nuevo: se vuelve a vaciar
                    if not cleared and self.form.list_clear:
                        # Lista de varias opciones: se vacía primero («Borrar»)
                        # para que el clic MARQUE (si ya estaba, lo quitaría).
                        # Posición del campo AHORA (al abrir, la página se desplaza).
                        cleared = self.page.click_text_near(
                            self.form.list_clear, self.page.bbox(target) or box, 700
                        )
                        if cleared:
                            self.page.wait(250)
                    # Espera acotada: si no aparece, se falla rápido (con
                    # diagnóstico) en vez de quedarse parado.
                    wait = min(self.timeout, 8000) if len(candidates) == 1 else min(self.timeout, 2500)
                    if self.page.click_option(candidate, wait, opener=target, opener_box=box):
                        return candidate
            finally:
                self.page.unmark_existing()
            return None

        def shows(target: str, value: str, seconds: float) -> tuple[bool, str | None]:
            deadline = time.monotonic() + seconds
            while True:
                try:
                    current = self._field_text(target)
                except Exception:
                    current = None
                if current is None or contains_text(value, current):
                    return True, current
                if time.monotonic() > deadline:
                    return False, current
                self.page.wait(200)

        def action() -> None:
            target = self._require(step, spec)
            box = self.page.bbox(target)  # ANTES de abrir la lista
            current = None
            for attempt in range(2):
                picked = pick(target, box)
                if picked is not None:
                    # Ya se ha pulsado una opción de este campo: la revisión
                    # final NO lo vuelve a tocar (otro clic la desmarcaría).
                    self.clicked_steps.add(step)
                if picked is None:
                    # El clic puede haber funcionado aunque no se confirme
                    # (lista que se cierra sola): si el campo YA lo muestra, vale.
                    self.close_open_lists()
                    for candidate in candidates:
                        ok, current = shows(target, candidate, 2)
                        if ok and current:
                            chosen.append(candidate)
                            return
                    self._dump_field_html(step, target)
                    raise self._fail(step, f"No aparece la opción «{option}» (probado: {candidates}).")
                # Lista que sigue abierta tras el clic = de VARIAS opciones
                # (Color, Material): pulsar otra vez la opción la QUITARÍA. Ahí
                # se espera más y no se repite; si al final no está, lo
                # completa la revisión final.
                multi = self.page.first_visible(self.OPEN_LISTS, 0) is not None
                # El «Seleccionar» de ESTA lista (la que cuelga del campo), no
                # el de otra lista de la página.
                if self.form.list_confirm_texts:
                    self.page.click_text_near(
                        self.form.list_confirm_texts, self.page.bbox(target) or box, 0, below=900
                    )
                self.close_open_lists()
                ok, current = shows(target, picked, 8 if multi else (3 if attempt == 0 else 6))
                if ok:
                    chosen.append(picked)
                    return
                if multi:
                    break
                logger.info("%s: «%s» no ha quedado puesto (intento %d); se repite.", step, picked, attempt + 1)
            logger.info("%s: «%s» pulsado pero el campo muestra «%s».", step, picked, current)
            self._dump_field_html(step, target)
            error = self._fail(step, f"Se ha elegido «{candidates[0]}» pero el campo muestra «{current}».")
            # Otro valor de verdad (no vacío ni el nombre del campo): no se
            # publica nada equivocado.
            label = step.split()[-1]
            shown = (current or "").strip().rstrip("*").strip()
            error.wrong_value = bool(shown) and not contains_text(label, shown)
            raise error

        self._do(step, action)
        if chosen and chosen[0] != option:
            self.skipped.append(f"{step}: Wallapop no tiene «{option}»; elegido «{chosen[0]}»")
        return chosen[0] if chosen else None

    def _dump_field_html(self, step: str, target: str) -> None:
        """Guarda el HTML de la zona del campo (y de la lista abierta) para
        poder ajustar el bot a la web real. Sin cookies ni datos privados."""
        html = ""
        for zone in (f"{target} >> xpath=ancestor::*[3]", f"{target} >> xpath=ancestor::*[2]", target):
            html = self.page.outer_html(zone, 30000)
            if html:
                break
        lists = self.page.outer_html("[role=listbox]", 15000)
        self.save_error_context(
            f"{step}-html", "HTML del campo para diagnóstico",
            extra={"html_campo": html, "html_lista_abierta": lists},
        )

    #: Listas desplegables abiertas (si quedan abiertas, tapan otros campos).
    OPEN_LISTS = ["[role=listbox]", "[role=menu]", "[role=option]"]

    def close_open_lists(self) -> None:
        """Cierra la lista que haya quedado abierta (p. ej. Color, que admite
        varias opciones y no se cierra sola) SIN deshacer la selección: primero
        su botón de aceptar si lo tiene; si no, un clic fuera (en un título del
        formulario), como haría una persona. Escape solo como último recurso
        (en algunas listas de Wallapop, Escape CANCELA lo elegido)."""
        confirm = self.page.first_visible(self.form.list_confirm, 300) if self.form.list_confirm else None
        if confirm:
            self.page.click(confirm)
            self.page.wait(150)
        neutral = self.page.first_visible(self.form.neutral_click, 0) if self.form.neutral_click else None
        if neutral:
            self.page.click(neutral)
            self.page.wait(150)
        if self.page.first_visible(self.OPEN_LISTS, 300):
            self.page.press("Escape")
            self.page.wait(200)

    def _choose_path(self, step: str, spec: FormField, path: str) -> str:
        """Categoría «A > B > Estructura de camas» con `CategoryPicker`:
        abre, recorre los niveles y VERIFICA que la final ha quedado puesta."""
        parts = [p.strip() for p in path.split(">") if p.strip()]
        if len(parts) == 1:  # solo el nombre final: ruta completa si se conoce
            known = next(
                (v for k, v in self.form.category_paths.items() if same_text(parts[0], k)), None
            )
            if known:
                parts = [p.strip() for p in known.split(">") if p.strip()]
        picker = CategoryPicker(
            self.page,
            opener_targets=spec.targets,
            panel_targets=self.form.category_panel,
            selected_targets=self.form.category_selected,
            timeout_ms=self.timeout,
            accept_timeout_ms=max(self.timeout, min(self.form.prepare_wait_ms, 30000)),
        )
        self.guard(step)
        try:
            leaf = picker.select(parts)
        except CategorySelectionError as exc:
            self.save_error_context(step, exc.reason, extra={"categoria": exc.diagnostics})
            raise BrowserStepError(step, exc.reason, self._last_shot) from exc
        except WallapopError:
            raise
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}"
            self.save_error_context(step, reason, extra={"categoria": picker._diag(parts)})
            raise BrowserStepError(step, reason, self._last_shot) from exc
        if debug_enabled():
            self.save_error_context("categoria-ok", f"Seleccionada «{leaf}»",
                                    extra={"categoria": {"pasos": picker.trace[-40:]}})
        self._check_wallapop_error(step)
        self.steps.append(step)
        return leaf

    # ------------------------------------------------------------------
    # Pasos
    # ------------------------------------------------------------------
    def open_create_listing(self) -> None:
        url = self.site.url(self.form.open_url)

        def open_() -> None:
            # Si la comprobación de sesión ya dejó abierto el formulario (misma
            # dirección), no se vuelve a cargar: un viaje menos por anuncio.
            try:
                current = self.page.current_url()
            except Exception:
                current = ""
            if current.split("#")[0] != url.split("#")[0]:
                self.page.goto(url)

        self._do("Abrir crear anuncio", open_)

    def choose_listing_type(self) -> None:
        spec = self.form.listing_type
        if not spec.targets:
            return
        target = self.page.first_visible(spec.targets, min(self.timeout, 5000))
        if target is None:
            if spec.optional:
                self.skipped.append("Tipo de anuncio (no se pregunta)")
                return
            raise self._fail("Tipo de anuncio", f"No aparece: {spec.targets}")
        self._do("Tipo de anuncio", lambda: self.page.click(target))

    def fill_title(self) -> None:
        self._type("Título", self.form.title, self.data.title)
        # Tras el título Wallapop muestra «Continuar»: se pulsa ahí, no se va
        # al menú «Categorías» de arriba (ese es el buscador de la web).
        self.continue_if_present("Continuar tras el título")

    def select_category(self) -> None:
        if not self.data.category:
            raise self._fail("Categoría", "La plantilla no tiene categoría.")
        self.category_chosen = self._choose_path("Categoría", self.form.category, self.data.category)
        if self.data.subcategory:
            self._choose("Subcategoría", self.form.subcategory, self.data.subcategory)

    def fill_attributes(self) -> None:
        for key in ATTRIBUTE_KEYS:
            value = self.data.attributes.get(key, "")
            spec = self.form.attributes.get(key)
            if not value:
                continue
            label = f"Característica {ATTRIBUTE_LABELS.get(key, key)}"
            if spec is None or not spec.targets:
                self.skipped.append(f"{label} (sin selectores en el YAML)")
                continue
            alternatives = (self.form.equivalents.get(key) or {}).get(value) or []
            # Si no se puede poner, NO para el resto (precio, envío, ubicación
            # y «Publicar» se hacen igual). Si queda OTRO valor, sí para.
            try:
                if key == "marca":
                    chosen = self._fill_brand(label, spec, value)
                else:
                    chosen = self._choose(label, spec, value, alternatives)
            except BrowserStepError as exc:
                if getattr(exc, "wrong_value", False):
                    raise
                detail = getattr(exc, "detail", "") or str(exc)
                logger.warning("%s: no se ha podido completar (%s). Se sigue.", label, detail)
                self.skipped.append(f"{label}: NO se ha podido completar ({detail[:160]})")
                self.close_open_lists()
                continue
            if chosen:
                self.attributes_filled[key] = chosen

    def _fill_brand(self, label: str, spec: FormField, brand: str) -> str | None:
        """Marca del Anuncio principal. Si el campo es para ESCRIBIR, se escribe
        y se elige la sugerencia que coincide (si Wallapop la ofrece); si es un
        desplegable, se elige como el color o el material. Siempre se comprueba."""
        target = self._find(label, spec, wait=False)
        if target is None:
            target = self.page.first_visible(spec.targets, min(self.timeout, self.QUICK_MS))
        if target is None:
            if spec.optional:
                self.skipped.append(f"{label} (el formulario no tiene este campo)")
                return None
            raise self._fail(label, f"No aparece ningún elemento de: {spec.targets}")
        try:
            current = self.page.value_of(target)
        except Exception:
            current = ""
        if current and contains_text(brand, current):
            self.steps.append(f"{label} (ya estaba: {brand})")
            return brand
        if not self.page.is_text_input(target):
            return self._choose(label, spec, brand, [])

        def action() -> None:
            self.page.type_text(target, brand)
            # Sugerencia con esa marca (si la hay); si no, se queda lo escrito.
            self.page.click_suggestion(brand, min(self.timeout, 3000))
            self.close_open_lists()
            shown = ""
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    shown = self.page.value_of(target)
                except Exception:
                    shown = ""
                if contains_text(brand, shown):
                    return
                self.page.wait(200)
            raise self._fail(label, f"Tras escribir «{brand}» el campo muestra «{shown}».")

        self._do(label, action)
        return brand

    def wait_until_details_ready(self) -> None:
        """Tras la categoría, Wallapop se queda «preparando» el resto del
        formulario (a veces con su IA). Se ESPERA a que aparezcan los campos
        de detalles (precio, estado, descripción…) antes de tocarlos."""
        targets = [
            *self.form.price.targets,
            *self.form.description.targets,
            *(self.form.attributes.get("estado").targets if self.form.attributes.get("estado") else []),
        ]
        deadline = time.monotonic() + self.form.prepare_wait_ms / 1000
        while time.monotonic() < deadline:
            self.guard("Preparando detalles")
            if self.page.first_visible(targets, 0):
                self.steps.append("Detalles listos")
                return
            if self.continue_if_present("Continuar tras la categoría", wait_ms=0):
                continue
            self.page.wait(250)
        raise self._fail(
            "Preparando detalles",
            "Tras elegir la categoría, Wallapop no ha mostrado los campos de precio/estado/"
            "descripción a tiempo.",
        )

    def _not_blocking(self, name: str, step) -> None:
        try:
            step()
        except (BrowserStepError, WallapopError) as exc:
            if isinstance(exc, VerificationRequiredError):
                raise
            detail = getattr(exc, "detail", "") or str(exc)
            logger.warning("%s: no se ha podido completar (%s). Se publica igual.", name, detail)
            self.skipped.append(f"{name}: NO se ha podido completar ({detail[:160]})")
            self.close_open_lists()

    def set_shipping(self) -> None:
        """«Activar envío» apagado (o como diga el YAML). El interruptor de
        Wallapop suele ser un dibujo encima de una casilla OCULTA: se busca
        aunque no se vea y se desmarca con su etiqueta/casilla."""
        spec = self.form.shipping
        if not spec.targets:
            return
        target = self.page.first_visible(spec.targets, min(self.timeout, 3000))
        if target is None:
            target = next((t for t in spec.targets if self.page.attached(t)), None)
        if target is None:
            self.skipped.append("Envío (no aparece el interruptor «Activar envío»)")
            return
        want = self.form.shipping_enabled if self.data.shipping is None else self.data.shipping
        label = "Envío " + ("activado" if want else "desactivado")
        if self.page.is_checked(target) == want:
            self.steps.append(label + " (ya estaba)")
            return
        self._do(label, lambda: self.page.set_checked(target, want))
        if self.page.is_checked(target) != want:
            raise self._fail("Envío", "No se ha podido dejar «Activar envío» como se indica.")

    def set_location(self) -> None:
        """Ubicación del Anuncio principal. Wallapop pone la de la cuenta; si
        ya muestra la ciudad pedida, no se toca."""
        city = (self.data.location or "").strip()
        if not city:
            return
        spec = self.form.location
        opener = self.page.first_visible(spec.targets, min(self.timeout, 3000)) if spec.targets else None
        if opener is None:
            self.skipped.append(f"Ubicación «{city}» (el formulario no muestra el campo)")
            return
        try:
            # La caja muestra la etiqueta y, debajo, la dirección
            # («Marca la localización» / «08007, Barcelona»): se lee entera.
            if self._shows_city(city, self._field_text(opener)):
                self.steps.append(f"Ubicación {city} (ya estaba)")
                return
        except Exception:
            pass

        def action() -> None:
            # 1) El campo donde se escribe: el mismo, o el que se abre al pulsar
            #    la caja «Marca la localización». Nunca el buscador de arriba.
            field_ = self.page.first_visible(self.form.location_input, 0)
            if field_ is None:
                self.page.click(opener)
                field_ = self.page.first_visible(self.form.location_input, min(self.timeout, 6000))
            if field_ is None:
                raise self._fail("Ubicación", "Al pulsar «Marca la localización» no aparece dónde escribir la ciudad.")
            # 2) Se escribe tecla a tecla y se elige la sugerencia (con el
            #    ratón o, si algo la tapa, con las flechas y Enter). El mapa no
            #    se toca.
            self.page.type_text(field_, city)
            if not self.page.click_suggestion(city, min(self.timeout, 8000)):
                self.page.press("ArrowDown")
                self.page.press("Enter")
            confirm = self.page.first_visible(self.form.location_confirm, 800) if self.form.location_confirm else None
            if confirm:
                self.page.click(confirm)
            # 3) Comprobar que ha quedado la ciudad (en el campo o en la caja).
            deadline = time.monotonic() + 5
            shown = None
            while time.monotonic() < deadline:
                for where in (field_, opener):
                    try:
                        shown = self._field_text(where)
                    except Exception:
                        continue
                    if self._shows_city(city, shown):
                        return
                self.page.wait(250)
            raise self._fail("Ubicación", f"Tras escribir «{city}» se muestra «{shown}».")

        self._do(f"Ubicación {city}", action)

    def review_and_complete(self) -> None:
        """Revisión final antes de publicar: si algún campo del Anuncio
        principal ha quedado vacío (p. ej. Color o Material), se vuelve a
        poner. Lo que ya está bien NO se toca (pulsarlo otra vez lo quitaría)."""
        self.close_open_lists()
        for key in ATTRIBUTE_KEYS:
            value = self.data.attributes.get(key, "")
            spec = self.form.attributes.get(key)
            if not value or spec is None or not spec.targets:
                continue
            label = f"Característica {ATTRIBUTE_LABELS.get(key, key)}"
            if label in self.clicked_steps or key in self.attributes_filled:
                # Ya se eligió (o ya estaba): NUNCA se vuelve a pulsar, porque en
                # Color y Material otro clic DESMARCA lo que hay.
                continue
            if self._find(label, spec, wait=False) is None:
                continue  # el formulario no tiene este campo
            expected = [self.attributes_filled.get(key), value,
                        *((self.form.equivalents.get(key) or {}).get(value) or [])]
            if self._field_has(spec, [e for e in expected if e], wait_s=3):
                continue
            shown = (self._read_choice(spec) or "").strip().rstrip("*").strip()
            if shown and not contains_text(ATTRIBUTE_LABELS.get(key, key), shown):
                continue  # muestra OTRO valor: lo decide la comprobación final
            logger.info("Revisión final: «%s» está vacío; se vuelve a poner «%s».", label, value)
            self.attributes_filled.pop(key, None)

            def again(key=key, label=label, spec=spec, value=value) -> None:
                if key == "marca":
                    chosen = self._fill_brand(f"{label} (revisión)", spec, value)
                else:
                    alternatives = (self.form.equivalents.get(key) or {}).get(value) or []
                    chosen = self._choose(f"{label} (revisión)", spec, value, alternatives)
                if chosen:
                    self.attributes_filled[key] = chosen

            self._not_blocking(f"{label} (revisión)", again)
            if key in self.attributes_filled:
                # Ya está: fuera el aviso de que no se pudo la primera vez.
                self.skipped[:] = [
                    s for s in self.skipped if not (s.startswith(label) and "NO se ha podido" in s)
                ]
        price = self._read(self.form.price)
        value = parse_price(price or "")
        if self.form.price.targets and (value is None or abs(value - float(self.data.price)) > 0.001):
            logger.info("Revisión final: el precio muestra «%s»; se vuelve a poner.", price)
            self._not_blocking("Precio (revisión)", self.fill_price)
        # Envío: solo si la primera vez NO se pudo. La ubicación no se repite
        # (se escribía varias veces).
        for name, step in (("Envío", self.set_shipping),):
            failed = [x for x in self.skipped if x.startswith(f"{name}:") and "NO se ha podido" in x]
            if failed:
                self.skipped[:] = [x for x in self.skipped if x not in failed]
                self._not_blocking(f"{name} (revisión)", step)

    def _field_has(self, spec: FormField, values: list[str], wait_s: float) -> bool:
        deadline = time.monotonic() + wait_s
        while True:
            current = self._read_choice(spec)
            if current and any(contains_text(v, current) for v in values):
                return True
            if time.monotonic() > deadline:
                return False
            self.page.wait(250)

    @staticmethod
    def _shows_city(city: str, shown: str | None) -> bool:
        """¿Muestra esa ciudad? «Sant Feliu de Llobregat, Barcelona» NO es
        «Barcelona» (es otra ciudad de la provincia)."""
        return bool(shown) and contains_text(city, shown) and not only_as_province(city, shown)

    def refill_if_overwritten(self) -> None:
        """Wallapop puede rellenar título y descripción con su IA DESPUÉS de
        que los escribamos: si no son los del Anuncio principal, se vuelven a
        poner (una vez) antes de comprobar el formulario."""
        checks = [
            ("Título (revisión)", self.form.final_title, self.data.title),
            ("Descripción", self.form.description, self.data.description),
        ]
        for step, spec, expected in checks:
            if not spec.targets:
                continue
            current = self._read(spec)
            if current is not None and normalize(current) != normalize(expected):
                logger.info("Wallapop ha cambiado «%s»: se vuelve a poner el del Anuncio principal.", step)
                self._type(f"{step} (de nuevo)", spec, expected)

    def fill_final_title(self) -> None:
        """En «Revisa la información» Wallapop cambia el título con su IA: se
        vuelve a poner el de la plantilla."""
        spec = self.form.final_title
        if not spec.targets:
            return
        if self.page.first_visible(spec.targets, min(self.timeout, 4000)) is None:
            self.skipped.append("Título en «Revisa la información» (no aparece)")
            return
        self._type("Título (revisión)", spec, self.data.title)

    def fill_description(self) -> None:
        self._type("Descripción", self.form.description, self.data.description)

    def fill_price(self) -> None:
        self._type("Precio", self.form.price, self.data.price_text)

    def _thumb_count(self, thumbs: list[str]) -> int:
        """Miniaturas visibles. Se usa el selector que más encuentre (no se
        suman: varios selectores pueden señalar la misma miniatura)."""
        return max((self.page.count(t) for t in thumbs), default=0)

    def prepare_images(self, images: list[str]) -> list[str]:
        """Copias JPEG ligeras (máx. 2048 px) para que Wallapop las acepte y
        se suban rápido. El original no se toca. Si no se puede convertir,
        se usa el original."""
        prepared: list[str] = []
        folder = Path(tempfile.gettempdir()) / "lotbot-subida"
        folder.mkdir(parents=True, exist_ok=True)
        for index, path in enumerate(images):
            try:
                from PIL import Image, ImageOps

                with Image.open(path) as img:
                    img = ImageOps.exif_transpose(img).convert("RGB")
                    img.thumbnail((2048, 2048))
                    target = folder / f"{self.account_ref}-{index}-{Path(path).stem}.jpg"
                    img.save(target, "JPEG", quality=90, optimize=True)
                prepared.append(str(target))
            except Exception:
                logger.warning("No se ha podido preparar %s; se sube el original.", Path(path).name)
                prepared.append(str(path))
        return prepared

    def _send_files(self, step: str, images: list[str]) -> str:
        """Entrega las fotos a Wallapop. Devuelve cómo se hizo (para el registro).

        1. Campo de archivos existente (aunque esté oculto).
        2. Si no existe: botón «Subir fotos» + ventana «Abrir archivo».
        3. Si no hay ninguno en esta pantalla: «Continuar» del formulario y otra vez.
        """
        photos = self.form.photos
        deadline = time.monotonic() + self.timeout / 1000
        continued = False
        while True:
            for target in photos.targets:
                if self.page.attached(target):
                    self.page.set_files(target, images)
                    return f"campo {target}"
            button = self.page.first_visible(self.form.photo_buttons, 0) if self.form.photo_buttons else None
            if button:
                self.page.upload_with_chooser(button, images, min(self.timeout, 10000))
                return f"botón {button}"
            if not continued and self.continue_if_present(f"Continuar (antes de {step})", wait_ms=0):
                continued = True
                continue
            if time.monotonic() > deadline:
                raise self._fail(
                    step,
                    "No se encuentra dónde subir las fotos (ni campo de archivos ni botón). "
                    f"Selectores: {photos.targets + self.form.photo_buttons}",
                )
            self.guard(step)
            self.page.wait(250)

    def upload_photos(self) -> None:
        step = "Fotos"
        images = [p for p in self.data.images if Path(p).is_file()]
        if not images:
            raise self._fail(
                step,
                "No hay ninguna foto preparada para este anuncio (sube una en «Anuncio "
                "principal → Fotografías» o activa FLUX).",
            )
        images = self.prepare_images(images)
        thumbs = self.form.photo_thumbnails

        def action() -> None:
            self._photos_before = self._thumb_count(thumbs) if thumbs else 0
            how = self._send_files(step, images)
            logger.info("Fotos entregadas a Wallapop (%s): %d.", how, len(images))

        self._do(step, action)
        # Comprobar que Wallapop HA CARGADO las fotos (aparece la miniatura).
        if not thumbs:
            raise self._fail(step, "El YAML no indica cómo reconocer una foto cargada (miniaturas).")
        deadline = time.monotonic() + self.form.photo_wait_ms / 1000
        while True:
            self.guard(step)
            if self.form.photo_rejected and self.page.first_visible(self.form.photo_rejected, 0):
                shot = self.save_error_context(step, "Wallapop ha rechazado la foto")
                raise ImageUploadError("Wallapop ha rechazado la foto.", shot)
            loaded = self._thumb_count(thumbs) - self._photos_before
            if loaded >= len(images):
                return
            if time.monotonic() > deadline:
                shot = self.save_error_context(step, f"Fotos cargadas {loaded} de {len(images)}")
                raise ImageUploadError(f"cargadas {max(loaded, 0)} de {len(images)}", shot)
            self.page.wait(200)

    # ------------------------------------------------------------------
    # Comprobación antes de publicar
    # ------------------------------------------------------------------
    def _field_text(self, target: str) -> str:
        """Lo que muestra un desplegable: su texto y el de su recuadro. En
        Wallapop lo elegido en Color/Material se ve en el recuadro, FUERA del
        botón (que solo dice «Color*»): sin esto el bot lo creía vacío."""
        own = self.page.value_of(target)
        try:
            around = self.page.value_of(f"{target} >> xpath=..")
        except Exception:
            around = ""
        return own if not around or around == own else f"{own} {around}"

    def _read_choice(self, spec: FormField) -> str | None:
        for target in (spec.readback or spec.targets):
            try:
                if self.page.first_visible([target], 0):
                    return self._field_text(target)
            except Exception:
                continue
        return None

    def _read(self, spec: FormField) -> str | None:
        for target in (spec.readback or spec.targets):
            try:
                if self.page.first_visible([target], 0):
                    return self.page.value_of(target)
            except Exception:
                continue
        return None

    def form_problems(self) -> dict[str, str]:
        problems: dict[str, str] = {}
        title = self._read(self.form.final_title) if self.form.final_title.targets else None
        if title is None:
            title = self._read(self.form.title)
        if title is None or normalize(title) != normalize(self.data.title):
            problems["Título"] = f"se esperaba «{self.data.title}», hay «{title}»"
        description = self._read(self.form.description)
        if description is None or normalize(description) != normalize(self.data.description):
            problems["Descripción"] = "el texto del formulario no es el de la plantilla"
        price = self._read(self.form.price)
        value = parse_price(price or "")
        if value is None or abs(value - float(self.data.price)) > 0.001:
            expected = f"{self.data.price:.2f}".replace(".", ",")
            problems["Precio"] = f"se esperaba {expected} €, hay «{price}»"
        category = self._read(self.form.category)
        leaf = getattr(self, "category_chosen", None) or self.data.category.split(">")[-1].strip()
        verified = getattr(self, "category_chosen", None)  # ya verificada al elegirla
        if not verified and category is not None and not contains_text(leaf, category):
            problems["Categoría"] = f"se esperaba «{leaf}», hay «{category}»"
        for key, expected in self.attributes_filled.items():
            spec = self.form.attributes[key]
            current = self._read_choice(spec)
            if current is None or not contains_text(expected, current):
                problems[ATTRIBUTE_LABELS.get(key, key)] = f"se esperaba «{expected}», hay «{current}»"
        thumbs = self.form.photo_thumbnails
        loaded = self._thumb_count(thumbs) - self._photos_before if thumbs else 0
        if loaded < len(self.data.images):
            problems["Fotos"] = f"cargadas {max(loaded, 0)} de {len(self.data.images)}"
        if self.profile_dir is not None and self.profile_dir.name != self.account_ref:
            problems["Cuenta"] = f"el navegador usa el perfil «{self.profile_dir.name}»"
        return problems

    def verify_form(self) -> None:
        self.guard("Comprobar formulario")
        problems = self.form_problems()
        if problems:
            shot = self.save_error_context("comprobar-formulario", json.dumps(problems, ensure_ascii=False))
            raise FormMismatchError(problems, shot)
        self.steps.append("Comprobar formulario")

    # ------------------------------------------------------------------
    # Publicar y confirmar
    # ------------------------------------------------------------------
    def submit_listing(self) -> None:
        self._do("Publicar", lambda: self.page.click(self._require("Publicar", self.form.submit)))

    def wait_for_publish_confirmation(self) -> str:
        """Devuelve la señal de éxito ('url' o 'texto') o '' si no se confirma."""
        deadline = time.monotonic() + self.site.success_timeout_ms / 1000
        url_regex = re.compile(self.site.success_url_regex) if self.site.success_url_regex else None
        while True:
            self.guard("Confirmación")
            self._check_wallapop_error("Confirmación")
            if url_regex and url_regex.search(self.page.current_url()):
                return "url"
            if self.site.success_texts and self.page.first_visible(self.site.success_texts, 0):
                return "texto"
            if time.monotonic() > deadline:
                self.save_error_context("confirmacion", "Wallapop no ha confirmado la publicación")
                return ""
            self.page.wait(300)

    def get_published_listing_url(self) -> str | None:
        pattern = self.site.item_url_regex
        if not pattern:
            return None
        match = re.search(pattern, self.page.current_url())
        if match:
            return match.group(0)
        try:
            links = self.page.links_matching(pattern)
        except Exception:
            links = []
        return links[0] if links else None

    # ------------------------------------------------------------------
    def run(self) -> PublishReport:
        self.open_create_listing()
        self.choose_listing_type()
        # Orden real de Wallapop: Resumen → Continuar → Fotos → Continuar →
        # categoría y detalles. El «Continuar» de Fotos está desactivado hasta
        # que hay una foto cargada.
        self.fill_title()
        self.upload_photos()
        self.continue_if_present("Continuar tras las fotos")
        self.select_category()
        self.wait_until_details_ready()
        self.fill_attributes()
        self.fill_final_title()
        self.fill_description()
        self.fill_price()
        # Envío y ubicación no deben impedir publicar: si Wallapop cambia algo
        # ahí, se publica igual y el resultado dice qué no se pudo hacer.
        self._not_blocking("Envío", self.set_shipping)
        self._not_blocking("Ubicación", self.set_location)
        # Primero se corrigen título y descripción (si Wallapop los cambió) y
        # DESPUÉS la revisión de campos: lo último que se toca antes de publicar.
        self.refill_if_overwritten()
        self.review_and_complete()
        self.verify_form()
        self.submit_listing()
        signal = self.wait_for_publish_confirmation()
        url = self.get_published_listing_url() if signal else None
        item_id = url.rstrip("/").rsplit("/", 1)[-1] if url else None
        if signal:
            self.steps.append("Confirmación de Wallapop")
        return PublishReport(
            confirmed=bool(signal),
            url=url,
            item_id=item_id,
            steps=list(self.steps),
            skipped=list(self.skipped),
            signal=signal,
        )
