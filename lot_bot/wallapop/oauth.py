"""Flujo OAuth 2.0 (codigo de autorizacion + PKCE), si Wallapop lo autoriza.

Las URLs concretas NO estan en el codigo: se leen del mapa de endpoints
oficial. Este modulo solo implementa el flujo estandar RFC 6749 / RFC 7636.

NUNCA se pide ni se guarda la contrasena de Wallapop: el usuario se autentica
en el navegador, en el dominio de Wallapop, y la aplicacion solo recibe un
codigo de autorizacion que canjea por tokens.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import logging
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

from lot_bot.logs.redaction import register_secret
from lot_bot.wallapop.dto import OAuthTokens
from lot_bot.wallapop.endpoint_map import OAuthConfig
from lot_bot.wallapop.errors import (
    AuthenticationError,
    ConfigurationError,
    NetworkError,
)

logger = logging.getLogger(__name__)


def generate_pkce_pair() -> tuple[str, str]:
    """Devuelve (code_verifier, code_challenge) segun RFC 7636 (S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


@dataclass(slots=True)
class OAuthClient:
    """Cliente OAuth generico configurado desde el mapa de endpoints."""

    config: OAuthConfig
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: list[str]

    def __post_init__(self) -> None:
        if self.client_secret:
            register_secret(self.client_secret)

    def _ensure_configured(self) -> None:
        if not self.config.is_configured:
            raise ConfigurationError(
                "authorize_url/token_url no definidos en el mapa de endpoints.",
                user_message=(
                    "El flujo de autorizacion de Wallapop no esta configurado. "
                    "Completa el bloque 'oauth' del fichero de endpoints oficial."
                ),
            )
        if not self.client_id:
            raise ConfigurationError(
                "WALLAPOP_CLIENT_ID vacio.",
                user_message="Falta el identificador de cliente de Wallapop (WALLAPOP_CLIENT_ID).",
            )

    def build_authorization_url(self, state: str, code_challenge: str | None) -> str:
        self._ensure_configured()
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
        }
        scopes = self.scopes or self.config.scopes
        if scopes:
            params["scope"] = " ".join(scopes)
        if self.config.audience:
            params["audience"] = self.config.audience
        if self.config.use_pkce and code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        params.update(self.config.extra_authorize_params)
        separator = "&" if "?" in self.config.authorize_url else "?"
        return f"{self.config.authorize_url}{separator}{urllib.parse.urlencode(params)}"

    # ------------------------------------------------------------------
    def exchange_code(self, code: str, code_verifier: str | None) -> OAuthTokens:
        self._ensure_configured()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        if self.config.use_pkce and code_verifier:
            data["code_verifier"] = code_verifier
        return self._token_request(data)

    def refresh(self, refresh_token: str) -> OAuthTokens:
        self._ensure_configured()
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        return self._token_request(data)

    def revoke(self, token: str) -> bool:
        if not self.config.revoke_url:
            return False
        try:
            response = httpx.post(
                self.config.revoke_url,
                data={"token": token, "client_id": self.client_id},
                timeout=15.0,
            )
            return response.status_code < 400
        except httpx.HTTPError as exc:
            logger.warning("No se ha podido revocar el token: %s", type(exc).__name__)
            return False

    def _token_request(self, data: dict[str, str]) -> OAuthTokens:
        try:
            response = httpx.post(
                self.config.token_url,
                data=data,
                headers={"Accept": "application/json"},
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise NetworkError(f"Error de red al pedir el token: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            # El cuerpo puede contener el motivo; se registra redactado.
            raise AuthenticationError(
                f"El servidor de autorizacion ha respondido {response.status_code}.",
                user_message=(
                    "Wallapop ha rechazado la autorizacion. "
                    "Comprueba el client id, el secreto y la URI de redireccion."
                ),
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise AuthenticationError("Respuesta de token no valida (no es JSON).") from exc

        access_token = payload.get("access_token")
        if not access_token:
            raise AuthenticationError("La respuesta no incluye 'access_token'.")
        register_secret(access_token)

        refresh_token = payload.get("refresh_token")
        if refresh_token:
            register_secret(refresh_token)

        expires_at = None
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float)):
            expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))

        scope_raw = payload.get("scope") or ""
        scopes = scope_raw.split() if isinstance(scope_raw, str) else list(scope_raw)

        return OAuthTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scopes=scopes,
            token_type=payload.get("token_type", "Bearer"),
        )


# ---------------------------------------------------------------------------
# Servidor local que recibe la redireccion del navegador
# ---------------------------------------------------------------------------
_SUCCESS_HTML = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>LOT Bot</title><style>
body{font-family:Segoe UI,system-ui,sans-serif;background:#0f1216;color:#e8edf2;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.card{background:#171c23;border:1px solid #232a33;border-radius:14px;padding:40px 48px;text-align:center}
h1{margin:0 0 8px;font-size:20px}p{margin:0;color:#9aa7b4}
</style></head><body><div class="card"><h1>Cuenta autorizada correctamente</h1>
<p>Ya puedes cerrar esta pestana y volver a LOT Bot.</p></div></body></html>"""

_ERROR_HTML = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>LOT Bot</title></head><body style="font-family:sans-serif">
<h1>No se ha podido completar la autorizacion</h1>
<p>Vuelve a LOT Bot para ver el detalle del error.</p></body></html>"""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 - firma impuesta por la libreria
        parsed = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        type(self).result.update(params)
        body = _SUCCESS_HTML if "code" in params else _ERROR_HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_POST(self) -> None:  # noqa: N802 - firma impuesta por la libreria
        """Algunos flujos devuelven los datos por POST en vez de por URL."""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        params = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
        params.update(
            {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
        )
        type(self).result.update(params)
        body = _SUCCESS_HTML if params else _ERROR_HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *_args) -> None:  # silencia el log por defecto
        return


@dataclass(slots=True)
class AuthorizationResult:
    """Lo que devuelve el flujo de autorizacion al volver al programa.

    `params` contiene todos los campos recibidos, para que los mecanismos
    distintos de OAuth (p. ej. una sesion autorizada) puedan leer los suyos.
    """

    code: str | None
    state: str | None
    error: str | None = None
    params: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str | None:
        return self.params.get(name)


class LocalCallbackServer:
    """Escucha en la redirect_uri local hasta recibir el codigo de autorizacion."""

    def __init__(self, redirect_uri: str) -> None:
        parsed = urllib.parse.urlparse(redirect_uri)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 8723
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> LocalCallbackServer:
        handler = type("_Handler", (_CallbackHandler,), {"result": {}})
        self._handler = handler
        self._server = http.server.HTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def wait(self, timeout: float = 300.0) -> AuthorizationResult:
        deadline = threading.Event()
        waited = 0.0
        step = 0.25
        while waited < timeout:
            result = self._handler.result
            if result:
                return AuthorizationResult(
                    code=result.get("code"),
                    state=result.get("state"),
                    error=result.get("error") or result.get("error_description"),
                    params=dict(result),
                )
            deadline.wait(step)
            waited += step
        return AuthorizationResult(code=None, state=None, error="timeout", params={})

    def __exit__(self, *_exc) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


def open_browser(url: str) -> bool:
    """Abre el navegador del sistema en la URL de autorizacion."""
    try:
        return webbrowser.open(url)
    except Exception as exc:  # pragma: no cover - depende del escritorio
        logger.warning("No se ha podido abrir el navegador: %s", exc)
        return False


def new_state() -> str:
    return secrets.token_urlsafe(24)
