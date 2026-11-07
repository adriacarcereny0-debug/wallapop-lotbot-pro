"""Pantalla de anuncios publicados."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from lot_bot.publishing.listings import ListingFilter
from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import (
    Card,
    Toolbar,
    ask_confirmation,
    build_table,
    fill_table,
    info_box,
    listing_status_label,
    selected_row_data,
    show_error,
    spanish_buttons,
)


class ListingsView(BaseView):
    title = "Anuncios"
    subtitle = "Anuncios publicados en Wallapop, por cuenta"

    def build(self) -> None:
        self.add_header_button("Sincronizar", self._sync, primary=True)
        self.add_header_button("Actualizar", self.refresh)

        toolbar = Toolbar()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar en título o descripción…")
        self.search.returnPressed.connect(self.refresh)
        toolbar.add(self.search, 1)

        self.size_filter = QLineEdit()
        self.size_filter.setPlaceholderText("Medida (135x190)")
        self.size_filter.setMaximumWidth(150)
        self.size_filter.returnPressed.connect(self.refresh)
        toolbar.add(self.size_filter)

        self.account_filter = QComboBox()
        self.account_filter.currentIndexChanged.connect(self.refresh)
        toolbar.add(self.account_filter)
        self.body.addWidget(toolbar)

        card = Card()
        self.table = build_table(
            ["Cuenta", "Título", "Precio", "Estado", "Visitas", "Favoritos", "Fotos", "Origen"]
        )
        self.table.itemSelectionChanged.connect(self._on_selection)
        card.add(self.table)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.price_button = QPushButton("Cambiar precio")
        self.price_button.setObjectName("Primary")
        self.price_button.clicked.connect(self._change_price)
        actions.addWidget(self.price_button)

        self.title_button = QPushButton("Cambiar título")
        self.title_button.clicked.connect(self._change_title)
        actions.addWidget(self.title_button)

        self.detail_button = QPushButton("Ver detalle")
        self.detail_button.clicked.connect(self._show_detail)
        actions.addWidget(self.detail_button)

        self.duplicates_button = QPushButton("Buscar duplicados")
        self.duplicates_button.clicked.connect(self._find_duplicates)
        actions.addWidget(self.duplicates_button)

        self.quality_button = QPushButton("Revisar calidad")
        self.quality_button.clicked.connect(self._check_quality)
        actions.addWidget(self.quality_button)

        actions.addStretch(1)
        self.select_account_button = QPushButton("Seleccionar todos (de esta cuenta)")
        self.select_account_button.setToolTip(
            "Selecciona todos los anuncios activos de la cuenta elegida arriba "
            "(o de todas las cuentas)."
        )
        self.select_account_button.clicked.connect(self._select_all_active)
        actions.addWidget(self.select_account_button)
        self.delete_button = QPushButton("Eliminar seleccionados")
        self.delete_button.setObjectName("Danger")
        self.delete_button.clicked.connect(self._delete)
        actions.addWidget(self.delete_button)
        card.body.addLayout(actions)

        self.selection_label = QLabel()
        self.selection_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        card.add(self.selection_label)

        self.body.addWidget(card, 1)
        self._on_selection()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
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

        listings = self.app.listings.search(self._criteria())
        self._listings = listings
        fill_table(
            self.table,
            [
                [
                    v.account_alias,
                    v.title,
                    f"{v.price:.2f} €" if v.price else "—",
                    listing_status_label(v.status),
                    v.views,
                    v.favorites,
                    len(v.image_urls),
                    ("★ Anuncio principal" + (" (con cambios)" if v.overrides else ""))
                    if v.master_ad_id
                    else (v.product_sku or "—"),
                ]
                for v in listings
            ],
            row_data=[v.id for v in listings],
            colorizer=lambda row, col, value: (
                theme.STATUS_COLORS.get(listings[row].status) if col == 3 and row < len(listings) else None
            ),
        )
        stats = self.app.listings.stats()
        self.header.set_subtitle(
            f"{len(listings)} anuncio(s) mostrados · {stats['activos']} activos · "
            f"precio medio {stats['precio_medio']:.2f} €"
        )
        self._on_selection()

    def _criteria(self) -> ListingFilter:
        return ListingFilter(
            text=self.search.text().strip() or None,
            size=self.size_filter.text().strip() or None,
            account_ref=self.account_filter.currentData(),
            limit=1000,
        )

    # ------------------------------------------------------------------
    def _selected_ids(self) -> list[int]:
        return [int(v) for v in selected_row_data(self.table)]

    def _on_selection(self) -> None:
        ids = self._selected_ids()
        has = bool(ids)
        self.price_button.setEnabled(has)
        self.title_button.setEnabled(len(ids) == 1)
        self.detail_button.setEnabled(len(ids) == 1)
        self.delete_button.setEnabled(has)
        self.selection_label.setText(
            f"{len(ids)} anuncio(s) seleccionados." if has else "Selecciona uno o varios anuncios."
        )

    # ------------------------------------------------------------------
    def _sync(self) -> None:
        refs = [a.internal_ref for a in self.app.accounts.list_accounts() if a.is_connected]
        if not refs:
            show_error(self, "No hay ninguna cuenta conectada.")
            return

        def success(results):
            total = sum(r.get("nuevos", 0) + r.get("actualizados", 0) for r in results.values())
            info_box(self, "Sincronización", f"{total} anuncio(s) sincronizados.")

        self.run_task(
            lambda: self.app.listings.sync_all(refs), on_success=success, on_done=self.refresh
        )

    def _change_price(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Cambiar precio")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Se cambiará el precio de {len(ids)} anuncio(s)."))
        spin = QDoubleSpinBox()
        spin.setRange(0.01, 100000)
        spin.setDecimals(2)
        spin.setSuffix(" €")
        selected = [v for v in self._listings if v.id in ids]
        spin.setValue(selected[0].price or 0 if selected else 0)
        layout.addWidget(spin)

        by_account: dict[str, int] = {}
        for view in selected:
            by_account[view.account_alias] = by_account.get(view.account_alias, 0) + 1
        layout.addWidget(
            QLabel("\n".join(f"• {alias}: {count} anuncio(s)" for alias, count in by_account.items()))
        )

        buttons = spanish_buttons(QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        ))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        price = spin.value()
        if not ask_confirmation(
            self,
            "Confirmar cambio de precio",
            f"Se modificarán {len(ids)} anuncio(s) al precio de {price:.2f} €.\n\n"
            + "\n".join(f"• {alias}: {count} anuncio(s)" for alias, count in by_account.items()),
        ):
            return

        def success(outcomes):
            done = sum(1 for o in outcomes if o.success)
            failed = [o for o in outcomes if not o.success]
            message = f"Precio actualizado en {done} de {len(outcomes)} anuncio(s)."
            if failed:
                message += "\n\nErrores:\n" + "\n".join(f"• {o.account_ref}: {o.message}" for o in failed)
            info_box(self, "Resultado", message)

        self.run_task(
            lambda: self.app.publishing.update_prices(ids, price, confirmed=True, actor="usuario"),
            on_success=success,
            on_done=self.refresh,
        )

    def _change_title(self) -> None:
        ids = self._selected_ids()
        if len(ids) != 1:
            return
        view = self.app.listings.get(ids[0])
        dialog = QDialog(self)
        dialog.setWindowTitle("Cambiar título")
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Cuenta: {view.account_alias}"))
        field = QLineEdit(view.title)
        layout.addWidget(field)
        buttons = spanish_buttons(QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        ))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_title = field.text().strip()
        if not new_title or new_title == view.title:
            return
        if not ask_confirmation(
            self, "Confirmar cambio", f"«{view.title}»\n→\n«{new_title}»"
        ):
            return
        self.run_task(
            lambda: self.app.publishing.update_listing(
                ids[0], {"title": new_title}, confirmed=True, actor="usuario"
            ),
            on_success=lambda outcome: info_box(self, "Resultado", outcome.message),
            on_done=self.refresh,
        )

    def _show_detail(self) -> None:
        ids = self._selected_ids()
        if len(ids) != 1:
            return
        view = self.app.listings.get(ids[0])
        dialog = QDialog(self)
        dialog.setWindowTitle(view.title)
        dialog.resize(620, 520)
        layout = QVBoxLayout(dialog)
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(
            f"Cuenta: {view.account_alias}\n"
            f"Identificador Wallapop: {view.wallapop_item_id or '—'}\n"
            f"Precio: {view.price} {view.currency}\n"
            f"Estado: {view.status}\n"
            f"Categoría: {view.category or '—'}\n"
            f"Visitas: {view.views} · Favoritos: {view.favorites}\n"
            f"Fotografías: {len(view.image_urls)}\n"
            f"Características: {view.attributes}\n\n"
            f"DESCRIPCIÓN:\n{view.description or '(vacía)'}"
        )
        layout.addWidget(viewer)
        buttons = spanish_buttons(QDialogButtonBox(QDialogButtonBox.StandardButton.Close))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _find_duplicates(self) -> None:
        groups = self.app.listings.find_duplicates(self._criteria())
        if not groups:
            info_box(self, "Duplicados", "No se han detectado anuncios duplicados.")
            return
        text = "\n\n".join(
            f"{group.describe()}\n"
            + "\n".join(
                f"   · {m.get('cuenta')}: {m.get('titulo')} ({m.get('precio')} €)"
                for m in group.members
            )
            for group in groups[:20]
        )
        info_box(
            self,
            "Duplicados detectados",
            f"{len(groups)} grupo(s).\n\n{text}\n\n"
            f"LOT Bot no elimina nada automáticamente: revisa y decide tú.",
        )

    def _check_quality(self) -> None:
        reports = self.app.listings.quality_reports(self._criteria())
        with_errors = [r for r in reports if not r.can_publish]
        if not with_errors:
            info_box(
                self,
                "Control de calidad",
                f"{len(reports)} anuncio(s) revisados. Ninguno tiene errores bloqueantes.",
            )
            return
        text = "\n\n".join(
            f"{r.subject} ({r.score}/100)\n" + "\n".join(f"   ✗ {i.message}" for i in r.errors)
            for r in with_errors[:20]
        )
        info_box(
            self,
            "Anuncios con información incompleta",
            f"{len(with_errors)} de {len(reports)} anuncios tienen problemas:\n\n{text}",
        )

    def _select_all_active(self) -> None:
        """Todos los anuncios activos de la lista (la cuenta la elige el filtro)."""
        active = {v.id for v in self._listings if v.status == "active"}
        self.table.clearSelection()
        mode = self.table.selectionMode()
        self.table.setSelectionMode(self.table.SelectionMode.MultiSelection)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and int(item.data(Qt.ItemDataRole.UserRole) or 0) in active:
                self.table.selectRow(row)
        self.table.setSelectionMode(mode)
        self._on_selection()

    def _delete(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        selected = [v for v in self._listings if v.id in ids]
        by_account: dict[str, int] = {}
        for view in selected:
            by_account[view.account_alias] = by_account.get(view.account_alias, 0) + 1
        interval = self.app.publish_queue.interval
        minutes = max(1, round(interval * (len(ids) - 1) / 60)) if len(ids) > 1 else 0
        titles = "\n".join(f"   · {v.title}" for v in selected[:8])
        if len(selected) > 8:
            titles += f"\n   · … y {len(selected) - 8} más"
        if not ask_confirmation(
            self,
            "Eliminar anuncios de Wallapop",
            f"Se van a ELIMINAR {len(ids)} anuncio(s) en Wallapop:\n\n"
            + "\n".join(f"• {alias}: {count} anuncio(s)" for alias, count in by_account.items())
            + f"\n\n{titles}\n\n"
            f"Se eliminan de uno en uno, con {interval} segundos entre cada uno"
            + (f" (unos {minutes} min en total)" if minutes else "")
            + ".\nEsta acción NO se puede deshacer.",
            destructive=True,
        ):
            return
        self.selection_label.setText(
            f"Eliminando {len(ids)} anuncio(s) de uno en uno… no cierres LOT Bot."
        )
        self.delete_button.setEnabled(False)

        def success(outcomes):
            done = [o for o in outcomes if o.success]
            failed = [o for o in outcomes if not o.success]
            message = f"Eliminados: {len(done)} de {len(ids)}."
            if failed:
                message += "\n\nNo eliminados:\n" + "\n".join(f"• {o.message}" for o in failed)
                message += "\n\nHay una captura de cada fallo en la carpeta logs/navegador."
            if len(outcomes) < len(ids):
                message += (
                    f"\n\nSe ha parado antes de terminar: {len(ids) - len(outcomes)} anuncio(s) "
                    "no se han tocado. Resuelve lo que pide Wallapop y vuelve a intentarlo."
                )
            info_box(self, "Resultado", message)

        self.run_task(
            lambda: self.app.publishing.delete_listings(
                ids, confirmed=True, actor="usuario", interval_seconds=interval
            ),
            on_success=success,
            on_done=self.refresh,
        )
