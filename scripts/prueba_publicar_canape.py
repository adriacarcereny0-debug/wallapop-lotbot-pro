"""Prueba real controlada: «Publica 1 canapé», con el navegador VISIBLE.

Ejecuta el mismo camino que el asistente de LOT Bot: orden → plan →
confirmación → cola → navegador de la cuenta → formulario completo →
publicar → confirmación de Wallapop → URL/ID guardados.

Uso (desde la carpeta del proyecto, con el entorno de LOT Bot activado):

    # Contra la página LOCAL que imita Wallapop (no publica nada de verdad):
    python scripts/prueba_publicar_canape.py --simulado

    # Contra Wallapop, con tu cuenta ya conectada en LOT Bot (PUBLICA DE VERDAD):
    python scripts/prueba_publicar_canape.py --cuenta "Cuenta Comercial 1"

Nunca en modo oculto (headless). Si Wallapop pide una verificación, se
completa a mano en la ventana y se pulsa Enter aquí («Continuar»).
Los errores dejan captura y contexto en la carpeta logs/navegador.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _console_continue(app, stop: threading.Event) -> None:
    """Si la cola se pausa pidiendo algo al usuario, «Continuar» = Enter."""
    cola = app.publish_queue
    avisado = None
    while not stop.is_set():
        progreso = cola.progress()
        if progreso and progreso.status == "paused" and progreso.pause_reason != avisado:
            avisado = progreso.pause_reason
            print(f"\n⏸  {progreso.pause_reason}")
            input("   Cuando lo hayas hecho en el navegador, pulsa Enter para Continuar… ")
            cola.resume(progreso.job_id)
        time.sleep(0.5)


def _prepare_simulated(app):
    """Cuenta de prueba + web local que imita Wallapop + una foto."""
    from PIL import Image

    from lot_bot.wallapop.auth.base import AuthCredential, AuthKind
    from tests.wallapop_simulado import SimulatedWallapop

    web = SimulatedWallapop().__enter__()
    service = app.wallapop
    service.site = web.site(service.site)
    modo = os.environ.get("LOT_BOT_SIMULADO_CATEGORIAS")  # p. ej. «dentro-arbol»
    if modo:
        service.site.urls["subir"] += f"?cat={modo}"
    service.site.visible = True
    cuenta = app.accounts.add_account("Cuenta de prueba")
    service.profiles.profile_dir(cuenta.internal_ref).joinpath("Local State").write_text("{}")
    app.accounts.store_credential(
        cuenta.internal_ref,
        AuthCredential(kind=AuthKind.BROWSER_SESSION, metadata={"account_ref": cuenta.internal_ref}),
    )
    foto = Path(tempfile.mkdtemp()) / "canape.jpg"
    Image.new("RGB", (1200, 900), (150, 150, 155)).save(foto)
    app.master_ads.add_images(
        None, [{"path": str(foto), "file_format": "JPEG", "width": 1200, "height": 900}]
    )
    app.publish_queue.save_settings(generate_images=False)
    return web, cuenta.alias


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--simulado", action="store_true", help="página local que imita Wallapop")
    group.add_argument("--cuenta", help="alias de la cuenta de Wallapop conectada en LOT Bot")
    parser.add_argument(
        "--diagnostico", action="store_true",
        help="registra cada paso de la categoría y guarda capturas en logs/navegador",
    )
    args = parser.parse_args()
    if args.diagnostico:
        os.environ["LOT_BOT_BROWSER_DEBUG"] = "1"

    if args.simulado:
        os.environ["LOT_BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="lotbot-prueba-")
    from lot_bot.bootstrap import create_application

    app = create_application(start_scheduler=False)
    web = None
    try:
        app.set_integration_mode("navegador")
        if args.simulado:
            web, alias = _prepare_simulated(app)
        else:
            alias = args.cuenta
        app.wallapop.site.visible = True  # primera prueba real: siempre visible

        orden = f"Publica 1 canapé en la cuenta {alias}"
        print(f"» {orden}")
        respuesta = app.agent.ask(orden)
        for mensaje in respuesta.messages:
            print(mensaje.text)
        if not respuesta.needs_confirmation:
            print("No se ha preparado la publicación (mira el mensaje de arriba).")
            return 1
        plan = respuesta.pending.request
        print(f"\n{plan.title}")
        for linea in plan.lines:
            print(f"  · {linea}")
        if input("\n¿Confirmas la publicación? (s/n) ").strip().lower() not in ("s", "si", "sí"):
            print("Cancelado. No se ha publicado nada.")
            return 0
        app.agent.confirm(plan.token)

        stop = threading.Event()
        threading.Thread(target=_console_continue, args=(app, stop), daemon=True).start()
        app.publish_queue.run_until_idle()
        stop.set()

        progreso = app.publish_queue.progress()
        for tarea in progreso.tasks:
            print(f"\nResultado: {tarea['estado']}")
            print(f"  URL: {tarea['url'] or '—'}")
            print(f"  ID:  {tarea['id_wallapop'] or '—'}")
            if tarea["error"]:
                print(f"  Detalle: {tarea['error']}")
        if web is not None:
            print(f"\n(Simulado) Recibido por la web local: {web.published}")
        return 0 if progreso.published == progreso.total else 2
    finally:
        app.shutdown()
        if web is not None:
            web.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
