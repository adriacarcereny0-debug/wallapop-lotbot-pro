"""Pantalla «Estadísticas»: cuenta → anuncio → visualizaciones → favoritos →
rendimiento, con histórico y recomendaciones.

Nada se inventa: lo que no se ha podido leer aparece como «No disponible».
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from lot_bot.stats import NOT_AVAILABLE, show
from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import (
    Card,
    SectionTitle,
    build_table,
    fill_table,
    info_box,
    selected_row_data,
)

SORTS = [
    ("Más visualizaciones", "visualizaciones"),
    ("Más favoritos", "favoritos"),
    ("Mejor rendimiento (visualizaciones/día)", "rendimiento"),
    ("Más recientes", "fecha"),
]


def _num(value) -> str:
    if value is None:
        return NOT_AVAILABLE
    if isinstance(value, float):
        return f"{value:.2f}".replace(".", ",")
    return str(value)


class StatisticsView(BaseView):
    title = "Estadísticas"
    subtitle = "Visualizaciones y favoritos reales de tus anuncios, por cuenta"

    def build(self) -> None:
        self.refresh_button = self.add_header_button(
            "Actualizar estadísticas", self._capture, primary=True
        )

        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.body.addWidget(self.notice)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Cuenta:"))
        self.account = QComboBox()
        self.account.setMinimumHeight(32)
        self.account.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.account, 1)
        filters.addWidget(QLabel("Ordenar por:"))
        self.sort = QComboBox()
        self.sort.setMinimumHeight(32)
        for label, key in SORTS:
            self.sort.addItem(label, key)
        self.sort.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.sort, 1)
        self.body.addLayout(filters)

        card = Card()
        self.table = build_table(
            [
                "Cuenta",
                "Anuncio",
                "Visualizaciones",
                "Favoritos",
                "Visualiz./día",
                "Fav. por 100 visitas",
                "Publicado",
                "Estado",
                "Mediciones",
                "Enlace",
            ]
        )
        card.add(self.table)
        row = QHBoxLayout()
        history = QPushButton("Ver histórico del anuncio")
        history.clicked.connect(self._history)
        row.addWidget(history)
        row.addStretch(1)
        card.body.addLayout(row)
        self.body.addWidget(card, 3)

        advice = Card()
        advice.add(SectionTitle("Análisis y recomendaciones"))
        self.advice = QPlainTextEdit()
        self.advice.setReadOnly(True)
        self.advice.setMinimumHeight(140)
        advice.add(self.advice)
        self.body.addWidget(advice, 2)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self.notice.setText(self.app.stats.availability_note())
        current = self.account.currentData()
        self.account.blockSignals(True)
        self.account.clear()
        self.account.addItem("Todas las cuentas", None)
        for account in self.app.accounts.list_accounts(include_demo=self.app.demo_mode):
            self.account.addItem(account.alias, account.internal_ref)
        index = self.account.findData(current)
        self.account.setCurrentIndex(max(index, 0))
        self.account.blockSignals(False)
        self.refresh_button.setEnabled(self.app.stats.available)

        rows = self.app.stats.table(self.account.currentData(), sort_by=self.sort.currentData())
        self._rows = rows
        fill_table(
            self.table,
            [
                [
                    r.account_alias,
                    r.title,
                    show(r.views),
                    show(r.favorites),
                    _num(r.views_per_day),
                    _num(r.favorites_rate),
                    r.published_at.strftime("%d/%m/%Y %H:%M") if r.published_at else NOT_AVAILABLE,
                    r.status,
                    r.measurements,
                    r.url or NOT_AVAILABLE,
                ]
                for r in rows
            ],
            row_data=[r.listing_id for r in rows],
            colorizer=lambda row, col, value: theme.TEXT_FAINT if value == NOT_AVAILABLE else None,
        )
        self._refresh_advice()

    def _refresh_advice(self) -> None:
        ref = self.account.currentData()
        result = self.app.optimizer.recommendations(ref)
        lines = []
        if self.app.demo_mode:
            lines.append("MODO DEMO: análisis sobre datos simulados.\n")
        top = self.app.analyzer.top("visualizaciones", 3, ref)["anuncios"]
        if top:
            lines.append("DATOS — Más visualizaciones:")
            lines += [f"  • {a['cuenta']} · {a['titulo'][:40]} · {a['visualizaciones']}" for a in top]
        top_f = self.app.analyzer.top("favoritos", 3, ref)["anuncios"]
        if top_f:
            lines.append("DATOS — Más favoritos:")
            lines += [f"  • {a['cuenta']} · {a['titulo'][:40]} · {a['favoritos']}" for a in top_f]
        lines.append("")
        if result["recomendaciones"]:
            lines.append("RECOMENDACIONES (no cambian nada por sí solas):")
            for item in result["recomendaciones"]:
                lines.append(f"  • {item['recomendacion']}")
                lines.append(f"    Basado en: {item['basado_en']}")
            lines.append("")
            lines.append(result["nota"])
        else:
            lines += result["sin_datos_suficientes"]
        self.advice.setPlainText("\n".join(lines))

    def _capture(self) -> None:
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Leyendo estadísticas…")
        ref = self.account.currentData()

        def success(result) -> None:
            text = (
                f"Con datos: {result['leidos']}\nSin datos disponibles: {result['sin_datos']}\n"
                f"Omitidos (sin dirección o cuenta sin sesión): {result['omitidos']}"
            )
            if result["errores"]:
                text += "\n\nAvisos:\n" + "\n".join(f"• {e}" for e in result["errores"][:8])
            info_box(self, "Estadísticas actualizadas", text)

        def done(*_) -> None:
            self.refresh_button.setText("Actualizar estadísticas")
            self.refresh()

        self.run_task(self.app.stats.capture, [ref] if ref else None, on_success=success, on_done=done)

    def _history(self) -> None:
        selected = selected_row_data(self.table)
        if not selected:
            info_box(self, "Histórico", "Selecciona un anuncio de la tabla.")
            return
        rows = self.app.stats.history(selected[0])
        dialog = QDialog(self)
        dialog.setWindowTitle("Histórico de estadísticas")
        dialog.resize(560, 360)
        layout = QVBoxLayout(dialog)
        table = build_table(["Fecha", "Visualizaciones", "Favoritos", "Estado", "Origen"])
        fill_table(
            table,
            [
                [
                    r["fecha"].strftime("%d/%m/%Y %H:%M"),
                    show(r["visualizaciones"]),
                    show(r["favoritos"]),
                    r["estado"] or "",
                    "simulado (DEMO)" if r["origen"] == "demo" else r["origen"],
                ]
                for r in rows
            ],
        )
        layout.addWidget(table)
        if not rows:
            layout.addWidget(QLabel("Este anuncio todavía no tiene mediciones."))
        dialog.exec()
