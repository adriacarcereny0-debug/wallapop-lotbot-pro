"""Servidor LOCAL que imita el formulario «Subir producto» de Wallapop.

Solo para pruebas: sirve `tests/fixtures/wallapop_simulado` en 127.0.0.1 y
guarda lo que «se publica». No se conecta a Wallapop ni a internet.
"""

from __future__ import annotations

import copy
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "fixtures" / "wallapop_simulado"

#: Página de un anuncio propio: «Eliminar» está dentro del menú «Más
#: opciones»; pide el motivo y luego confirmar.
_ITEM_PAGE = """<!doctype html><meta charset=utf-8><title>{slug}</title>
<h1>{titulo}</h1><p class=precio>{precio} €</p>
<a href='/item/{slug}'>Ver anuncio</a>
<a href='/app/catalog/edit/{slug}'>Editar</a>
<button id=mas aria-label="Más opciones">…</button>
<div id=menu hidden><button id=eliminar>Eliminar</button></div>
<div role=dialog id=motivo hidden><p>¿Por qué lo eliminas?</p>
  <label><input type=radio name=m> Lo he vendido en Wallapop</label>
  <label id=yano><input type=radio name=m> Ya no lo vendo</label>
  <button id=ok disabled>Eliminar</button></div>
<script>
mas.onclick = () => { menu.hidden = false; };
eliminar.onclick = () => { motivo.hidden = false; };
yano.onclick = () => { ok.disabled = false; };
ok.onclick = async () => {
  await fetch('/borrar/{slug}', {method: 'POST', body: '{}'});
  document.body.innerHTML = '<h1>Has eliminado el anuncio</h1>';
};
</script>"""

#: «Editar»: los mismos campos que el formulario de subir (título y precio).
_EDIT_PAGE = """<!doctype html><meta charset=utf-8><title>Editar</title>
<label for=t>Título</label><input id=t name=title value="{titulo}">
<label for=p>Precio</label><input id=p name=sale_price type=number step=0.01 value="{precio}">
<button id=g>Guardar cambios</button>
<script>
g.onclick = async () => {
  await fetch('/editar/{slug}', {method: 'POST',
    body: JSON.stringify({titulo: t.value, precio: p.value})});
  location.href = '/item/{slug}';
};
</script>"""


class SimulatedWallapop:
    def __init__(self) -> None:
        self.published: list[dict] = []
        self.visited: list[str] = []
        self.deleted: set[str] = set()
        #: slug → {"titulo", "precio"} de cada anuncio publicado.
        self.items: dict[str, dict] = {}
        self.edits: list[dict] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silencio
                pass

            def _send(self, code: int, body: bytes, kind: str = "text/html; charset=utf-8"):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                server.visited.append(path)
                if path == "/app/catalog/upload":
                    self._send(200, (ROOT / "app/catalog/upload.html").read_bytes())
                elif path == "/app/catalog/published":
                    # «Tus productos»: una ficha por anuncio a la venta.
                    cards = "".join(
                        f"<a href='/item/{slug}'><div><p>{d['titulo']}</p>"
                        f"<p>{d['precio']} €</p></div></a>"
                        for slug, d in server.items.items() if slug not in server.deleted
                    )
                    self._send(200, f"<!doctype html><meta charset=utf-8><h1>Tus productos</h1>{cards}".encode())
                elif path.startswith("/app/catalog/edit/"):
                    slug = path.rsplit("/", 1)[-1]
                    d = server.items[slug]
                    self._send(
                        200,
                        _EDIT_PAGE.replace("{slug}", slug).replace("{titulo}", d["titulo"])
                        .replace("{precio}", str(d["precio"])).encode(),
                    )
                elif path.startswith("/item/"):
                    slug = path.rsplit("/", 1)[-1]
                    if slug in server.deleted:
                        self._send(
                            404,
                            b"<!doctype html><meta charset=utf-8>"
                            b"<h1>Este anuncio ya no est\xc3\xa1 disponible</h1>",
                        )
                        return
                    d = server.items.get(slug, {"titulo": slug, "precio": ""})
                    self._send(
                        200,
                        _ITEM_PAGE.replace("{slug}", slug).replace("{titulo}", d["titulo"])
                        .replace("{precio}", str(d["precio"])).encode(),
                    )
                else:
                    self._send(200, b"<!doctype html><meta charset=utf-8><h1>Inicio (simulado)</h1>")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if self.path.startswith("/borrar/"):
                    self.rfile.read(length)
                    server.deleted.add(self.path.rsplit("/", 1)[-1])
                    self._send(200, b"{}", "application/json")
                    return
                if self.path.startswith("/editar/"):
                    cambios = json.loads(self.rfile.read(length) or b"{}")
                    server.items[self.path.rsplit("/", 1)[-1]].update(cambios)
                    server.edits.append(cambios)
                    self._send(200, b"{}", "application/json")
                    return
                data = json.loads(self.rfile.read(length) or b"{}")
                server.published.append(data)
                item_id = f"canape-canape-{700 + len(server.published)}"
                server.items[item_id] = {"titulo": data.get("titulo", ""), "precio": data.get("precio", "")}
                self._send(200, json.dumps({"id": item_id}).encode(), "application/json")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self) -> SimulatedWallapop:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def site(self, base_site):
        """Copia de la configuración real apuntando a este servidor local."""
        site = copy.deepcopy(base_site)
        site.urls = dict(site.urls)
        site.urls["inicio"] = self.base + "/"
        site.urls["subir"] = self.base + "/app/catalog/upload"
        site.urls["anuncio_regex"] = re.escape(self.base) + r"/item/[A-Za-z0-9\-_%]+"
        site.urls["mis_anuncios"] = self.base + "/app/catalog/published"
        site.check_wait_ms = 300
        return site
