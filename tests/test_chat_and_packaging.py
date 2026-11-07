"""Pruebas del chat en español natural y del empaquetado para Windows."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lot_bot.ai.rule_provider import RuleBasedProvider


def interpretar(frase: str):
    respuesta = RuleBasedProvider().complete("", [{"role": "user", "content": frase}], [])
    return respuesta.tool_calls[0] if respuesta.tool_calls else None


# ---------------------------------------------------------------------------
# El asistente reconoce las expresiones naturales
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "frase, herramienta",
    [
        ("Sube el canapé", "publish_master_ad"),
        ("Publica el anuncio de canapé", "publish_master_ad"),
        ("Crea anuncios de canapés", "publish_master_ad"),
        ("Crea un anuncio para este canapé.", "publish_master_ad"),
        ("Publica este anuncio.", "publish_master_ad"),
        ("Prepara el anuncio de canapé", "preview_master_ad"),
        ("Muéstrame el anuncio de canapé", "get_master_ad"),
        ("Muéstrame los anuncios de la cuenta 1.", "search_listings"),
        ("¿Qué mensajes nuevos hay?", "get_messages"),
        ("Prepara una respuesta para este cliente.", "prepare_message_response"),
        ("Genera una descripción mejor.", "generate_description"),
        ("Busca duplicados.", "detect_duplicates"),
        ("Cambia el precio a 270 €.", "update_price"),
        ("Para este anuncio pon el precio a 12 €", "update_price"),
        ("Actualiza la plantilla: precio a 12 €", "update_master_ad"),
    ],
)
def test_frases_naturales(frase, herramienta):
    llamada = interpretar(frase)
    assert llamada is not None, f"No se ha entendido: {frase}"
    assert llamada.name == herramienta


def test_publica_n_canapes_extrae_el_numero():
    llamada = interpretar("Publica 10 canapés")
    assert llamada.name == "publish_master_ad"
    assert llamada.arguments["copias"] == 10


def test_el_numero_de_copias_no_se_confunde_con_la_medida():
    llamada = interpretar("Publica el canapé de 135x190")
    assert "copias" not in llamada.arguments


def test_la_cuenta_se_extrae_de_la_frase():
    assert interpretar("Muéstrame los anuncios de la cuenta 1").arguments["cuentas"] == ["Cuenta 1"]


def test_prepara_respuesta_no_se_confunde_con_preparar_anuncio():
    assert interpretar("Prepara una respuesta para este cliente").name == "prepare_message_response"
    assert interpretar("Prepara el anuncio de canapé").name == "preview_master_ad"


def test_actualizar_la_plantilla_solo_si_se_dice_expresamente():
    """«Pon el precio a 12 €» cambia anuncios, NUNCA la plantilla."""
    assert interpretar("Para este anuncio pon el precio a 12 €").name == "update_price"
    llamada = interpretar("Actualiza la plantilla: precio a 12 €")
    assert llamada.name == "update_master_ad"
    assert llamada.arguments["cambios"] == {"precio": 12.0}


# ---------------------------------------------------------------------------
# Conversaciones completas con el agente
# ---------------------------------------------------------------------------
def test_cambiar_precio_sin_decir_de_que_no_cambia_todo(app_with_data):
    """Fallo corregido: antes cambiaba TODOS los anuncios."""
    respuesta = app_with_data.agent.ask("Cambia el precio a 270 €")
    assert not respuesta.needs_confirmation
    assert "¿De qué anuncios" in respuesta.text() or any(
        "De qué anuncios" in m.text for m in respuesta.messages
    )


def test_sube_el_canape_pide_confirmacion_y_publica(app_with_data):
    agente = app_with_data.agent
    respuesta = agente.ask("Sube el canapé")
    assert respuesta.needs_confirmation
    plan = respuesta.pending.request
    assert "Canapés — Anuncio principal" in plan.title
    assert any("MODO DEMO" in linea for linea in plan.lines)
    assert app_with_data.master_ads.publications() == []  # nada antes de confirmar

    agente.confirm(plan.token)
    # Se publica mediante la cola (reloj de pruebas: no espera 60 s de verdad).
    assert app_with_data.master_ads.publications() == []
    app_with_data.publish_queue.run_until_idle()
    assert len(app_with_data.master_ads.publications()) == len(
        app_with_data.accounts.list_accounts()
    )


def test_este_anuncio_ambiguo_pregunta_cual(app_with_data):
    agente = app_with_data.agent
    agente.confirm(agente.ask("Publica 3 canapés").pending.token)
    app_with_data.publish_queue.run_until_idle()
    respuesta = agente.ask("Para este anuncio pon el precio a 12 €")
    assert not respuesta.needs_confirmation
    assert "¿Cuál?" in " ".join(m.text for m in respuesta.messages)


def test_cambiar_un_anuncio_concreto_con_confirmacion_clara(app_with_data):
    agente = app_with_data.agent
    agente.confirm(agente.ask("Publica 3 canapés").pending.token)
    app_with_data.publish_queue.run_until_idle()
    elegido = agente.focus["listing_ids"][1]

    respuesta = agente.ask(f"Cambia el precio del anuncio {elegido} a 12 €")
    titulo = respuesta.pending.request.title
    assert "de 11,44 € a 12,00 €" in titulo

    agente.confirm(respuesta.pending.token)
    precios = {p.id: p.price for p in app_with_data.master_ads.publications()}
    assert precios[elegido] == 12.0
    assert sorted(v for k, v in precios.items() if k != elegido) == [11.44, 11.44]
    assert app_with_data.master_ads.get().price == 11.44


def test_actualizar_la_plantilla_desde_el_chat(app_with_data):
    agente = app_with_data.agent
    respuesta = agente.ask("Actualiza la plantilla: precio a 12 €")
    assert respuesta.needs_confirmation
    assert app_with_data.master_ads.get().price == 11.44
    agente.confirm(respuesta.pending.token)
    assert app_with_data.master_ads.get().price == 12.0


def test_cambiar_la_oferta_de_una_medida(app_with_data):
    app_with_data.master_ads.update(None, {"locked": False}, confirmed=True)
    agente = app_with_data.agent
    respuesta = agente.ask("Cambia el precio de los canapés de 135x190 a 275 €")
    assert any("Oferta" in linea and "135x190" in linea for linea in respuesta.pending.request.lines)
    agente.confirm(respuesta.pending.token)
    assert "135x190 → 275€" in app_with_data.master_ads.get().description


def test_con_la_plantilla_unica_la_oferta_no_cambia_sola(app_with_data):
    antes = app_with_data.master_ads.get().description
    respuesta = app_with_data.agent.ask("Cambia el precio de los canapés de 135x190 a 275 €")
    if respuesta.needs_confirmation:
        assert any("plantilla única" in linea for linea in respuesta.pending.request.lines)
        app_with_data.agent.confirm(respuesta.pending.token)
    else:
        assert "plantilla única" in " ".join(m.text for m in respuesta.messages)
    assert app_with_data.master_ads.get().description == antes


def test_generar_descripcion_no_modifica_la_plantilla(app_with_data):
    antes = app_with_data.master_ads.get().description
    respuesta = app_with_data.agent.ask("Genera una descripción mejor")
    assert not respuesta.needs_confirmation
    assert app_with_data.master_ads.get().description == antes


def test_respuesta_para_este_cliente_con_mensajes_desactivados(app_with_data):
    respuesta = app_with_data.agent.ask("Prepara una respuesta para este cliente")
    texto = " ".join(m.text for m in respuesta.messages)
    assert "desactivados" in texto
    assert not respuesta.needs_confirmation


def test_anuncios_de_una_cuenta(app_with_data):
    agente = app_with_data.agent
    agente.ask("Muéstrame los anuncios de la cuenta 1")
    herramientas = [r for r in agente.history() if r.get("role") == "user"]
    assert herramientas  # la orden llegó a ejecutarse
    anuncios = app_with_data.listings.search(
        __import__("lot_bot.publishing.listings", fromlist=["ListingFilter"]).ListingFilter(
            account_ref="demo-1"
        )
    )
    assert all(v.account_ref == "demo-1" for v in anuncios)


# ---------------------------------------------------------------------------
# Empaquetado para Windows
# ---------------------------------------------------------------------------
def test_en_el_exe_los_recursos_se_buscan_donde_los_deja_la_receta(monkeypatch, tmp_path):
    """Fallo corregido: se buscaban en _MEIPASS/resources."""
    from lot_bot.config import paths

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.resource_root() == tmp_path / "lot_bot"
    assert paths.build_paths(tmp_path / "datos").resources == tmp_path / "lot_bot" / "resources"


def test_la_receta_empaqueta_los_recursos_en_esa_misma_ruta():
    receta = (Path(__file__).resolve().parents[1] / "build" / "LOT-Bot.spec").read_text(encoding="utf-8")
    assert '"lot_bot/resources"' in receta
    assert "access_profile.example.yaml" in receta
    assert 'copy_metadata(package)' in receta


def test_el_env_junto_al_exe_se_lee(monkeypatch, tmp_path):
    """Fallo corregido: el .env junto a LOT-Bot.exe no se leía nunca."""
    from lot_bot.config import settings as ajustes

    exe = tmp_path / "LOT-Bot" / "LOT-Bot.exe"
    exe.parent.mkdir()
    exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    candidatos = ajustes._env_file_candidates()
    assert candidatos[0] == exe.parent / ".env"


def test_el_perfil_de_acceso_se_detecta_junto_al_programa(monkeypatch, tmp_path, temp_paths):
    from lot_bot.config.settings import Settings

    exe = tmp_path / "LOT-Bot" / "LOT-Bot.exe"
    (exe.parent / "config").mkdir(parents=True)
    exe.write_text("")
    perfil = exe.parent / "config" / "access_profile.local.yaml"
    perfil.write_text("auth: {}\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert Settings(LOT_BOT_DEMO_MODE=False).access_profile_path == perfil
    # Y una ruta relativa se resuelve respecto al programa
    relativa = Settings(WALLAPOP_ACCESS_PROFILE="config/access_profile.local.yaml")
    assert relativa.access_profile_path == perfil


def test_el_icono_existe_en_el_repositorio():
    raiz = Path(__file__).resolve().parents[1]
    assert (raiz / "lot_bot" / "resources" / "lot_bot.ico").is_file()


def test_el_script_de_windows_genera_el_exe_esperado():
    script = (Path(__file__).resolve().parents[1] / "build" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "LOT-Bot.spec" in script
    assert 'dist\\LOT-Bot' in script or "dist\\\\LOT-Bot" in script
    assert "access_profile.example.yaml" in script
