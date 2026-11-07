"""Punto de entrada de LOT Bot."""

from __future__ import annotations

import logging
import sys
import traceback

from lot_bot import APP_NAME, ORG_NAME, __version__

logger = logging.getLogger(__name__)


def _install_exception_hook() -> None:
    """Muestra un mensaje entendible si algo falla de forma inesperada."""

    def hook(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical(
            "Error no controlado", exc_info=(exc_type, exc_value, exc_traceback)
        )
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                box = QMessageBox()
                box.setWindowTitle(f"{APP_NAME} · error inesperado")
                box.setIcon(QMessageBox.Icon.Critical)
                box.setText(
                    "Se ha producido un error inesperado.\n"
                    "El detalle técnico se ha guardado en los registros."
                )
                box.setDetailedText(
                    "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
                )
                box.exec()
        except Exception:  # pragma: no cover
            pass

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    """Arranca la aplicación de escritorio."""
    from PySide6.QtCore import QLocale, Qt
    from PySide6.QtWidgets import QApplication

    from lot_bot.bootstrap import create_application, seed_demo_catalog
    from lot_bot.ui.main_window import MainWindow
    from lot_bot.ui.theme import stylesheet

    # Números y precios con formato español (11,44 €) en toda la interfaz.
    QLocale.setDefault(QLocale(QLocale.Language.Spanish, QLocale.Country.Spain))
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
    qt_app = QApplication(argv if argv is not None else sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setApplicationVersion(__version__)
    qt_app.setOrganizationName(ORG_NAME)
    # «Fusion»: el tema se ve igual en todos los Windows (el estilo nativo
    # ignora parte de la hoja de estilos: flechas, casillas, desplegables).
    qt_app.setStyle("Fusion")
    qt_app.setStyleSheet(stylesheet())

    _install_exception_hook()

    try:
        app = create_application()
    except Exception as exc:
        logger.critical("No se ha podido iniciar LOT Bot", exc_info=True)
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox()
        box.setWindowTitle(f"{APP_NAME} · no se ha podido iniciar")
        box.setIcon(QMessageBox.Icon.Critical)
        box.setText(f"LOT Bot no ha podido arrancar:\n\n{exc}")
        box.setDetailedText(traceback.format_exc())
        box.exec()
        return 1

    if app.demo_mode:
        seed_demo_catalog(app)

    window = MainWindow(app)
    window.show()
    logger.info("Ventana principal abierta.")
    return qt_app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
