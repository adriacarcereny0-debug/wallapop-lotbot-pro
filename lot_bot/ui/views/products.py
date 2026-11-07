"""Pantalla de productos: catalogo, calidad, fotografias y publicacion."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from lot_bot.catalog.service import ProductFilter
from lot_bot.database.models import ProductStatus
from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import (
    Card,
    SectionTitle,
    Toolbar,
    ask_confirmation,
    build_table,
    fill_table,
    info_box,
    selected_row_data,
    show_error,
    spanish_buttons,
)

STATUS_LABELS = {
    "draft": "Borrador",
    "ready": "Listo para publicar",
    "published": "Publicado",
    "paused": "Pausado",
    "archived": "Archivado",
}


class ProductDialog(QDialog):
    """Alta y edicion de un producto."""

    def __init__(self, parent=None, product=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Producto")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.name = QLineEdit(product.name if product else "")
        self.sku = QLineEdit(product.sku if product else "")
        self.sku.setPlaceholderText("Se genera automáticamente si lo dejas vacío")
        self.product_type = QLineEdit(product.product_type if product else "Canapé abatible")
        self.category = QLineEdit(product.category if product else "")
        self.category.setPlaceholderText("Obligatoria para publicar")
        self.subcategory = QLineEdit(product.subcategory or "" if product else "")
        self.size = QLineEdit(product.size if product else "")
        self.size.setPlaceholderText("135x190")
        self.color = QLineEdit(product.color if product else "")
        self.material = QLineEdit(product.material if product else "")
        self.condition = QComboBox()
        self.condition.addItems(["Nuevo", "Como nuevo", "En buen estado", "Con uso"])
        if product and product.condition:
            self.condition.setCurrentText(product.condition)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 100000)
        self.price.setDecimals(2)
        self.price.setSuffix(" €")
        self.price.setValue(product.price or 0 if product else 0)
        self.stock = QSpinBox()
        self.stock.setRange(0, 10000)
        self.stock.setValue(product.stock if product else 0)
        self.description = QPlainTextEdit(product.description or "" if product else "")
        self.description.setFixedHeight(90)
        self.status = QComboBox()
        for value, label in STATUS_LABELS.items():
            self.status.addItem(label, value)
        if product:
            index = self.status.findData(product.status)
            if index >= 0:
                self.status.setCurrentIndex(index)
        self.features = QPlainTextEdit()
        self.features.setFixedHeight(70)
        self.features.setPlaceholderText("Una por línea, «nombre: valor». Ej.: almacenaje: sí")
        if product:
            extra = {
                k: v
                for k, v in product.features.items()
                if k not in {"medida", "color", "material", "estado"}
            }
            self.features.setPlainText("\n".join(f"{k}: {v}" for k, v in extra.items()))
        self.tags = QLineEdit(", ".join(product.tags) if product else "")
        self.tags.setPlaceholderText("Separadas por comas")

        form.addRow("Nombre*", self.name)
        form.addRow("SKU", self.sku)
        form.addRow("Tipo", self.product_type)
        form.addRow("Categoría", self.category)
        form.addRow("Subcategoría", self.subcategory)
        form.addRow("Medida", self.size)
        form.addRow("Color", self.color)
        form.addRow("Material", self.material)
        form.addRow("Estado", self.condition)
        form.addRow("Precio", self.price)
        form.addRow("Stock", self.stock)
        form.addRow("Descripción", self.description)
        form.addRow("Características", self.features)
        form.addRow("Etiquetas", self.tags)
        form.addRow("Situación", self.status)
        layout.addLayout(form)

        buttons = spanish_buttons(QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        ))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "sku": self.sku.text().strip(),
            "product_type": self.product_type.text().strip(),
            "category": self.category.text().strip(),
            "size": self.size.text().strip(),
            "color": self.color.text().strip(),
            "material": self.material.text().strip(),
            "condition": self.condition.currentText(),
            "price": self.price.value() or None,
            "stock": self.stock.value(),
            "description": self.description.toPlainText().strip(),
            "subcategory": self.subcategory.text().strip(),
            "status": self.status.currentData(),
            "features": self._features(),
            "tags": [t.strip() for t in self.tags.text().split(",") if t.strip()],
        }

    def _features(self) -> dict:
        features: dict[str, str] = {}
        for line in self.features.toPlainText().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                if key.strip() and value.strip():
                    features[key.strip()] = value.strip()
        return features


class ProductsView(BaseView):
    title = "Productos"
    subtitle = "Catálogo interno: la fuente de verdad de tus anuncios"

    def build(self) -> None:
        self.add_header_button("Nuevo producto", self._create, primary=True)
        self.add_header_button("Actualizar", self.refresh)

        toolbar = Toolbar()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por nombre, SKU, medida o color…")
        self.search.returnPressed.connect(self.refresh)
        toolbar.add(self.search, 1)

        self.status_filter = QComboBox()
        self.status_filter.addItem("Todos los estados", None)
        for value, label in STATUS_LABELS.items():
            self.status_filter.addItem(label, value)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        toolbar.add(self.status_filter)

        self.account_filter = QComboBox()
        self.account_filter.currentIndexChanged.connect(self.refresh)
        toolbar.add(self.account_filter)
        self.body.addWidget(toolbar)

        columns = QHBoxLayout()
        columns.setSpacing(14)

        table_card = Card()
        self.table = build_table(
            ["SKU", "Nombre", "Medida", "Color", "Precio", "Stock", "Fotos", "Calidad", "Estado", "Cuentas"]
        )
        self.table.itemSelectionChanged.connect(self._on_selection)
        table_card.add(self.table)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        for text, slot, name in (
            ("Editar", self._edit, ""),
            ("Fotografías", self._manage_images, ""),
            ("Asignar a cuentas", self._assign, ""),
            ("Vista previa", self._preview, ""),
            ("Publicar", self._publish, "Primary"),
        ):
            button = QPushButton(text)
            if name:
                button.setObjectName(name)
            button.clicked.connect(slot)
            button.setEnabled(False)
            actions.addWidget(button)
            setattr(self, f"_button_{text.lower().split()[0]}", button)
        actions.addStretch(1)
        self.delete_button = QPushButton("Eliminar")
        self.delete_button.setObjectName("Danger")
        self.delete_button.clicked.connect(self._delete)
        self.delete_button.setEnabled(False)
        actions.addWidget(self.delete_button)
        table_card.body.addLayout(actions)
        columns.addWidget(table_card, 3)

        detail_card = Card()
        detail_card.add(SectionTitle("Control de calidad"))
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Selecciona un producto para ver su análisis.")
        detail_card.add(self.detail)
        columns.addWidget(detail_card, 2)

        self.body.addLayout(columns, 1)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_account_filter()
        products = self.app.catalog.list_products(self._criteria())
        self._products = products
        fill_table(
            self.table,
            [
                [
                    p.sku,
                    p.name,
                    p.size or "—",
                    p.color or "—",
                    f"{p.price:.2f} €" if p.price else "—",
                    p.stock,
                    p.image_count,
                    f"{p.quality_score}/100",
                    STATUS_LABELS.get(p.status, p.status),
                    len(p.accounts),
                ]
                for p in products
            ],
            row_data=[p.id for p in products],
            colorizer=lambda row, col, value: (
                theme.DANGER
                if col == 7 and row < len(products) and products[row].quality_score < 60
                else (theme.STATUS_COLORS.get(products[row].status) if col == 8 and row < len(products) else None)
            ),
        )
        self.header.set_subtitle(f"{len(products)} producto(s) en el catálogo")
        self._on_selection()

    def _refresh_account_filter(self) -> None:
        current = self.account_filter.currentData()
        self.account_filter.blockSignals(True)
        self.account_filter.clear()
        self.account_filter.addItem("Todas las cuentas", None)
        for account in self.app.accounts.list_accounts():
            self.account_filter.addItem(account.alias, account.internal_ref)
        index = self.account_filter.findData(current)
        if index >= 0:
            self.account_filter.setCurrentIndex(index)
        self.account_filter.blockSignals(False)

    def _criteria(self) -> ProductFilter:
        status = self.status_filter.currentData()
        return ProductFilter(
            text=self.search.text().strip() or None,
            status=ProductStatus(status) if status else None,
            account_ref=self.account_filter.currentData(),
            limit=500,
        )

    # ------------------------------------------------------------------
    def _selected_id(self) -> int | None:
        ids = selected_row_data(self.table)
        return ids[0] if ids else None

    def _on_selection(self) -> None:
        product_id = self._selected_id()
        enabled = product_id is not None
        for name in ("editar", "fotografías", "asignar", "vista", "publicar"):
            button = getattr(self, f"_button_{name}", None)
            if button is not None:
                button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        if product_id is None:
            self.detail.clear()
            return
        try:
            report = self.app.catalog.validate_product(product_id)
        except ValueError:
            self.detail.clear()
            return
        product = self.app.catalog.get_product(product_id)
        lines = [
            f"<h3>{product.sku} · {product.name}</h3>",
            f"<p><b>Calidad:</b> {report.score}/100 — "
            + (
                f"<span style='color:{theme.SUCCESS}'>se puede publicar</span>"
                if report.can_publish
                else f"<span style='color:{theme.DANGER}'>no se puede publicar</span>"
            )
            + "</p>",
        ]
        if report.errors:
            lines.append("<p><b>Errores que impiden publicar:</b></p><ul>")
            lines += [f"<li>{i.message} {i.suggestion}</li>" for i in report.errors]
            lines.append("</ul>")
        if report.warnings:
            lines.append("<p><b>Avisos:</b></p><ul>")
            lines += [f"<li>{i.message}</li>" for i in report.warnings]
            lines.append("</ul>")
        lines.append(
            f"<p><b>Cuentas asignadas:</b> {', '.join(product.accounts) or 'ninguna'}</p>"
        )
        self.detail.setHtml("".join(lines))

    # ------------------------------------------------------------------
    def _create(self) -> None:
        dialog = ProductDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            show_error(self, "El producto necesita un nombre.")
            return
        try:
            product = self.app.catalog.create_product(values)
        except ValueError as exc:
            show_error(self, str(exc))
            return
        self.app.audit.record_success("Alta de producto", target=product.sku)
        self.refresh()

    def _edit(self) -> None:
        product_id = self._selected_id()
        if product_id is None:
            return
        product = self.app.catalog.get_product(product_id)
        dialog = ProductDialog(self, product)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        values.pop("sku", None)
        try:
            self.app.catalog.update_product(product_id, values)
        except ValueError as exc:
            show_error(self, str(exc))
            return
        self.app.audit.record_success("Modificación de producto", target=product.sku)
        self.refresh()

    def _delete(self) -> None:
        product_id = self._selected_id()
        if product_id is None:
            return
        product = self.app.catalog.get_product(product_id)
        if not ask_confirmation(
            self,
            "Eliminar producto",
            f"Se eliminará «{product.sku} · {product.name}» del catálogo local.\n\n"
            f"Los anuncios ya publicados en Wallapop NO se tocan.",
            destructive=True,
        ):
            return
        self.app.catalog.delete_product(product_id)
        self.app.audit.record_success("Baja de producto", target=product.sku)
        self.refresh()

    # ------------------------------------------------------------------
    def _manage_images(self) -> None:
        product_id = self._selected_id()
        if product_id is None:
            return
        from lot_bot.ui.widgets.images_dialog import ImagesDialog, product_adapter

        ImagesDialog(product_adapter(self.app, product_id), self).exec()
        self.refresh()

    def _assign(self) -> None:
        product_id = self._selected_id()
        if product_id is None:
            return
        product = self.app.catalog.get_product(product_id)
        accounts = self.app.accounts.list_accounts()

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Cuentas de {product.sku}")
        dialog.setMinimumWidth(380)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Selecciona en qué cuentas debe publicarse este producto:"))
        listing = QListWidget()
        listing.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for account in accounts:
            listing.addItem(f"{account.alias}  ({account.internal_ref})")
        for index, account in enumerate(accounts):
            if account.internal_ref in product.accounts:
                listing.item(index).setSelected(True)
        layout.addWidget(listing)
        buttons = spanish_buttons(QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        ))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = [accounts[i.row()].internal_ref for i in listing.selectedIndexes()]
        self.app.catalog.assign_to_accounts(product_id, selected, replace=True)
        self.app.audit.record_success(
            "Asignación de producto a cuentas", target=product.sku, detail=", ".join(selected)
        )
        self.refresh()

    # ------------------------------------------------------------------
    def _preview(self) -> None:
        product_id = self._selected_id()
        if product_id is None:
            return
        try:
            previews = self.app.publishing.build_previews(product_id)
        except ValueError as exc:
            show_error(self, str(exc))
            return
        text = "\n\n".join(
            f"{'—' * 40}\n{p.summary()}\n\nTÍTULO:\n{p.title}\n\nDESCRIPCIÓN:\n{p.description}"
            for p in previews
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("Vista previa del anuncio")
        dialog.resize(680, 620)
        layout = QVBoxLayout(dialog)
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(text)
        layout.addWidget(viewer)
        buttons = spanish_buttons(QDialogButtonBox(QDialogButtonBox.StandardButton.Close))
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _publish(self) -> None:
        product_id = self._selected_id()
        if product_id is None:
            return
        try:
            previews = self.app.publishing.build_previews(product_id)
        except ValueError as exc:
            show_error(self, str(exc))
            return

        blocked = [p for p in previews if not p.can_publish]
        lines = [f"{p.account_ref}: {p.title} — {p.price} €" for p in previews]
        text = (
            f"Se publicarán {len(previews) - len(blocked)} anuncio(s):\n\n"
            + "\n".join(lines)
        )
        if blocked:
            text += "\n\nBLOQUEADOS por control de calidad:\n" + "\n".join(
                f"• {p.account_ref}: "
                + "; ".join(i.message for i in (p.quality.errors if p.quality else []))
                for p in blocked
            )
        if not ask_confirmation(self, "Publicar en Wallapop", text):
            return

        def work():
            return self.app.publishing.publish(product_id, confirmed=True, actor="usuario")

        def success(outcomes):
            done = sum(1 for o in outcomes if o.success)
            detail = "\n".join(f"• {o.account_ref}: {o.message}" for o in outcomes)
            info_box(self, "Resultado de la publicación", f"Publicados {done} de {len(outcomes)}.\n\n{detail}")
            self.refresh()

        self.run_task(work, on_success=success)
