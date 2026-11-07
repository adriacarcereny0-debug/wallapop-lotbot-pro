"""Iconos de línea propios (24×24, trazo redondeado) para el menú lateral.

Se dibujan con QtSvg en el color que toque, así siempre encajan con el tema y
se ven nítidos a cualquier escala de pantalla.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

#: Trazos de cada icono (dentro de un viewBox 0 0 24 24).
SHAPES: dict[str, str] = {
    "asistente": (
        '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/>'
        '<path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>'
    ),
    "cuentas": '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c1.2-3.5 4-5 7-5s5.8 1.5 7 5"/>',
    "publicacion": '<path d="M12 15.5V4.5"/><path d="M7.5 9 12 4.5 16.5 9"/><path d="M5 19.5h14"/>',
    "anuncios": (
        '<path d="M9.5 6.5h10"/><path d="M9.5 12h10"/><path d="M9.5 17.5h10"/>'
        '<circle cx="5" cy="6.5" r=".6"/><circle cx="5" cy="12" r=".6"/><circle cx="5" cy="17.5" r=".6"/>'
    ),
    "estadisticas": '<path d="M5.5 19.5v-7"/><path d="M12 19.5v-14"/><path d="M18.5 19.5v-10"/>',
    "imagenes": (
        '<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.6"/>'
        '<path d="M20.5 15.5l-4.5-4.5-8.5 8"/>'
    ),
    "principal": (
        '<path d="M12 4l2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.4l-4.8 2.5.9-5.4-3.9-3.8 5.4-.8z"/>'
    ),
    "configuracion": (
        '<path d="M4 7.5h9"/><path d="M18 7.5h2"/><circle cx="15.5" cy="7.5" r="2.3"/>'
        '<path d="M4 16.5h3"/><path d="M12 16.5h8"/><circle cx="9.5" cy="16.5" r="2.3"/>'
    ),
    "panel": (
        '<rect x="4" y="4" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.6"/>'
        '<rect x="4" y="13.5" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.6"/>'
    ),
    "productos": (
        '<path d="M4 8 12 4l8 4v8.5L12 20.5 4 16.5z"/><path d="M4 8l8 4 8-4"/><path d="M12 12v8.5"/>'
    ),
    "historial": '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
    "logs": '<path d="M12 4 20.5 19H3.5z"/><path d="M12 10v4"/><path d="M12 16.6v.1"/>',
}


def _svg(name: str, color: str) -> bytes:
    body = SHAPES[name]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</svg>"
    ).encode()


def pixmap(name: str, color: str, size: int = 18, ratio: float = 2.0) -> QPixmap:
    """El icono pintado en `color` (nítido en pantallas de alta densidad)."""
    px = int(size * ratio)
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(_svg(name, color))).render(painter)
    painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(ratio)
    return result


def nav_icon(name: str, normal: str, active: str, size: int = 18) -> QIcon:
    """Icono para un botón del menú: gris normal, color de acento si está activo."""
    icon = QIcon()
    icon.addPixmap(pixmap(name, normal, size), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(pixmap(name, active, size), QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(pixmap(name, active, size), QIcon.Mode.Active, QIcon.State.On)
    return icon


def icon_size(size: int = 18) -> QSize:
    return QSize(size, size)
