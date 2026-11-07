"""Ventana principal de LOT Bot: barra lateral y pantallas."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from lot_bot import APP_NAME
from lot_bot.ui import icons, theme
from lot_bot.ui.views import (
    AccountsView,
    AssistantView,
    DashboardView,
    HistoryView,
    ImageStudioView,
    ListingsView,
    LogsView,
    MasterAdView,
    ProductsView,
    PublishQueueView,
    SettingsView,
    StatisticsView,
)
from lot_bot.ui.widgets.workers import TaskRunner

if TYPE_CHECKING:  # pragma: no cover
    from lot_bot.bootstrap import Application

logger = logging.getLogger(__name__)

#: (etiqueta, icono textual, clase de la vista)
NAVIGATION = [
    # --- Lo principal ---
    ("Asistente IA", "asistente", AssistantView),
    ("Cuentas de Wallapop", "cuentas", AccountsView),
    ("Publicación automática", "publicacion", PublishQueueView),
    ("Anuncios (eliminar)", "anuncios", ListingsView),
    ("Estadísticas", "estadisticas", StatisticsView),
    ("Imágenes / IA", "imagenes", ImageStudioView),
    ("Anuncio principal", "principal", MasterAdView),
    ("Configuración", "configuracion", SettingsView),
    # --- Más ---
    ("Panel", "panel", DashboardView),
    ("Productos", "productos", ProductsView),
    ("Historial", "historial", HistoryView),
    ("Logs y errores", "logs", LogsView),
]
#: Primera pantalla del grupo «Más» del menú.
MORE_SECTION_START = "Panel"


class MainWindow(QMainWindow):
    """Ventana principal."""

    def __init__(self, app: Application) -> None:
        super().__init__()
        self.app = app
        self.runner = TaskRunner(6)
        #: Hilos reservados para el asistente: nunca esperan a otras pantallas.
        self.assistant_runner = TaskRunner(2)

        self.setWindowTitle(f"{APP_NAME} {app.version}")
        self.resize(1360, 860)
        self.setMinimumSize(1080, 700)
        self.setWindowIcon(_build_icon())

        central = QWidget()
        central.setObjectName("Content")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        # Franja fija: en DEMO debe ser imposible no darse cuenta.
        self.demo_banner = QLabel()
        self.demo_banner.setWordWrap(True)
        self.demo_banner.setObjectName("DemoBanner")
        right.addWidget(self.demo_banner)
        self.stack = QStackedWidget()
        self.stack.setObjectName("Content")
        right.addWidget(self.stack, 1)
        layout.addLayout(right, 1)
        self.setCentralWidget(central)

        self.views: list = []
        for label, _icon, view_class in NAVIGATION:
            try:
                runner = self.assistant_runner if view_class is AssistantView else self.runner
                view = view_class(self.app, runner)
            except Exception:  # una vista rota no debe impedir abrir el programa
                logger.exception("No se ha podido crear la pantalla '%s'", label)
                view = _error_placeholder(label)
            self.views.append(view)
            self.stack.addWidget(view)

        status = QStatusBar()
        self.status_label = QLabel()
        status.addWidget(self.status_label)
        self.setStatusBar(status)
        self._update_status()

        # El asistente es el centro de la aplicación: se abre directamente ahí.
        self._go_to(0)  # el asistente es el centro de la aplicación

    # ------------------------------------------------------------------
    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(252)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(0)

        # Logo + nombre
        header = QHBoxLayout()
        header.setContentsMargins(22, 22, 18, 10)
        header.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(_logo_pixmap(36))
        logo.setFixedSize(36, 36)
        header.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(1)
        brand = QLabel(APP_NAME)
        brand.setObjectName("Brand")
        names.addWidget(brand)
        self.brand_sub = QLabel()
        self.brand_sub.setObjectName("BrandSub")
        names.addWidget(self.brand_sub)
        header.addLayout(names, 1)
        layout.addLayout(header)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        for index, (label, icon, _view) in enumerate(NAVIGATION):
            if index == 0 or label == MORE_SECTION_START:
                section = QLabel("PRINCIPAL" if index == 0 else "MÁS")
                section.setObjectName("NavSection")
                layout.addWidget(section)
            button = QPushButton(f"  {label}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if icon in icons.SHAPES:
                button.setIcon(icons.nav_icon(icon, theme.TEXT_MUTED, theme.ACCENT))
                button.setIconSize(icons.icon_size(18))
            button.clicked.connect(lambda _=False, i=index: self._go_to(i))
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            layout.addWidget(button)

        layout.addStretch(1)

        self.mode_label = QLabel()
        self.mode_label.setWordWrap(True)
        self.mode_label.setObjectName("SidebarFooter")
        layout.addWidget(self.mode_label)
        return sidebar

    # ------------------------------------------------------------------
    def go_to_view(self, label: str) -> None:
        for index, (name, _icon, _view) in enumerate(NAVIGATION):
            if name == label:
                self._go_to(index)
                return

    def _go_to(self, index: int) -> None:
        """Muestra una pantalla y recarga sus datos."""
        self.stack.setCurrentIndex(index)
        if index < len(self.nav_buttons):
            self.nav_buttons[index].setChecked(True)
        # Se refresca siempre de forma explicita: `currentChanged` no se emite
        # cuando se vuelve a seleccionar la pantalla que ya estaba activa.
        self._on_page_changed(index)

    def _on_page_changed(self, index: int) -> None:
        view = self.stack.widget(index)
        refresh = getattr(view, "refresh", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                logger.exception("Error al refrescar la pantalla %s", type(view).__name__)
        self._update_status()

    def _update_status(self) -> None:
        """Refleja el modo real y el mecanismo de acceso activo.

        Nunca presenta una cuenta de demostración como conectada a Wallapop.
        """
        backend = self.app.backend
        accounts = self.app.accounts.list_accounts()

        if backend.demo:
            mode = "MODO DEMO — datos simulados"
            connected = sum(1 for a in accounts if a.is_connected)
            detail = "MODO DEMO\nNada de lo que hagas afecta a Wallapop."
        else:
            mode = f"{backend.label} — {self.app.auth_method.describe()}"
            connected = sum(1 for a in accounts if a.is_connected and not a.is_demo)
            detail = f"{backend.label}\nDatos en:\n{self.app.paths.root}"

        self.status_label.setText(
            f"  {mode}   ·   {connected}/{len(accounts)} cuentas conectadas   ·   "
            f"Asistente: {self.app.agent.provider.describe()}"
        )
        self.brand_sub.setText(backend.label)
        self.mode_label.setText(detail)
        self.demo_banner.setVisible(backend.demo)
        self.demo_banner.setText(
            "MODO DEMO — Datos simulados. Nada de lo que hagas se envía a Wallapop: "
            "las publicaciones, precios y mensajes son de prueba."
        )

    def refresh_current(self) -> None:
        self._on_page_changed(self.stack.currentIndex())

    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - firma de Qt
        logger.info("Cerrando la ventana principal.")
        self.runner.wait(2000)
        self.app.shutdown()
        super().closeEvent(event)


def _error_placeholder(label: str) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    message = QLabel(
        f"No se ha podido cargar la pantalla «{label}».\n"
        f"Consulta la pantalla de Logs para ver el detalle técnico."
    )
    message.setAlignment(Qt.AlignmentFlag.AlignCenter)
    message.setWordWrap(True)
    layout.addWidget(message)
    return widget


def _logo_pixmap(size: int) -> QPixmap:
    """Logo (la linterna) para la barra lateral, nítido en pantallas HiDPI."""
    from lot_bot.config.paths import get_paths

    resources = get_paths().resources
    svg = resources / "logo" / ("lotbot-logo-pequeno.svg" if size <= 32 else "lotbot-logo.svg")
    ratio = 2.0
    if svg.is_file():
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer

        image = QImage(int(size * ratio), int(size * ratio), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(str(svg)).render(painter)
        painter.end()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(ratio)
        return pixmap
    png = resources / "lot_bot.png"
    pixmap = QPixmap(str(png)) if png.is_file() else QPixmap()
    return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation) if not pixmap.isNull() else pixmap


def _build_icon() -> QIcon:
    """Icono de la aplicacion.

    Usa el fichero empaquetado si esta disponible y, si no, lo dibuja en
    memoria para que el programa funcione igualmente.
    """
    from PySide6.QtGui import QColor, QPainter

    from lot_bot.config.paths import get_paths

    for candidate in (
        get_paths().resources / "lot_bot.ico",
        get_paths().resources / "lot_bot.png",
    ):
        if candidate.is_file():
            icon = QIcon(str(candidate))
            if not icon.isNull():
                return icon

    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(theme.BG_ELEVATED))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(theme.ACCENT))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(8, 8, 48, 48, 12, 12)
    painter.setPen(QColor(theme.ACCENT_TEXT))
    font = painter.font()
    font.setBold(True)
    font.setPointSize(22)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "L")
    painter.end()
    return QIcon(pixmap)
