"""Ajustes de la aplicacion, leidos de variables de entorno y del fichero .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lot_bot.config.paths import app_dir, get_paths


def _env_file_candidates() -> list[Path]:
    """Ficheros .env a cargar, de MAYOR a menor prioridad.

    `load_dotenv(override=False)` no pisa lo ya cargado, asi que el primero
    que exista manda. El primero es el que esta junto a LOT-Bot.exe (o en la
    raiz del proyecto), que es donde la documentacion le dice al usuario que
    lo ponga.
    """
    candidates = [app_dir() / ".env", Path.cwd() / ".env"]
    try:
        candidates.append(get_paths().config / ".env")
    except Exception:  # pragma: no cover - solo en entornos muy restringidos
        pass
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def load_env_files() -> None:
    for candidate in _env_file_candidates():
        if candidate.is_file():
            load_dotenv(candidate, override=False)


class Settings(BaseSettings):
    """Configuracion tipada de LOT Bot.

    Ningun secreto se escribe jamas en el repositorio: todos los campos
    sensibles se leen de variables de entorno o del almacen cifrado local.
    """

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    # --- Modo de funcionamiento ---
    demo_mode: bool = Field(default=True, alias="LOT_BOT_DEMO_MODE")
    environment: Literal["development", "production"] = Field(
        default="development", alias="LOT_BOT_ENV"
    )
    log_level: str = Field(default="INFO", alias="LOT_BOT_LOG_LEVEL")
    language: Literal["es", "en"] = Field(default="es", alias="LOT_BOT_LANGUAGE")

    # --- Wallapop ---
    wallapop_client_id: str = Field(default="", alias="WALLAPOP_CLIENT_ID")
    wallapop_client_secret: str = Field(default="", alias="WALLAPOP_CLIENT_SECRET")
    wallapop_redirect_uri: str = Field(
        default="http://127.0.0.1:8723/callback", alias="WALLAPOP_REDIRECT_URI"
    )
    #: Perfil de acceso autorizado (autenticacion + operaciones).
    wallapop_access_profile: str = Field(default="", alias="WALLAPOP_ACCESS_PROFILE")
    #: Nombre anterior del fichero, aceptado para no romper instalaciones.
    wallapop_endpoint_map: str = Field(default="", alias="WALLAPOP_ENDPOINT_MAP")
    wallapop_scopes: str = Field(default="", alias="WALLAPOP_SCOPES")
    #: «navegador» = integración mediante navegador con sesión del usuario.
    #: Normalmente se elige en Configuración → Wallapop (se guarda en la base
    #: de datos local); esta variable solo sirve para forzarlo.
    wallapop_integration: Literal["", "perfil", "navegador"] = Field(
        default="", alias="LOT_BOT_WALLAPOP_INTEGRATION"
    )

    # --- IA ---
    ai_provider: Literal["anthropic", "rules"] = Field(default="rules", alias="LOT_BOT_AI_PROVIDER")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    ai_model: str = Field(default="claude-sonnet-5", alias="LOT_BOT_AI_MODEL")
    ai_max_tokens: int = Field(default=4096, alias="LOT_BOT_AI_MAX_TOKENS")

    # --- Seguridad ---
    master_key: str = Field(default="", alias="LOT_BOT_MASTER_KEY")

    # --- Negocio ---
    business_whatsapp: str = Field(default="", alias="LOT_BOT_BUSINESS_WHATSAPP")
    business_name: str = Field(default="", alias="LOT_BOT_BUSINESS_NAME")

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            return "INFO"
        return value

    # ------------------------------------------------------------------
    # Propiedades derivadas
    # ------------------------------------------------------------------
    @property
    def scopes(self) -> list[str]:
        return [s for s in self.wallapop_scopes.replace(",", " ").split() if s]

    @property
    def access_profile_path(self) -> Path | None:
        """Ruta del perfil de acceso autorizado.

        Acepta el nombre nuevo (WALLAPOP_ACCESS_PROFILE) y, por compatibilidad
        con instalaciones anteriores, el antiguo (WALLAPOP_ENDPOINT_MAP).
        """
        raw = self.wallapop_access_profile or self.wallapop_endpoint_map
        if not raw:
            # Sin variable: se busca el nombre estandar en config/ junto al
            # programa, para que baste con dejar el fichero ahi.
            for candidate in (
                app_dir() / "config" / "access_profile.local.yaml",
                get_paths().config / "access_profile.local.yaml",
            ):
                if candidate.is_file():
                    return candidate
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            # Relativa al programa, no a la carpeta desde la que se lanzo.
            for base in (app_dir(), Path.cwd()):
                if (base / path).is_file():
                    return base / path
            return app_dir() / path
        return path

    @property
    def endpoint_map_path(self) -> Path | None:
        """Nombre anterior de `access_profile_path`."""
        return self.access_profile_path

    @property
    def has_oauth_client_credentials(self) -> bool:
        """True si hay credenciales de cliente OAuth.

        OJO: esto NO es requisito para usar LOT Bot. Solo lo necesita el
        mecanismo OAuth. Otros mecanismos autorizados no usan client_id ni
        client_secret.
        """
        return bool(self.wallapop_client_id and self.wallapop_redirect_uri)

    @property
    def can_use_real_wallapop(self) -> bool:
        """True si hay un perfil de acceso con el que intentar conectar.

        Que el perfil este completo lo decide `build_backend`, que es quien
        sabe distinguir entre falta de autenticacion y falta de operaciones.
        """
        path = self.access_profile_path
        return path is not None and path.is_file()

    @property
    def demo_forced(self) -> bool:
        """True si LOT_BOT_DEMO_MODE=true se ha indicado expresamente."""
        return self.demo_mode and "demo_mode" in self.model_fields_set

    @property
    def effective_demo_mode(self) -> bool:
        """Modo DEMO efectivo.

        Si el usuario desactiva DEMO pero no hay perfil de acceso autorizado,
        seguimos en DEMO: nunca simulamos una conexion real.
        """
        if self.demo_mode:
            return True
        return not self.can_use_real_wallapop

    @property
    def ai_available(self) -> bool:
        if self.ai_provider == "anthropic":
            return bool(self.anthropic_api_key)
        return True

    def describe_redactions(self) -> dict[str, str]:
        """Valores que jamas deben aparecer en logs ni en pantalla."""
        secrets = {
            "wallapop_client_secret": self.wallapop_client_secret,
            "anthropic_api_key": self.anthropic_api_key,
            "master_key": self.master_key,
        }
        return {k: v for k, v in secrets.items() if v}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_env_files()
    return Settings()  # type: ignore[call-arg]


def reload_settings() -> Settings:
    """Vuelve a leer la configuracion (tras editarla desde la pantalla de ajustes)."""
    get_settings.cache_clear()
    return get_settings()
