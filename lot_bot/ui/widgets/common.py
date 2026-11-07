"""Widgets comunes: tarjetas, cabeceras, tablas y panel de confirmacion."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lot_bot.ui import theme


class Card(QFrame):
    """Contenedor con borde redondeado."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 12) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(spacing)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget


class SectionTitle(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("SectionTitle")


class PageHeader(QWidget):
    """Titulo, subtitulo y zona de acciones de una pantalla."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageTitle")
        texts.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("PageSubtitle")
        self.subtitle_label.setVisible(bool(subtitle))
        texts.addWidget(self.subtitle_label)
        layout.addLayout(texts)
        layout.addStretch(1)

        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))

    def add_action(self, button: QPushButton) -> QPushButton:
        self.actions.addWidget(button)
        return button


class StatCard(QFrame):
    """Tarjeta con una cifra destacada."""

    def __init__(
        self, label: str, value: str = "—", hint: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setMinimumWidth(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(3)

        self.label_widget = QLabel(label.upper())
        self.label_widget.setObjectName("StatLabel")
        layout.addWidget(self.label_widget)

        self.value_widget = QLabel(value)
        self.value_widget.setObjectName("StatValue")
        layout.addWidget(self.value_widget)

        self.hint_widget = QLabel(hint)
        self.hint_widget.setObjectName("StatHint")
        self.hint_widget.setVisible(bool(hint))
        self.hint_widget.setWordWrap(True)
        layout.addWidget(self.hint_widget)

    def update_value(self, value: Any, hint: str = "") -> None:
        self.value_widget.setText(str(value))
        if hint:
            self.hint_widget.setText(hint)
            self.hint_widget.setVisible(True)


class Badge(QLabel):
    """Etiqueta de estado con color."""

    def __init__(self, text: str, status: str = "idle", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.apply_status(status)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def apply_status(self, status: str) -> None:
        color = theme.STATUS_COLORS.get(status, theme.TEXT_MUTED)
        self.setStyleSheet(
            f"background: {color}22; color: {color}; border: 1px solid {color}55;"
            f"border-radius: 9px; padding: 2px 10px; font-size: 11px; font-weight: 600;"
        )


class EmptyState(QWidget):
    """Mensaje cuando no hay datos que mostrar."""

    def __init__(self, title: str, hint: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        title_label.setFont(font)
        layout.addWidget(title_label)

        self.hint_label = QLabel(hint)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(self.hint_label)

    def set_hint(self, text: str) -> None:
        self.hint_label.setText(text)


class Toolbar(QWidget):
    """Fila de filtros y acciones."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(8)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.layout_.addWidget(widget, stretch)
        return widget

    def add_stretch(self) -> None:
        self.layout_.addStretch(1)


class ConfirmationPanel(QFrame):
    """Panel [Cancelar] [Confirmar] para las acciones que modifican datos.

    Es el ultimo filtro antes de que algo se publique, modifique o elimine.
    """

    confirmed = Signal(str)
    cancelled = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._token = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.title = QLabel()
        self.title.setObjectName("SectionTitle")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)

        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(self.detail)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.setObjectName("Ghost")
        self.cancel_button.clicked.connect(self._on_cancel)
        buttons.addWidget(self.cancel_button)

        self.confirm_button = QPushButton("Confirmar")
        self.confirm_button.setObjectName("Primary")
        self.confirm_button.clicked.connect(self._on_confirm)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)

        self.setVisible(False)

    def show_request(self, token: str, title: str, lines: list[str], destructive: bool) -> None:
        self._token = token
        self.title.setText(("⚠ " if destructive else "") + title)
        body = "\n".join(f"•  {line}" for line in lines)
        if destructive:
            body += "\n\nESTA ACCIÓN NO SE PUEDE DESHACER."
        self.detail.setText(body)
        self.confirm_button.setObjectName("Danger" if destructive else "Primary")
        self.confirm_button.setText("Eliminar" if destructive else "Confirmar")
        self.confirm_button.style().unpolish(self.confirm_button)
        self.confirm_button.style().polish(self.confirm_button)
        self.setStyleSheet(
            f"QFrame#Card {{ border: 1px solid {theme.DANGER if destructive else theme.ACCENT}; }}"
        )
        self.setVisible(True)

    def clear(self) -> None:
        self._token = ""
        self.setVisible(False)

    def _on_confirm(self) -> None:
        if self._token:
            token, self._token = self._token, ""
            self.setVisible(False)
            self.confirmed.emit(token)

    def _on_cancel(self) -> None:
        if self._token:
            token, self._token = self._token, ""
            self.setVisible(False)
            self.cancelled.emit(token)


# ---------------------------------------------------------------------------
# Utilidades de tablas
# ---------------------------------------------------------------------------
def build_table(headers: list[str], selectable_rows: bool = True) -> QTableWidget:
    """Crea una tabla con el aspecto estandar de la aplicacion."""
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(
        QAbstractItemView.SelectionBehavior.SelectRows
        if selectable_rows
        else QAbstractItemView.SelectionBehavior.SelectItems
    )
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setSortingEnabled(True)
    table.setWordWrap(False)
    header = table.horizontalHeader()
    header.setStretchLastSection(True)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    if headers:
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    return table


def fill_table(
    table: QTableWidget,
    rows: list[list[Any]],
    row_data: list[Any] | None = None,
    colorizer: Callable[[int, int, Any], str | None] | None = None,
) -> None:
    """Rellena la tabla. `row_data` guarda el objeto asociado a cada fila."""
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            item = QTableWidgetItem("" if value is None else str(value))
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            if colorizer is not None:
                color = colorizer(row_index, column_index, value)
                if color:
                    from PySide6.QtGui import QColor

                    item.setForeground(QColor(color))
            if column_index == 0 and row_data is not None and row_index < len(row_data):
                item.setData(Qt.ItemDataRole.UserRole, row_data[row_index])
            table.setItem(row_index, column_index, item)
    table.setSortingEnabled(True)
    table.resizeColumnsToContents()


def selected_row_data(table: QTableWidget) -> list[Any]:
    """Objetos asociados a las filas seleccionadas."""
    data: list[Any] = []
    for index in table.selectionModel().selectedRows() if table.selectionModel() else []:
        item = table.item(index.row(), 0)
        if item is not None:
            value = item.data(Qt.ItemDataRole.UserRole)
            if value is not None:
                data.append(value)
    return data


def info_box(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Information)
    box.exec()


def show_error(parent: QWidget, text: str, detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setWindowTitle("Ha ocurrido un error")
    box.setText(text)
    if detail:
        box.setDetailedText(detail)
    box.setIcon(QMessageBox.Icon.Warning)
    box.exec()


def ask_confirmation(parent: QWidget, title: str, text: str, destructive: bool = False) -> bool:
    """Dialogo de confirmacion para acciones lanzadas desde botones."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Question)
    confirm = box.addButton(
        "Eliminar" if destructive else "Confirmar", QMessageBox.ButtonRole.AcceptRole
    )
    box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is confirm


#: Textos en español para los botones estándar de Qt, que de lo contrario
#: aparecen en el idioma del sistema operativo.
_BUTTON_TEXTS = {
    "Ok": "Aceptar",
    "Cancel": "Cancelar",
    "Close": "Cerrar",
    "Save": "Guardar",
    "Yes": "Sí",
    "No": "No",
    "Apply": "Aplicar",
    "Reset": "Restablecer",
    "Discard": "Descartar",
}


def spanish_buttons(box):
    """Traduce al español los botones estándar de un QDialogButtonBox."""
    from PySide6.QtWidgets import QDialogButtonBox

    for standard in QDialogButtonBox.StandardButton:
        button = box.button(standard)
        if button is None:
            continue
        translated = _BUTTON_TEXTS.get(standard.name)
        if translated:
            button.setText(translated)
    return box


#: Estados de anuncio en español, para las tablas.
LISTING_STATUS_LABELS = {
    "draft": "Borrador",
    "pending": "Pendiente",
    "active": "Activo",
    "inactive": "Inactivo",
    "sold": "Vendido",
    "removed": "Eliminado",
    "error": "Error",
}


def listing_status_label(value: str) -> str:
    return LISTING_STATUS_LABELS.get(value, value)
