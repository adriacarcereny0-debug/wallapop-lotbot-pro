"""Pantalla de configuracion."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lot_bot.config.secrets import mask
from lot_bot.config.settings import reload_settings
from lot_bot.database.models import Template
from lot_bot.templates_engine.defaults import TEMPLATE_VARIABLES
from lot_bot.ui import theme
from lot_bot.ui.views.base import BaseView
from lot_bot.ui.widgets.common import (
    Card,
    SectionTitle,
    ask_confirmation,
    info_box,
    show_error,
)

MIN_CONTROL_HEIGHT = 34


def _scrollable(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setWidget(content)
    return area


def _fixed_height(*widgets: QWidget) -> None:
    """Altura mínima y sin encogerse: el diseño nunca los deja ilegibles."""
    for widget in widgets:
        widget.setMinimumHeight(MIN_CONTROL_HEIGHT)
        widget.setSizePolicy(widget.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Fixed)


class SettingsView(BaseView):
    title = "Configuración"
    subtitle = "Negocio, IA, conexión con Wallapop y plantillas"

    def build(self) -> None:
        self.tabs = QTabWidget()
        # Cada pestaña va dentro de un área con desplazamiento: si la ventana
        # es baja, se hace scroll en vez de aplastar los controles.
        self.tabs.addTab(_scrollable(self._business_tab()), "Negocio")
        self.tabs.addTab(_scrollable(self._templates_tab()), "Plantillas")
        self.tabs.addTab(_scrollable(self._ai_tab()), "IA / Imágenes")
        self.tabs.addTab(_scrollable(self._wallapop_tab()), "Wallapop")
        self.tabs.addTab(_scrollable(self._about_tab()), "Acerca de")
        self.body.addWidget(self.tabs, 1)

    # ------------------------------------------------------------------
    def _business_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        card = Card()
        card.add(SectionTitle("Datos del negocio"))
        note = QLabel(
            "Estos datos se usan en las plantillas y en las respuestas a compradores. "
            "Lo que dejes vacío, el asistente NO se lo inventará: responderá «No dispongo "
            "de esa información»."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        card.add(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.business_fields = {
            "name": QLineEdit(),
            "whatsapp": QLineEdit(),
            "delivery": QLineEdit(),
            "assembly": QLineEdit(),
            "payment": QLineEdit(),
            "location": QLineEdit(),
            "precio_90": QLineEdit(),
            "precio_135": QLineEdit(),
            "precio_150": QLineEdit(),
        }
        self.business_fields["whatsapp"].setPlaceholderText("Número de WhatsApp del negocio")
        self.business_fields["delivery"].setPlaceholderText(
            "Ej.: El transporte y el montaje son gratuitos."
        )
        self.business_fields["assembly"].setPlaceholderText("Ej.: Incluye montaje en domicilio.")
        self.business_fields["payment"].setPlaceholderText("Ej.: Aceptamos efectivo y Bizum.")
        self.business_fields["location"].setPlaceholderText("Ej.: Recogida en nuestra tienda de …")
        for key in ("precio_90", "precio_135", "precio_150"):
            self.business_fields[key].setPlaceholderText("Importe en € (solo el número)")

        labels = {
            "name": "Nombre del negocio",
            "whatsapp": "WhatsApp",
            "delivery": "Condiciones de envío",
            "assembly": "Condiciones de montaje",
            "payment": "Formas de pago",
            "location": "Recogida / tienda",
            "precio_90": "Precio oferta 90x190",
            "precio_135": "Precio oferta 135x190",
            "precio_150": "Precio oferta 150x190",
        }
        for key, field in self.business_fields.items():
            form.addRow(labels[key], field)
        card.body.addLayout(form)

        save = QPushButton("Guardar datos del negocio")
        save.setObjectName("Primary")
        save.clicked.connect(self._save_business)
        card.add(save)
        layout.addWidget(card)
        layout.addStretch(1)
        return widget

    def _templates_tab(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)

        left = Card()
        left.add(SectionTitle("Plantillas"))
        self.template_list = QListWidget()
        self.template_list.currentRowChanged.connect(self._load_template)
        left.add(self.template_list)
        layout.addWidget(left, 1)

        right = Card()
        right.add(SectionTitle("Editar plantilla"))
        form = QFormLayout()
        self.template_title = QLineEdit()
        self.template_description = QPlainTextEdit()
        self.template_description.setFixedHeight(160)
        form.addRow("Patrón del título", self.template_title)
        form.addRow("Patrón de la descripción", self.template_description)
        right.body.addLayout(form)

        variables = QLabel(
            "Variables disponibles:\n"
            + "\n".join(f"  {{{k}}} — {v}" for k, v in list(TEMPLATE_VARIABLES.items())[:14])
        )
        variables.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        right.add(variables)

        save = QPushButton("Guardar plantilla")
        save.setObjectName("Primary")
        save.clicked.connect(self._save_template)
        right.add(save)
        layout.addWidget(right, 2)
        return widget

    def _ai_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        card = Card()
        card.add(SectionTitle("Asistente IA"))

        form = QFormLayout()
        self.ai_provider = QComboBox()
        self.ai_provider.addItem("Órdenes directas (sin IA externa)", "rules")
        self.ai_provider.addItem("Claude (lenguaje natural)", "anthropic")
        form.addRow("Modo del asistente", self.ai_provider)

        self.ai_key_label = QLabel()
        form.addRow("Clave de API", self.ai_key_label)
        self.ai_model_label = QLabel()
        form.addRow("Modelo", self.ai_model_label)
        card.body.addLayout(form)

        note = QLabel(
            "La clave de API se configura en el fichero .env (variable ANTHROPIC_API_KEY) "
            "y nunca se muestra completa ni se guarda en los registros.\n\n"
            "Con «Órdenes directas» el bot funciona sin conexión a ningún servicio de IA: "
            "entiende instrucciones concretas como «Cambia el precio de los canapés de "
            "135x190 a 269 €»."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        card.add(note)

        apply_button = QPushButton("Aplicar modo del asistente")
        apply_button.setObjectName("Primary")
        apply_button.clicked.connect(self._apply_ai)
        card.add(apply_button)
        layout.addWidget(card)
        layout.addWidget(self._flux_card())
        layout.addStretch(1)
        return widget

    def _flux_card(self) -> QWidget:
        card = Card()
        card.add(SectionTitle("Imágenes automáticas — FLUX.2 Pro (Black Forest Labs)"))
        explanation = QLabel(
            "Genera una fotografía de producto distinta para cada anuncio que publica la "
            "cola. La clave se guarda cifrada en este ordenador (no en ningún fichero de "
            "texto) y nunca aparece en los registros. En modo DEMO no se usa: se generan "
            "imágenes de prueba sin gastar créditos."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        card.add(explanation)

        row = QHBoxLayout()
        self.flux_key = QLineEdit()
        self.flux_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.flux_key.setPlaceholderText("Pega aquí la clave de API de Black Forest Labs")
        row.addWidget(self.flux_key, 1)
        self.flux_show = QPushButton("Mostrar")
        self.flux_show.setCheckable(True)
        self.flux_show.toggled.connect(self._toggle_flux_visibility)
        row.addWidget(self.flux_show)
        _fixed_height(self.flux_key, self.flux_show)
        card.body.addLayout(row)

        self.flux_status = QLabel()
        self.flux_status.setWordWrap(True)
        card.add(self.flux_status)

        buttons = QHBoxLayout()
        save = QPushButton("Guardar")
        save.setObjectName("Primary")
        save.clicked.connect(self._save_flux_key)
        buttons.addWidget(save)
        self.flux_test = QPushButton("Probar conexión y ver saldo")
        self.flux_test.clicked.connect(self._test_flux)
        buttons.addWidget(self.flux_test)
        delete = QPushButton("Eliminar clave")
        delete.setObjectName("Danger")
        delete.clicked.connect(self._delete_flux_key)
        buttons.addWidget(delete)
        buttons.addStretch(1)
        card.body.addLayout(buttons)
        return card

    # -- FLUX -------------------------------------------------------------
    def _toggle_flux_visibility(self, visible: bool) -> None:
        from lot_bot.config.api_keys import FLUX

        if visible and not self.flux_key.text():
            self.flux_key.setText(self.app.api_keys.get(FLUX) or "")
        self.flux_key.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        self.flux_show.setText("Ocultar" if visible else "Mostrar")

    def _refresh_flux(self) -> None:
        from lot_bot.config.api_keys import FLUX

        key = self.app.api_keys.get(FLUX)
        self.flux_key.clear()
        self.flux_show.setChecked(False)
        self.flux_status.setText(
            f"Clave guardada: {mask(key)}" if key else "No hay ninguna clave guardada."
        )

    def _save_flux_key(self) -> None:
        from lot_bot.config.api_keys import FLUX

        try:
            self.app.api_keys.set(FLUX, self.flux_key.text())
        except ValueError as exc:
            show_error(self, str(exc))
            return
        self.app.refresh_image_generator()
        self.app.audit.record_success("Clave de FLUX.2 Pro guardada", actor="usuario")
        self._refresh_flux()
        info_box(self, "Clave guardada", "La clave se ha guardado cifrada en este ordenador.")

    def _delete_flux_key(self) -> None:
        from lot_bot.config.api_keys import FLUX
        from lot_bot.ui.widgets.common import ask_confirmation

        if not ask_confirmation(
            self, "Eliminar clave", "Se borrará la clave de FLUX.2 Pro de este ordenador."
        ):
            return
        self.app.api_keys.delete(FLUX)
        self.app.refresh_image_generator()
        self.app.audit.record_success("Clave de FLUX.2 Pro eliminada", actor="usuario")
        self._refresh_flux()

    def _test_flux(self) -> None:
        from lot_bot.config.api_keys import FLUX
        from lot_bot.images.generation import FluxImageService, GenerationError

        typed = self.flux_key.text().strip()
        key = typed or self.app.api_keys.get(FLUX)
        if not key:
            show_error(self, "Escribe o guarda antes una clave.")
            return
        self.flux_test.setEnabled(False)
        self.flux_status.setText("Comprobando con Black Forest Labs…")

        def work():
            try:
                return ("ok", FluxImageService(lambda: key).get_credits())
            except GenerationError as exc:
                return ("error", exc.user_message)

        def success(result) -> None:
            status, value = result
            if status == "ok":
                credits = "no disponible" if value is None else f"{value:g} créditos"
                self.flux_status.setText(f"Conexión correcta. Saldo: {credits}.")
            else:
                self.flux_status.setText(f"<span style='color:{theme.DANGER}'>{value}</span>")

        self.run_task(work, on_success=success, on_done=lambda *_: self.flux_test.setEnabled(True))

    def _wallapop_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        integration = Card()
        integration.add(SectionTitle("Integración con Wallapop"))
        form = QFormLayout()
        self.integration_mode = QComboBox()
        self.integration_mode.addItem("Modo demostración (sin Wallapop)", "demo")
        self.integration_mode.addItem(
            "Integración mediante navegador (uso personal autorizado)", "navegador"
        )
        form.addRow("Modo", self.integration_mode)
        self.publish_interval = QSpinBox()
        self.publish_interval.setRange(60, 3600)
        self.publish_interval.setSuffix(" s")
        form.addRow("Intervalo mínimo entre publicaciones", self.publish_interval)
        self.publish_images = QComboBox()
        self.publish_images.addItem(
            "Mis fotos marcadas, todas en cada anuncio («Anuncio principal» → «Fotografías…»)",
            False,
        )
        self.publish_images.addItem(
            "Mis fotos marcadas, UNA distinta en cada anuncio (nunca repite una ya publicada)",
            "rotar",
        )
        self.publish_images.addItem(
            "Generar una foto distinta por anuncio con FLUX.2 Pro (solo esa foto; necesita clave)",
            True,
        )
        # Se guarda nada más elegirla (sin pulsar «Aplicar»).
        self.publish_images.currentIndexChanged.connect(self._save_photo_option)
        form.addRow("Fotos de cada anuncio", self.publish_images)
        self.used_photos_label = QLabel()
        self.used_photos_label.setWordWrap(True)
        reset_used = QPushButton("Volver a usar las fotos ya publicadas")
        reset_used.clicked.connect(self._reset_used_photos)
        form.addRow(self.used_photos_label, reset_used)
        self.max_listings = QSpinBox()
        self.max_listings.setRange(0, 5000)
        self.max_listings.setSpecialValueText("Sin límite")
        self.max_listings.setToolTip(
            "Si una cuenta llega a este número de anuncios, antes de publicar uno nuevo se "
            "eliminan los MÁS ANTIGUOS que hagan falta. 0 = no se elimina nada."
        )
        self.max_listings.valueChanged.connect(
            lambda value: self.app.publish_queue.save_settings(max_listings_per_account=value)
        )
        form.addRow("Máximo de anuncios por cuenta\n(borra los más antiguos)", self.max_listings)
        _fixed_height(
            self.integration_mode, self.publish_interval, self.publish_images, self.max_listings
        )
        integration.body.addLayout(form)
        integration_note = QLabel(
            "Con la integración por navegador tú inicias sesión en Wallapop y LOT Bot "
            "publica por ti desde ese navegador, con un mínimo de 60 segundos entre "
            "anuncios. Es para el uso personal autorizado del titular de LOT Bot; no es "
            "una integración oficial de Wallapop. Los pasos de la web están en "
            "wallapop_browser.yaml."
        )
        integration_note.setWordWrap(True)
        integration_note.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        integration.add(integration_note)
        self.selectors_label = QLabel()
        self.selectors_label.setWordWrap(True)
        self.selectors_label.setTextInteractionFlags(
            self.selectors_label.textInteractionFlags().TextSelectableByMouse
        )
        integration.add(self.selectors_label)
        apply_integration = QPushButton("Aplicar")
        apply_integration.setObjectName("Primary")
        _fixed_height(apply_integration)
        apply_integration.clicked.connect(self._apply_integration)
        integration.add(apply_integration)
        layout.addWidget(integration)

        card = Card()
        card.add(SectionTitle("Estado de la conexión"))
        self.wallapop_status = QLabel()
        self.wallapop_status.setWordWrap(True)
        card.add(self.wallapop_status)

        form = QFormLayout()
        self.auth_method_label = QLabel()
        self.auth_method_label.setWordWrap(True)
        self.profile_label = QLabel()
        self.profile_label.setWordWrap(True)
        self.redirect_label = QLabel()
        form.addRow("Mecanismo de acceso", self.auth_method_label)
        form.addRow("Perfil de acceso", self.profile_label)
        form.addRow("Dirección de retorno", self.redirect_label)
        card.body.addLayout(form)

        note = QLabel(
            "LOT Bot no exige una API key. El mecanismo de acceso es el que Wallapop "
            "haya autorizado y se declara en el perfil de acceso. Los valores "
            "sensibles se leen de variables de entorno y nunca se muestran aquí."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        card.add(note)

        buttons = QHBoxLayout()
        select = QPushButton("Seleccionar perfil de acceso…")
        select.clicked.connect(self._select_access_profile)
        buttons.addWidget(select)
        reload_button = QPushButton("Recargar configuración")
        reload_button.clicked.connect(self._reload_settings)
        buttons.addWidget(reload_button)
        buttons.addStretch(1)
        card.body.addLayout(buttons)
        layout.addWidget(card)

        missing_card = Card()
        missing_card.add(SectionTitle("Qué falta para conectar con Wallapop real"))
        self.missing_label = QPlainTextEdit()
        self.missing_label.setReadOnly(True)
        self.missing_label.setFixedHeight(120)
        missing_card.add(self.missing_label)
        layout.addWidget(missing_card)

        capabilities_card = Card()
        capabilities_card.add(SectionTitle("Operaciones disponibles"))
        self.capabilities_label = QPlainTextEdit()
        self.capabilities_label.setReadOnly(True)
        capabilities_card.add(self.capabilities_label)
        layout.addWidget(capabilities_card, 1)
        return widget

    def _about_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        card = Card()
        card.add(SectionTitle("LOT Bot"))
        self.about_label = QLabel()
        self.about_label.setWordWrap(True)
        self.about_label.setTextInteractionFlags(
            self.about_label.textInteractionFlags().TextSelectableByMouse
        )
        card.add(self.about_label)
        layout.addWidget(card)
        layout.addStretch(1)
        return widget

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        business = self.app.reload_business_settings()
        for key, field in self.business_fields.items():
            field.setText(str(business.get(key) or ""))

        self._refresh_templates()

        settings = self.app.settings
        index = self.ai_provider.findData(settings.ai_provider)
        if index >= 0:
            self.ai_provider.setCurrentIndex(index)
        self.ai_key_label.setText(mask(settings.anthropic_api_key))
        self.ai_model_label.setText(settings.ai_model)
        self._refresh_flux()

        index = self.integration_mode.findData(self.app.integration_mode or "demo")
        self.integration_mode.setCurrentIndex(max(index, 0))
        queue_settings = self.app.publish_queue.settings()
        self.publish_interval.setValue(queue_settings["minimum_publish_interval_seconds"])
        self.publish_images.blockSignals(True)  # mostrar no es elegir: no se guarda
        if queue_settings["generate_images"]:
            self.publish_images.setCurrentIndex(self.publish_images.findData(True))
        elif queue_settings.get("rotate_photos"):
            self.publish_images.setCurrentIndex(self.publish_images.findData("rotar"))
        else:
            self.publish_images.setCurrentIndex(0)
        self.publish_images.blockSignals(False)
        self.max_listings.blockSignals(True)
        self.max_listings.setValue(int(queue_settings.get("max_listings_per_account") or 0))
        self.max_listings.blockSignals(False)
        used = len(self.app.publish_queue.used_photos())
        self.used_photos_label.setText(f"Fotos ya publicadas (no se repiten): {used}")
        from lot_bot.wallapop.browser.config import local_config_path

        self.selectors_label.setText(
            f"Para ajustar los pasos de la web, copia el fichero incluido a: "
            f"{local_config_path()}"
        )

        backend = self.app.backend
        method = self.app.auth_method
        color = theme.WARNING if backend.demo else theme.SUCCESS
        self.wallapop_status.setText(
            f"<b style='color:{color}'>{backend.label}</b><br>{backend.reason}"
        )
        ready = "listo" if method.is_ready else "faltan datos"
        self.auth_method_label.setText(f"{method.describe()} ({ready})")
        profile = self.app.access_profile
        self.profile_label.setText(
            str(profile.source_path) if profile.source_path else "(no configurado)"
        )
        self.redirect_label.setText(settings.wallapop_redirect_uri or "(no configurada)")

        if backend.demo and self.app.missing_access_data:
            self.missing_label.setPlainText(
                "\n".join(f"• {item}" for item in self.app.missing_access_data)
            )
        elif backend.demo:
            self.missing_label.setPlainText(
                "Estás en modo DEMO. Para publicar en Wallapop, elige arriba «Integración "
                "mediante navegador» y conecta tus cuentas en Cuentas → «Añadir cuenta Wallapop»."
            )
        else:
            pendientes = [m for m in self.app.missing_access_data]
            self.missing_label.setPlainText(
                "\n".join(f"• {item}" for item in pendientes)
                if pendientes
                else "Nada: el acceso autorizado está completo."
            )

        from lot_bot.wallapop.capabilities import CAPABILITY_LABELS, Capability

        granted = self.app.wallapop.capabilities()
        lines = [
            f"{'✓' if c in granted else '✗'}  {CAPABILITY_LABELS[c]}  ({c.value})"
            for c in Capability
        ]
        lines.append("")
        lines.append(
            "Las operaciones marcadas con ✗ responden "
            "NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS. Para activarlas, decláralas "
            "en el bloque «operations:» del perfil de acceso autorizado."
        )
        self.capabilities_label.setPlainText("\n".join(lines))

        self.about_label.setText(
            f"<b>LOT Bot {self.app.version}</b><br><br>"
            f"Gestión y automatización multicuenta de Wallapop con asistente IA.<br><br>"
            f"Carpeta de datos: {self.app.paths.root}<br>"
            f"Base de datos: {self.app.paths.database}<br>"
            f"Registros: {self.app.paths.logs}<br><br>"
            f"Modo: {self.app.backend_label}<br>"
            f"Asistente: {self.app.agent.provider.describe()}<br><br>"
            f"Este programa debe usarse respetando los términos de Wallapop y la "
            f"autorización de automatización concedida al titular de las cuentas."
        )

    # ------------------------------------------------------------------
    def _refresh_templates(self) -> None:
        from sqlalchemy import select

        with self.app.db.session_scope() as session:
            templates = session.scalars(select(Template).order_by(Template.name)).all()
            self._templates = [
                {
                    "id": t.id,
                    "name": t.name,
                    "title_pattern": t.title_pattern,
                    "description_pattern": t.description_pattern,
                }
                for t in templates
            ]
        self.template_list.blockSignals(True)
        self.template_list.clear()
        for template in self._templates:
            self.template_list.addItem(template["name"])
        self.template_list.blockSignals(False)
        if self._templates:
            self.template_list.setCurrentRow(0)

    def _load_template(self, row: int) -> None:
        if row < 0 or row >= len(getattr(self, "_templates", [])):
            return
        template = self._templates[row]
        self.template_title.setText(template["title_pattern"])
        self.template_description.setPlainText(template["description_pattern"])

    def _save_template(self) -> None:
        row = self.template_list.currentRow()
        if row < 0 or row >= len(self._templates):
            return
        template_id = self._templates[row]["id"]
        with self.app.db.session_scope() as session:
            template = session.get(Template, template_id)
            if template is None:
                return
            template.title_pattern = self.template_title.text().strip()
            template.description_pattern = self.template_description.toPlainText()
        self.app.audit.record_success(
            "Modificación de plantilla", target=self._templates[row]["name"]
        )
        info_box(self, "Plantilla guardada", "Los cambios se aplicarán a las próximas publicaciones.")
        self._refresh_templates()

    def _save_business(self) -> None:
        values = {key: field.text().strip() for key, field in self.business_fields.items()}
        self.app.save_business_settings(values)
        info_box(
            self,
            "Datos guardados",
            "Los datos del negocio se usarán en las plantillas y en las respuestas a compradores.",
        )

    def _apply_ai(self) -> None:
        provider_key = self.ai_provider.currentData()
        settings = self.app.settings
        if provider_key == "anthropic" and not settings.anthropic_api_key:
            show_error(
                self,
                "No hay clave de API configurada.",
                "Añade ANTHROPIC_API_KEY en el fichero .env y reinicia LOT Bot.",
            )
            return
        from lot_bot.ai.anthropic_provider import AnthropicProvider
        from lot_bot.ai.rule_provider import RuleBasedProvider

        provider = (
            AnthropicProvider(settings.anthropic_api_key, settings.ai_model, settings.ai_max_tokens)
            if provider_key == "anthropic"
            else RuleBasedProvider()
        )
        self.app.set_ai_provider(provider)
        self.app.audit.record_success("Cambio de asistente IA", detail=provider.describe())
        info_box(self, "Asistente actualizado", f"Modo activo: {provider.describe()}")
        self.refresh()

    def _save_photo_option(self) -> None:
        choice = self.publish_images.currentData()
        self.app.publish_queue.save_settings(
            generate_images=choice is True, rotate_photos=choice == "rotar"
        )

    def _reset_used_photos(self) -> None:
        if not ask_confirmation(
            self,
            "Volver a usar fotos",
            "Las fotos ya publicadas se podrán volver a subir. Si publicas la misma foto en "
            "varios anuncios o cuentas, Wallapop puede verlo como anuncios repetidos.",
        ):
            return
        self.app.publish_queue.reset_used_photos()
        self.refresh()

    def _apply_integration(self) -> None:
        mode = self.integration_mode.currentData()
        try:
            self.app.publish_queue.save_settings(
                minimum_publish_interval_seconds=self.publish_interval.value(),
                generate_images=self.publish_images.currentData() is True,
                rotate_photos=self.publish_images.currentData() == "rotar",
            )
            if mode != (self.app.integration_mode or "demo"):
                self.app.set_integration_mode(mode)
        except (ValueError, RuntimeError) as exc:
            show_error(self, "No se ha podido aplicar la configuración.", str(exc))
            return
        window = self.window()
        if hasattr(window, "_update_status"):
            window._update_status()
        info_box(self, "Configuración aplicada", f"Modo activo: {self.app.backend_label}")
        self.refresh()

    def _select_access_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Perfil de acceso autorizado de Wallapop", "", "YAML (*.yaml *.yml)"
        )
        if not path:
            return
        info_box(
            self,
            "Perfil seleccionado",
            f"Añade esta línea a tu fichero .env y reinicia LOT Bot:\n\n"
            f"WALLAPOP_ACCESS_PROFILE={Path(path)}\n\n"
            f"LOT Bot no modifica el .env por seguridad.",
        )

    def _reload_settings(self) -> None:
        settings = reload_settings()
        try:
            self.app.rebuild_backend(settings)
        except Exception as exc:
            show_error(self, "No se ha podido aplicar la nueva configuración.", str(exc))
            return
        info_box(self, "Configuración recargada", f"Modo activo: {self.app.backend_label}")
        self.refresh()
