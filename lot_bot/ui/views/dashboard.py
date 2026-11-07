"""Panel principal: resumen del estado del negocio."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel

from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import Card, SectionTitle, StatCard, build_table, fill_table


class DashboardView(BaseView):
    title = "Panel"
    subtitle = "Resumen de cuentas, catálogo y actividad"

    def build(self) -> None:
        self.add_header_button("Actualizar", self.refresh)
        self.sync_button = self.add_header_button("Sincronizar ahora", self._sync, primary=True)

        # --- Aviso de modo ---
        self.mode_card = Card()
        self.mode_label = QLabel()
        self.mode_label.setWordWrap(True)
        self.mode_card.add(self.mode_label)
        self.body.addWidget(self.mode_card)

        # --- Cifras ---
        grid = QGridLayout()
        grid.setSpacing(12)
        self.stats = {
            "cuentas": StatCard("Cuentas conectadas"),
            "anuncios": StatCard("Anuncios activos"),
            "productos": StatCard("Productos en catálogo"),
            "mensajes": StatCard("Mensajes sin leer"),
            "calidad": StatCard("Anuncios con incidencias"),
            "errores": StatCard("Errores (7 días)"),
        }
        for index, card in enumerate(self.stats.values()):
            grid.addWidget(card, index // 3, index % 3)
        self.body.addLayout(grid)

        # --- Cuentas y actividad ---
        columns = QHBoxLayout()
        columns.setSpacing(14)

        accounts_card = Card()
        accounts_card.add(SectionTitle("Cuentas"))
        self.accounts_table = build_table(["Cuenta", "Estado", "Anuncios", "Última sincronización"])
        self.accounts_table.setMinimumHeight(180)
        accounts_card.add(self.accounts_table)
        columns.addWidget(accounts_card, 1)

        activity_card = Card()
        activity_card.add(SectionTitle("Actividad reciente"))
        self.activity_table = build_table(["Fecha", "Acción", "Cuenta", "Resultado"])
        self.activity_table.setMinimumHeight(180)
        activity_card.add(self.activity_table)
        columns.addWidget(activity_card, 1)

        self.body.addLayout(columns, 1)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        backend = self.app.backend
        color = theme.WARNING if backend.demo else theme.SUCCESS
        self.mode_label.setText(
            f"<b style='color:{color}'>{backend.label}</b> · {backend.reason}"
        )
        self.sync_button.setEnabled(True)

        accounts = self.app.accounts.list_accounts()
        connected = [
            a for a in accounts if a.is_connected and (backend.demo or not a.is_demo)
        ]
        listing_stats = self.app.listings.stats()
        product_counts = self.app.catalog.count_products()
        audit_stats = self.app.audit.stats(days=7)

        self.stats["cuentas"].update_value(
            f"{len(connected)}/{len(accounts)}", "cuentas conectadas"
        )
        self.stats["anuncios"].update_value(
            listing_stats["activos"], f"de {listing_stats['total']} en total"
        )
        master_stats = self.app.master_ads.stats()
        self.stats["productos"].update_value(
            product_counts["total"],
            f"{product_counts.get('published', 0)} publicados · anuncio principal: "
            f"{master_stats['publicaciones']} publicaciones",
        )
        unread = self.app.messages.unread_count() if self.app.messages.messaging_available else "—"
        self.stats["mensajes"].update_value(
            unread,
            "mensajería no disponible" if unread == "—" else "conversaciones sin leer",
        )

        reports = self.app.listings.quality_reports()
        with_errors = sum(1 for r in reports if not r.can_publish)
        self.stats["calidad"].update_value(
            with_errors, f"de {len(reports)} anuncios revisados"
        )
        self.stats["errores"].update_value(
            audit_stats["errores"], f"{audit_stats['correctas']} acciones correctas"
        )

        listings_by_account = self.app.listings.count_by_account()
        fill_table(
            self.accounts_table,
            [
                [
                    a.alias,
                    "Solo demostración" if (a.is_demo and not backend.demo) else a.status_label,
                    listings_by_account.get(a.internal_ref, 0),
                    a.last_sync_at.strftime("%d/%m/%Y %H:%M") if a.last_sync_at else "nunca",
                ]
                for a in accounts
            ],
            row_data=[a.internal_ref for a in accounts],
            colorizer=lambda row, col, value: (
                theme.STATUS_COLORS.get(accounts[row].status.value)
                if col == 1 and row < len(accounts)
                else None
            ),
        )

        entries = self.app.audit.recent(limit=25)
        fill_table(
            self.activity_table,
            [
                [
                    e.timestamp.strftime("%d/%m %H:%M"),
                    e.action,
                    e.account_ref or "—",
                    e.result.upper(),
                ]
                for e in entries
            ],
            colorizer=lambda row, col, value: (
                theme.DANGER if col == 3 and str(value) == "ERROR" else None
            ),
        )

    # ------------------------------------------------------------------
    def _sync(self) -> None:
        refs = [a.internal_ref for a in self.app.accounts.list_accounts() if a.is_connected]
        if not refs:
            return
        self.sync_button.setEnabled(False)
        self.sync_button.setText("Sincronizando…")

        def work():
            listings = self.app.listings.sync_all(refs)
            messages = {}
            if self.app.messages.messaging_available:
                messages = self.app.messages.sync_all(refs)
            return listings, messages

        def done(_result=None):
            self.sync_button.setEnabled(True)
            self.sync_button.setText("Sincronizar ahora")
            self.refresh()

        self.run_task(work, on_success=lambda r: None, on_done=done)
