"""Carga de la descripción de la web de Wallapop (`wallapop_browser.yaml`).

Todos los selectores y direcciones viven en ese fichero: el código no
contiene ninguno. Se puede sobrescribir con una copia local en la carpeta de
configuración del usuario (nunca en el repositorio).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lot_bot.config.paths import get_paths

logger = logging.getLogger(__name__)

LOCAL_FILENAME = "wallapop_browser.local.yaml"


def default_config_path() -> Path:
    return get_paths().resources / "wallapop_browser.yaml"


def local_config_path() -> Path:
    return get_paths().config / LOCAL_FILENAME


@dataclass(slots=True)
class FormField:
    """Un campo del formulario de Wallapop (selectores en el YAML)."""

    targets: list[str] = field(default_factory=list)
    readback: list[str] = field(default_factory=list)
    optional: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FormField:
        data = data or {}
        return cls(
            targets=[str(t) for t in (data.get("objetivos") or [])],
            readback=[str(t) for t in (data.get("lectura") or [])],
            optional=bool(data.get("opcional", False)),
        )


#: Características del anuncio, en el orden en que se rellenan.
ATTRIBUTE_KEYS = ("estado", "uso", "color", "material", "marca")


@dataclass(slots=True)
class ListingFormConfig:
    open_url: str = "subir"
    listing_type: FormField = field(default_factory=FormField)
    title: FormField = field(default_factory=FormField)
    category: FormField = field(default_factory=FormField)
    subcategory: FormField = field(default_factory=FormField)
    attributes: dict[str, FormField] = field(default_factory=dict)
    description: FormField = field(default_factory=FormField)
    price: FormField = field(default_factory=FormField)
    photos: FormField = field(default_factory=FormField)
    photo_buttons: list[str] = field(default_factory=list)
    photo_thumbnails: list[str] = field(default_factory=list)
    photo_rejected: list[str] = field(default_factory=list)
    photo_wait_ms: int = 45000
    submit: FormField = field(default_factory=FormField)
    next_button: FormField = field(default_factory=FormField)
    category_panel: list[str] = field(default_factory=list)
    category_selected: list[str] = field(default_factory=list)
    category_paths: dict[str, str] = field(default_factory=dict)
    #: Tras elegir la categoría Wallapop «prepara» el resto del formulario.
    prepare_wait_ms: int = 60000
    list_confirm: list[str] = field(default_factory=list)
    #: Textos del botón que vacía una lista de varias opciones («Borrar»).
    list_clear: list[str] = field(default_factory=list)
    #: Textos del botón que guarda lo marcado en esa lista («Seleccionar»).
    list_confirm_texts: list[str] = field(default_factory=list)
    neutral_click: list[str] = field(default_factory=list)
    location: FormField = field(default_factory=FormField)
    location_input: list[str] = field(default_factory=list)
    location_confirm: list[str] = field(default_factory=list)
    final_title: FormField = field(default_factory=FormField)
    shipping: FormField = field(default_factory=FormField)
    shipping_enabled: bool = False
    #: {característica: {valor de la plantilla: [alternativas en Wallapop]}}
    equivalents: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def defined(self) -> bool:
        return bool(self.title.targets and self.submit.targets)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ListingFormConfig:
        data = data or {}
        photos = data.get("fotos") or {}
        attributes = data.get("caracteristicas") or {}
        return cls(
            open_url=str((data.get("abrir") or {}).get("url", "subir")),
            listing_type=FormField.from_dict(data.get("tipo_anuncio")),
            title=FormField.from_dict(data.get("titulo")),
            category=FormField.from_dict(data.get("categoria")),
            subcategory=FormField.from_dict(data.get("subcategoria")),
            attributes={
                str(k): FormField.from_dict(v) for k, v in attributes.items() if isinstance(v, dict)
            },
            description=FormField.from_dict(data.get("descripcion")),
            price=FormField.from_dict(data.get("precio")),
            photos=FormField.from_dict(photos),
            photo_buttons=[str(x) for x in (photos.get("boton") or [])],
            photo_thumbnails=[str(x) for x in (photos.get("miniaturas") or [])],
            photo_rejected=[str(x) for x in (photos.get("rechazada") or [])],
            photo_wait_ms=int(photos.get("espera_ms", 45000)),
            submit=FormField.from_dict(data.get("publicar")),
            next_button=FormField.from_dict(data.get("continuar")),
            category_panel=[str(x) for x in ((data.get("categoria") or {}).get("panel") or [])],
            location=FormField.from_dict(data.get("ubicacion")),
            location_input=[str(x) for x in ((data.get("ubicacion") or {}).get("escribir") or [])],
            location_confirm=[str(x) for x in ((data.get("ubicacion") or {}).get("confirmar") or [])],
            list_confirm=[str(x) for x in (data.get("listas_aceptar") or [])],
            list_clear=[str(x) for x in (data.get("listas_borrar") or [])],
            list_confirm_texts=[str(x) for x in (data.get("listas_guardar") or [])],
            neutral_click=[str(x) for x in (data.get("clic_fuera") or [])],
            prepare_wait_ms=int((data.get("categoria") or {}).get("espera_preparacion_ms", 60000)),
            category_paths={
                str(k): str(v) for k, v in ((data.get("categoria") or {}).get("rutas") or {}).items()
            },
            category_selected=[
                str(x) for x in ((data.get("categoria") or {}).get("seleccionada") or [])
            ],
            final_title=FormField.from_dict(data.get("titulo_revision")),
            shipping=FormField.from_dict(data.get("envio")),
            shipping_enabled=bool((data.get("envio") or {}).get("activado", False)),
            equivalents={
                str(k): {str(a): [str(x) for x in (b or [])] for a, b in (v or {}).items()}
                for k, v in (data.get("equivalencias") or {}).items()
            },
            errors=[str(x) for x in (data.get("errores") or [])],
        )


@dataclass(slots=True)
class DeleteConfig:
    """Pasos para eliminar un anuncio en su página (sección `eliminar` del YAML)."""

    #: Menú que hay que abrir antes (si el botón está dentro de «…»).
    menu: list[str] = field(default_factory=list)
    button: list[str] = field(default_factory=list)
    #: Motivo que pide Wallapop (se pulsa el primero que aparezca).
    reasons: list[str] = field(default_factory=list)
    confirm: list[str] = field(default_factory=list)
    #: Textos que indican que el anuncio ya no existe o se ha eliminado.
    gone: list[str] = field(default_factory=list)
    wait_ms: int = 15000

    @property
    def defined(self) -> bool:
        return bool(self.button)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> DeleteConfig:
        data = data or {}

        def strs(key: str) -> list[str]:
            return [str(x) for x in (data.get(key) or [])]

        return cls(
            menu=strs("menu"),
            button=strs("boton"),
            reasons=strs("motivos"),
            confirm=strs("confirmar"),
            gone=strs("eliminado"),
            wait_ms=int(data.get("espera_ms", 15000)),
        )


@dataclass(slots=True)
class EditConfig:
    """Cambiar precio o título en la página del anuncio (sección `editar`)."""

    menu: list[str] = field(default_factory=list)
    button: list[str] = field(default_factory=list)
    save: list[str] = field(default_factory=list)
    wait_ms: int = 20000

    @property
    def defined(self) -> bool:
        return bool(self.button and self.save)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EditConfig:
        data = data or {}
        return cls(
            menu=[str(x) for x in (data.get("menu") or [])],
            button=[str(x) for x in (data.get("boton") or [])],
            save=[str(x) for x in (data.get("guardar") or [])],
            wait_ms=int(data.get("espera_ms", 20000)),
        )


@dataclass(slots=True)
class BrowserSiteConfig:
    verified: bool
    visible: bool
    channels: list[str]
    locale: str
    timeout_ms: int
    urls: dict[str, str]
    logged_in: list[str]
    logged_out: list[str]
    verification: list[str]
    user_name: list[str]
    price_format: str
    form: ListingFormConfig
    success_url_regex: str
    success_texts: list[str]
    success_timeout_ms: int
    check_url: str = "subir"
    check_url_contains: str = "/app/"
    check_wait_ms: int = 4000
    check_private: list[str] = field(default_factory=list)
    keep_open_seconds: int = 900
    stats_patterns: dict[str, str] = field(default_factory=dict)
    delete: DeleteConfig = field(default_factory=DeleteConfig)
    edit: EditConfig = field(default_factory=EditConfig)
    source: Path | None = None

    def url(self, name: str) -> str:
        value = self.urls.get(name)
        if not value:
            raise KeyError(f"La dirección «{name}» no está en {self.source}.")
        return value

    @property
    def item_url_regex(self) -> str:
        return self.urls.get("anuncio_regex", "")

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: Path | None = None) -> BrowserSiteConfig:
        nav = data.get("navegador") or {}
        session = data.get("sesion") or {}
        publish = data.get("publicar") or {}
        success = publish.get("exito") or {}
        check = session.get("comprobacion") or {}
        return cls(
            verified=bool(data.get("verificado", False)),
            visible=bool(nav.get("visible", True)),
            channels=[str(c) for c in (nav.get("canales") or ["chromium"])],
            locale=str(nav.get("idioma", "es-ES")),
            timeout_ms=int(nav.get("tiempo_espera_ms", 20000)),
            urls={str(k): str(v) for k, v in (data.get("urls") or {}).items()},
            logged_in=[str(x) for x in (session.get("iniciada") or [])],
            logged_out=[str(x) for x in (session.get("sin_sesion") or [])],
            verification=[str(x) for x in (session.get("verificacion") or [])],
            user_name=[str(x) for x in (session.get("nombre_usuario") or [])],
            price_format=str(publish.get("formato_precio", "coma")),
            form=ListingFormConfig.from_dict(publish.get("formulario")),
            success_url_regex=str(success.get("url_regex", "")),
            success_texts=[str(x) for x in (success.get("textos") or [])],
            success_timeout_ms=int(success.get("tiempo_espera_ms", 60000)),
            check_url=str(check.get("url", "subir")),
            check_url_contains=str(check.get("url_debe_contener", "/app/")),
            check_wait_ms=int(check.get("espera_ms", 4000)),
            check_private=[str(x) for x in (check.get("privada") or [])],
            keep_open_seconds=int(session.get("mantener_abierto_s", 900)),
            stats_patterns={
                str(k): str(v) for k, v in (data.get("estadisticas") or {}).items() if v
            },
            delete=DeleteConfig.from_dict(data.get("eliminar")),
            edit=EditConfig.from_dict(data.get("editar")),
            source=source,
        )


def load_site_config(path: Path | None = None) -> BrowserSiteConfig:
    """Usa la copia local si existe; si no, la incluida en el programa."""
    candidates = [path] if path else [local_config_path(), default_config_path()]
    for candidate in candidates:
        if candidate and candidate.is_file():
            with open(candidate, encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            return BrowserSiteConfig.from_dict(data, source=candidate)
    raise FileNotFoundError("No se encuentra wallapop_browser.yaml.")
