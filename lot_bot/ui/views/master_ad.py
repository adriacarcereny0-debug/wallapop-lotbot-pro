"""Pantalla «Anuncio principal»: la plantilla de canapés del negocio.

Aquí se edita la plantilla maestra, se ve cómo queda, se publica y se
consultan sus publicaciones. Publicar nunca cambia la plantilla; guardar la
plantilla siempre pide confirmación y enseña qué cambia.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import (
    Card,
    SectionTitle,
    ask_confirmation,
    build_table,
    fill_table,
    info_box,
    listing_status_label,
    show_error,
    spanish_buttons,
)
from lot_bot.ui.widgets.images_dialog import ImagesDialog, master_adapter


def _split(text: str, separator: str = ",") -> list[str]:
    return [part.strip() for part in text.split(separator) if part.strip()]


class PublishDialog(QDialog):
    """Elegir cuentas y número de publicaciones."""

    def __init__(self, app, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Publicar el anuncio principal")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("¿En qué cuentas quieres publicarlo?"))
        self.checks: list[tuple[str, QCheckBox]] = []
        for account in app.accounts.list_accounts():
            usable = account.is_connected and (app.demo_mode or not account.is_demo)
            label = account.alias + ("" if usable else " — no conectada")
            box = QCheckBox(label)
            box.setChecked(usable)
            box.setEnabled(usable)
            layout.addWidget(box)
            self.checks.append((account.internal_ref, box))

        form = QFormLayout()
        self.copies = QSpinBox()
        self.copies.setRange(0, 100)
        self.copies.setSpecialValueText("Una en cada cuenta marcada")
        form.addRow("Número de publicaciones", self.copies)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 100000)
        self.price.setDecimals(2)
        self.price.setSuffix(" €")
        self.price.setSpecialValueText("El de la plantilla")
        form.addRow("Precio solo para estas", self.price)
        layout.addLayout(form)
        hint = QLabel(
            "Si indicas un número, las publicaciones se reparten por turnos entre las "
            "cuentas marcadas. Un precio aquí solo afecta a estas publicaciones: la "
            "plantilla no cambia."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(hint)

        buttons = spanish_buttons(
            QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Continuar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selection(self) -> tuple[list[str], int | None, dict]:
        refs = [ref for ref, box in self.checks if box.isChecked()]
        copies = self.copies.value() or None
        overrides = {"price": self.price.value()} if self.price.value() > 0 else {}
        return refs, copies, overrides


class MasterAdView(BaseView):
    title = "Anuncio principal"
    subtitle = "La plantilla del anuncio de canapés que publicas de forma recurrente"

    def build(self) -> None:
        self.add_header_button("Restaurar datos originales", self._restore)
        self.add_header_button("Fotografías…", self._images)
        self.add_header_button("Publicar…", self._publish, primary=True)

        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.body.addWidget(self.notice)

        columns = QHBoxLayout()
        columns.setSpacing(14)

        # --- Formulario de la plantilla ---
        form_card = Card()
        form_card.add(SectionTitle("Plantilla maestra"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container.setObjectName("CardContent")
        form = QFormLayout(container)
        form.setSpacing(9)

        self.name = QLineEdit()
        self.title_field = QLineEdit()
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 100000)
        self.price.setDecimals(2)
        self.price.setSuffix(" €")
        self.condition = QComboBox()
        self.condition.setEditable(True)
        self.condition.addItems(["Nuevo", "Como nuevo", "En buen estado", "Con uso"])
        self.attr_uso = QLineEdit()
        self.attr_color = QLineEdit()
        self.attr_material = QLineEdit()
        self.attr_brand = QLineEdit()
        self.attr_brand.setPlaceholderText(
            "Marca del producto (p. ej. Pikolin). Vacío = el anuncio se publica sin marca"
        )
        self.active_box = QCheckBox("Anuncio activo: el bot lo publica (desmárcalo para pausarlo)")
        self.shipping_box = QCheckBox(
            "Envío activado en Wallapop (desmarcado = solo venta en mano, sin envío)"
        )
        self.location = QLineEdit()
        self.location.setPlaceholderText("Ciudad donde se publica, p. ej. Barcelona, Madrid, Valencia…")
        self.locked_box = QCheckBox(
            "Plantilla única activa: todos los anuncios automáticos usan exactamente "
            "este título, precio y descripción"
        )
        self.category = QLineEdit()
        self.category.setPlaceholderText("Sin categoría (obligatoria para publicar en real)")
        self.subcategory = QLineEdit()
        self.whatsapp = QLineEdit()
        self.delivery = QLineEdit()
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("Separadas por comas")
        self.keywords = QLineEdit()
        self.keywords.setPlaceholderText("Separadas por comas")
        self.aliases = QLineEdit()
        self.aliases.setPlaceholderText("Cómo lo llamas en el chat, separado por comas")
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(170)

        self.variants = QTableWidget(0, 2)
        self.variants.setHorizontalHeaderLabels(["Medida", "Precio oferta (€)"])
        self.variants.verticalHeader().setVisible(False)
        self.variants.horizontalHeader().setStretchLastSection(True)
        self.variants.setFixedHeight(140)
        variant_buttons = QHBoxLayout()
        add_variant = QPushButton("Añadir medida")
        add_variant.clicked.connect(self._add_variant)
        remove_variant = QPushButton("Quitar medida")
        remove_variant.clicked.connect(self._remove_variant)
        variant_buttons.addWidget(add_variant)
        variant_buttons.addWidget(remove_variant)
        variant_buttons.addStretch(1)

        form.addRow("Nombre interno", self.name)
        form.addRow("", self.locked_box)
        form.addRow("Título", self.title_field)
        form.addRow("Precio", self.price)
        form.addRow("Estado", self.condition)
        form.addRow("Categoría / uso", self.attr_uso)
        form.addRow("Color", self.attr_color)
        form.addRow("Material", self.attr_material)
        form.addRow("Marca", self.attr_brand)
        form.addRow("Ubicación", self.location)
        form.addRow("Envío", self.shipping_box)
        form.addRow("", self.active_box)
        form.addRow("Categoría", self.category)
        form.addRow("Subcategoría", self.subcategory)
        form.addRow("Ofertas por medida", self.variants)
        form.addRow("", self._wrap(variant_buttons))
        form.addRow("WhatsApp", self.whatsapp)
        form.addRow("Transporte", self.delivery)
        form.addRow("Etiquetas", self.tags)
        form.addRow("Palabras clave", self.keywords)
        form.addRow("Nombres en el chat", self.aliases)
        form.addRow("Descripción", self.description)
        variables = QLabel(
            "Variables de la descripción: {whatsapp} y {precio_<medida>} "
            "(por ejemplo {precio_135x190}). Se rellenan con los campos de arriba."
        )
        variables.setWordWrap(True)
        variables.setStyleSheet(f"color: {theme.TEXT_FAINT}; font-size: 11px;")
        form.addRow("", variables)
        # Dentro de un área con scroll, Qt comprime las filas si no se les da
        # una altura mínima (el precio llegaba a quedar ilegible).
        for field in (
            self.name, self.title_field, self.price, self.condition, self.category,
            self.attr_uso, self.attr_color, self.attr_material, self.location, self.active_box, self.locked_box,
            self.subcategory, self.whatsapp, self.delivery, self.tags, self.keywords,
            self.aliases,
        ):
            field.setMinimumHeight(36)
        container.setMinimumHeight(container.sizeHint().height())
        scroll.setWidget(container)
        form_card.add(scroll)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_button = QPushButton("Guardar cambios en la plantilla")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self._save)
        save_row.addWidget(self.save_button)
        form_card.body.addLayout(save_row)
        columns.addWidget(form_card, 3)

        # --- Vista previa, calidad y publicaciones ---
        right = QVBoxLayout()
        preview_card = Card()
        preview_card.add(SectionTitle("Así se publicará"))
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        preview_card.add(self.preview)
        right.addWidget(preview_card, 3)

        publications_card = Card()
        self.stats_label = SectionTitle("Publicaciones")
        publications_card.add(self.stats_label)
        self.publications = build_table(["Cuenta", "Precio", "Estado", "Cambios propios"])
        publications_card.add(self.publications)
        right.addWidget(publications_card, 2)
        columns.addLayout(right, 2)

        self.body.addLayout(columns, 1)

    @staticmethod
    def _wrap(layout) -> QWidget:
        widget = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        widget.setLayout(layout)
        return widget

    # ------------------------------------------------------------------
    def _master(self):
        return self.app.master_ads.get()

    def refresh(self) -> None:
        master = self._master()
        if master is None:
            self.notice.setText("No hay ningún anuncio principal configurado.")
            return
        self._key = master.key
        if self.app.demo_mode:
            self.notice.setText(
                f"<b style='color:{theme.WARNING}'>MODO DEMO</b> — publicar crea anuncios "
                f"simulados. Si la plantilla no tiene categoría o fotos propias, se usan unas "
                f"de demostración que no se guardan en ella."
            )
        else:
            self.notice.setText(
                f"<b style='color:{theme.SUCCESS}'>WALLAPOP REAL</b> — publicar crea anuncios "
                f"de verdad en las cuentas elegidas, siempre con confirmación previa."
            )

        self.name.setText(master.name)
        self.title_field.setText(master.title)
        self.attr_uso.setText(str(master.attributes.get("uso") or ""))
        self.attr_color.setText(str(master.attributes.get("color") or ""))
        self.attr_material.setText(str(master.attributes.get("material") or ""))
        self.attr_brand.setText(str(master.attributes.get("marca") or ""))
        self.location.setText(str(master.attributes.get("ubicacion") or ""))
        self.active_box.setChecked(str(master.attributes.get("activo") or "sí") != "no")
        self.shipping_box.setChecked(str(master.attributes.get("envio") or "no") == "sí")
        self.locked_box.setChecked(master.locked)
        self.price.setValue(master.price or 0)
        self.condition.setCurrentText(master.condition or "")
        self.category.setText(master.category or "")
        self.subcategory.setText(master.subcategory or "")
        self.whatsapp.setText(master.contact_whatsapp or "")
        self.delivery.setText(master.delivery_note or "")
        self.tags.setText(", ".join(master.tags))
        self.keywords.setText(", ".join(master.keywords))
        self.aliases.setText(", ".join(master.aliases))
        self.description.setPlainText(master.description_pattern)
        self.variants.setRowCount(0)
        for variant in master.variants:
            self._add_variant(variant.get("medida", ""), variant.get("precio", ""))

        self._refresh_preview(master)

    def _refresh_preview(self, master) -> None:
        refs = [a.internal_ref for a in self.app.accounts.list_accounts()][:1] or ["(sin cuenta)"]
        try:
            preview = self.app.master_ads.build_previews(master.key, refs)[0]
        except ValueError as exc:
            self.preview.setPlainText(str(exc))
            return
        lines = [
            f"<h3>{preview.title}</h3>",
            f"<p><b>{master.features_line}</b></p>",
            f"<p><b>Precio:</b> {(preview.price or 0):.2f} €".replace(".", ",") + "</p>",
            f"<p><b>Categoría:</b> {preview.category or '(sin categoría)'}"
            + (" <i>(de demostración)</i>" if not master.category and preview.category else "")
            + "</p>",
            f"<p><b>Fotografías:</b> {len(preview.image_paths)}</p>",
            "<pre style='white-space: pre-wrap; font-family: inherit'>"
            + preview.description
            + "</pre>",
        ]
        quality = preview.quality
        if quality is not None:
            color = theme.SUCCESS if quality.can_publish else theme.DANGER
            state = "se puede publicar" if quality.can_publish else "NO se puede publicar"
            lines.append(f"<p style='color:{color}'><b>Calidad {quality.score}/100 — {state}</b></p>")
            for issue in quality.errors:
                lines.append(f"<p style='color:{theme.DANGER}'>✗ {issue.message} {issue.suggestion}</p>")
            for issue in quality.warnings:
                lines.append(f"<p style='color:{theme.WARNING}'>⚠ {issue.message} (no bloquea)</p>")
        if master.missing_variables:
            lines.append(
                f"<p style='color:{theme.DANGER}'>Variables sin valor: "
                f"{', '.join(master.missing_variables)}</p>"
            )
        self.preview.setHtml("".join(lines))

        stats = self.app.master_ads.stats(master.key)
        self.stats_label.setText(
            f"Publicaciones: {stats['publicaciones']} · activas {stats['activas']} · "
            f"visitas {stats['visitas']} · favoritos {stats['favoritos']}"
        )
        publications = self.app.master_ads.publications(master.key)
        fill_table(
            self.publications,
            [
                [
                    p.account_alias,
                    f"{p.price:.2f} €" if p.price else "—",
                    listing_status_label(p.status),
                    ", ".join(f"{k}={v}" for k, v in p.overrides.items()) or "—",
                ]
                for p in publications
            ],
            row_data=[p.id for p in publications],
        )

    # ------------------------------------------------------------------
    def _add_variant(self, medida: str = "", precio="") -> None:
        row = self.variants.rowCount()
        self.variants.insertRow(row)
        self.variants.setItem(row, 0, QTableWidgetItem(str(medida)))
        self.variants.setItem(row, 1, QTableWidgetItem(str(precio)))

    def _remove_variant(self) -> None:
        row = self.variants.currentRow()
        if row >= 0:
            self.variants.removeRow(row)

    def _collect(self) -> dict:
        variants = []
        for row in range(self.variants.rowCount()):
            medida = (self.variants.item(row, 0).text() if self.variants.item(row, 0) else "").strip()
            raw = (self.variants.item(row, 1).text() if self.variants.item(row, 1) else "").strip()
            if not medida:
                continue
            try:
                precio = float(raw.replace(",", ".").replace("€", ""))
                precio = int(precio) if precio.is_integer() else precio
            except ValueError as exc:
                raise ValueError(f"El precio de la medida {medida} no es un número: «{raw}».") from exc
            variants.append({"medida": medida, "precio": precio})
        return {
            "name": self.name.text().strip(),
            "title": self.title_field.text(),
            "attributes": {
                "estado": self.condition.currentText().strip(),
                "uso": self.attr_uso.text().strip(),
                "color": self.attr_color.text().strip(),
                "material": self.attr_material.text().strip(),
                "marca": self.attr_brand.text().strip(),
                "ubicacion": self.location.text().strip(),
                "activo": "sí" if self.active_box.isChecked() else "no",
                "envio": "sí" if self.shipping_box.isChecked() else "no",
            },
            "locked": self.locked_box.isChecked(),
            "price": self.price.value() or None,
            "condition": self.condition.currentText().strip() or None,
            "category": self.category.text().strip(),
            "subcategory": self.subcategory.text().strip(),
            "contact_whatsapp": self.whatsapp.text().strip(),
            "delivery_note": self.delivery.text().strip(),
            "tags": _split(self.tags.text()),
            "keywords": _split(self.keywords.text()),
            "aliases": _split(self.aliases.text()),
            "description": self.description.toPlainText(),
            "variants": variants,
        }

    def _save(self) -> None:
        master = self._master()
        try:
            values = self._collect()
        except ValueError as exc:
            show_error(self, str(exc))
            return
        current = {
            "name": master.name,
            "title": master.title,
            "attributes": master.attributes,
            "locked": master.locked,
            "price": master.price,
            "condition": master.condition,
            "category": master.category or "",
            "subcategory": master.subcategory or "",
            "contact_whatsapp": master.contact_whatsapp or "",
            "delivery_note": master.delivery_note or "",
            "tags": master.tags,
            "keywords": master.keywords,
            "aliases": master.aliases,
            "description": master.description_pattern,
            "variants": master.variants,
        }
        changes = {k: v for k, v in values.items() if v != current.get(k)}
        if not changes:
            info_box(self, "Sin cambios", "La plantilla no tiene cambios que guardar.")
            return
        detail = "\n".join(
            f"• {k}: {str(current.get(k))[:70]} → {str(v)[:70]}" for k, v in changes.items()
        )
        if not ask_confirmation(
            self,
            "Guardar cambios en la plantilla",
            f"Vas a cambiar la PLANTILLA MAESTRA:\n\n{detail}\n\n"
            f"Los anuncios ya publicados no se modifican.",
        ):
            return
        try:
            self.app.master_ads.update(master.key, changes, confirmed=True, actor="usuario")
        except ValueError as exc:
            show_error(self, str(exc))
            return
        self.refresh()

    def _restore(self) -> None:
        if not ask_confirmation(
            self,
            "Restaurar datos originales",
            "La plantilla volverá a los datos que proporcionó el cliente (título, "
            "características, precio, descripción, ofertas y WhatsApp). Las fotografías "
            "se conservan.",
        ):
            return
        self.app.master_ads.restore_original(self._master().key, confirmed=True)
        self.refresh()

    def _images(self) -> None:
        ImagesDialog(master_adapter(self.app, self._master().key), self).exec()
        self.refresh()

    def _publish(self) -> None:
        dialog = PublishDialog(self.app, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        refs, copies, overrides = dialog.selection()
        if not refs:
            show_error(self, "Marca al menos una cuenta conectada.")
            return
        master = self._master()
        previews = self.app.master_ads.build_previews(master.key, refs, copies, overrides)
        aliases = {a.internal_ref: a.alias for a in self.app.accounts.list_accounts()}
        counts: dict[str, int] = {}
        for preview in previews:
            counts[preview.account_ref] = counts.get(preview.account_ref, 0) + 1
        blocked = [p for p in previews if not p.can_publish]
        text = (
            f"Voy a publicar {len(previews)} anuncio(s) de «{master.name}»:\n\n"
            + "\n".join(f"• {aliases.get(r, r)}: {n}" for r, n in counts.items())
            + f"\n\nPrecio: {(previews[0].price or 0):.2f} €".replace(".", ",")
        )
        if overrides:
            text += " (solo para estas publicaciones; la plantilla no cambia)"
        if blocked:
            reasons = sorted({i.message for p in blocked for i in (p.quality.errors if p.quality else [])})
            text += f"\n\nNO se podrán publicar {len(blocked)}: " + "; ".join(reasons)
        queue = self.app.publish_queue
        text += (
            f"\n\nSe publicarán de uno en uno, con al menos {queue.interval} segundos entre "
            f"publicaciones. Puedes seguir el progreso en «Publicación automática»."
        )
        if self.app.demo_mode:
            text += "\n\nMODO DEMO: no se enviará nada a Wallapop; es una simulación."
        if not ask_confirmation(self, "Confirmar publicación", text):
            return
        try:
            queue.enqueue_master(master.key, refs, copies, overrides, actor="usuario")
        except ValueError as exc:
            show_error(self, str(exc))
            return
        window = self.window()
        if hasattr(window, "go_to_view"):
            window.go_to_view("Publicación automática")
        else:
            info_box(self, "Cola creada", "Sigue el progreso en «Publicación automática».")
        self.refresh()
