"""Pantallas «Conectar Wallapop» y «Publicación masiva» (Qt sin ventana)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

pytestmark = pytest.mark.ui


@pytest.fixture()
def qt_app():
    from PySide6.QtWidgets import QApplication

    from lot_bot.ui.theme import stylesheet

    application = QApplication.instance() or QApplication([])
    application.setStyleSheet(stylesheet())
    return application


@pytest.fixture()
def silent_dialogs(monkeypatch):
    """Los avisos no deben bloquear la prueba."""
    shown: list[str] = []
    for module in ("lot_bot.ui.views.connect_wallapop", "lot_bot.ui.views.direct_publish"):
        monkeypatch.setattr(f"{module}.info_box", lambda _p, _t, text: shown.append(text))
        monkeypatch.setattr(f"{module}.show_error", lambda _p, text, *_a: shown.append(text))
    return shown


def test_menu_incluye_las_pantallas_nuevas():
    from lot_bot.ui.main_window import NAVIGATION

    nombres = [n for n, _, _ in NAVIGATION]
    assert "Conectar Wallapop" in nombres
    assert "Publicación masiva" in nombres
    # Lo que ya existía sigue ahí.
    for anterior in ("Cuentas de Wallapop", "Publicación automática", "Anuncio principal"):
        assert anterior in nombres


def test_conectar_wallapop_muestra_estado_y_conecta(qt_app, app, silent_dialogs):
    from lot_bot.ui.views.connect_wallapop import ConnectWallapopView
    from lot_bot.ui.widgets.workers import TaskRunner

    runner = TaskRunner(1)
    view = ConnectWallapopView(app, runner)

    # La base de datos de pruebas vive en memoria (no se comparte entre
    # hilos): la tarea «en segundo plano» se ejecuta aquí mismo.
    def run_now(function, *args, on_success=None, on_done=None, **kwargs):
        result = function(*args, **kwargs)
        if on_success:
            on_success(result)
        if on_done:
            on_done()

    view.run_task = run_now
    view.refresh()
    assert view.account_combo.count() == len(app.accounts.list_accounts())
    assert view.fields["auth"].text() == "Demostración (simulada)"
    assert view.stat_pending.value_widget.text() == "0"
    # Validar la cuenta DEMO en segundo plano.
    view._validate()
    assert any("demostración" in t.lower() for t in silent_dialogs)
    view.refresh()
    assert view.history_table.rowCount() >= 1


def test_publicacion_masiva_lista_y_controla_la_cola(qt_app, app, silent_dialogs):
    from lot_bot.integration import AdInput, EngineState
    from lot_bot.publishing.queue import FakeClock
    from lot_bot.ui.views.direct_publish import DirectPublishView
    from lot_bot.ui.widgets.workers import TaskRunner

    app.direct.engine.clock = FakeClock()
    ref = app.accounts.list_accounts()[0].internal_ref
    app.queue_manager.add_many(
        [AdInput(account_ref=ref, title=f"Mesa {n}", description="Roble", price=50 + n)
         for n in range(3)]
    )
    view = DirectPublishView(app, TaskRunner(1))
    view.refresh()
    assert view.table.rowCount() == 3
    assert view.stat_pending.value_widget.text() == "3"
    view._start()
    assert app.publication_engine.state is EngineState.RUNNING
    app.publication_engine.run_until_idle()
    view.refresh()
    assert view.stat_published.value_widget.text() == "3"
    view._pause()
    assert app.publication_engine.state is EngineState.PAUSED
    view.timer.stop()


def test_la_ventana_principal_abre_las_pantallas_nuevas(qt_app, app_with_data):
    from lot_bot.ui.main_window import MainWindow

    ventana = MainWindow(app_with_data)
    ventana.go_to_view("Conectar Wallapop")
    qt_app.processEvents()
    assert type(ventana.stack.currentWidget()).__name__ == "ConnectWallapopView"
    ventana.go_to_view("Publicación masiva")
    qt_app.processEvents()
    assert type(ventana.stack.currentWidget()).__name__ == "DirectPublishView"
    assert "Cola:" in ventana.status_label.text()
    ventana.close()


def test_conectar_wallapop_solo_ofrece_token(qt_app, app, silent_dialogs, monkeypatch):
    from lot_bot.ui.views.connect_wallapop import ConnectWallapopView
    from lot_bot.ui.widgets.workers import TaskRunner

    view = ConnectWallapopView(app, TaskRunner(1))
    assert not hasattr(view, "mode_combo")
    view.refresh()
    assert not view.mode_button.isHidden()  # en DEMO se ofrece activar el token
    view._apply_mode()
    assert app.integration_mode == "perfil"
    assert view.mode_button.isHidden()
