"""Pantalla de historial de acciones."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QTextEdit

from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import (
    Card,
    SectionTitle,
    StatCard,
    Toolbar,
    build_table,
    fill_table,
    selected_row_data,
)


class HistoryView(BaseView):
    title = "Historial de acciones"
    subtitle = "Qué se ha hecho, cuándo, en qué cuenta y con qué resultado"

    def build(self) -> None:
        self.add_header_button("Actualizar", self.refresh)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.total_card = StatCard("Acciones (7 días)")
        self.ok_card = StatCard("Correctas")
        self.error_card = StatCard("Con error")
        for card in (self.total_card, self.ok_card, self.error_card):
            stats_row.addWidget(card)
        stats_row.addStretch(1)
        self.body.addLayout(stats_row)

        toolbar = Toolbar()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrar por acción o producto…")
        self.search.textChanged.connect(self.refresh)
        toolbar.add(self.search, 1)
        self.account_filter = QComboBox()
        self.account_filter.currentIndexChanged.connect(self.refresh)
        toolbar.add(self.account_filter)
        self.errors_only = QCheckBox("Solo errores")
        self.errors_only.stateChanged.connect(self.refresh)
        toolbar.add(self.errors_only)
        self.body.addWidget(toolbar)

        card = Card()
        self.table = build_table(["Fecha", "Quién", "Acción", "Cuenta", "Elemento", "Resultado"])
        self.table.itemSelectionChanged.connect(self._on_selection)
        card.add(self.table)
        self.body.addWidget(card, 2)

        detail_card = Card()
        detail_card.add(SectionTitle("Detalle"))
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFixedHeight(130)
        detail_card.add(self.detail)
        self.body.addWidget(detail_card)

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

        entries = self.app.audit.recent(
            limit=500,
            account_ref=self.account_filter.currentData(),
            only_errors=self.errors_only.isChecked(),
        )
        text = self.search.text().strip().lower()
        if text:
            entries = [
                e
                for e in entries
                if text in e.action.lower() or text in (e.target or "").lower()
            ]
        self._entries = entries

        fill_table(
            self.table,
            [
                [
                    e.timestamp.strftime("%d/%m/%Y %H:%M"),
                    e.actor,
                    e.action,
                    e.account_ref or "—",
                    e.target or "—",
                    e.result.upper(),
                ]
                for e in entries
            ],
            row_data=[e.id for e in entries],
            colorizer=lambda row, col, value: (
                theme.DANGER
                if col == 5 and str(value) == "ERROR"
                else (theme.SUCCESS if col == 5 and str(value) == "OK" else None)
            ),
        )
        stats = self.app.audit.stats(days=7)
        self.total_card.update_value(stats["total"])
        self.ok_card.update_value(stats["correctas"])
        self.error_card.update_value(stats["errores"])
        self.header.set_subtitle(f"{len(entries)} registro(s) mostrados")

    def _on_selection(self) -> None:
        ids = selected_row_data(self.table)
        if not ids:
            self.detail.clear()
            return
        entry = next((e for e in self._entries if e.id == ids[0]), None)
        if entry is None:
            return
        lines = [entry.format_line()]
        if entry.detail:
            lines.append(f"\nDetalle: {entry.detail}")
        if entry.error:
            lines.append(f"\nError: {entry.error}")
        self.detail.setPlainText("\n".join(lines))
