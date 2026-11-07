"""Abstraccion del mecanismo de autenticacion con Wallapop.

POR QUE EXISTE ESTE MODULO
--------------------------
LOT Bot no presupone que el acceso autorizado a Wallapop sea una API con
`client_id` y `client_secret`. Ese es UNO de los mecanismos posibles, no el
unico. Aqui se define el contrato que cumple cualquier mecanismo autorizado,
de modo que el resto de la aplicacion no dependa de ninguno en concreto.

DOS PROBLEMAS DISTINTOS
-----------------------
Conviene no confundirlos, porque se resuelven por separado:

  1. AUTENTICACION  -> "como demuestro quien soy"   (este modulo)
  2. TRANSPORTE     -> "que puedo llamar y como"    (access_profile.py)

Cambiar el mecanismo de autenticacion NO elimina la necesidad de saber que
operaciones existen. Son dos piezas de informacion independientes y ambas
tienen que venir de Wallapop.

REGLAS INNEGOCIABLES
--------------------
  * No se inventa ningun endpoint, URL ni credencial.
  * No se guarda NUNCA la contrasena del usuario.
  * No se implementa nada que evite MFA, CAPTCHA ni ningun control de
    seguridad: si Wallapop exige un paso, el usuario lo hace.
  * Si falta un dato tecnico, se dice exactamente cual falta y quien debe
    proporcionarlo. Jamas se rellena con una suposicion.
"""

from __future__ import annotations

import enum
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class AuthKind(str, enum.Enum):
    """Mecanismos de acceso que LOT Bot sabe manejar."""

    #: Datos simulados. No toca Wallapop.
    DEMO = "demo"
    #: OAuth 2.0 con codigo de autorizacion + PKCE (si Wallapop lo ofrece).
    OAUTH = "oauth"
    #: El usuario se autentica en Wallapop y el mecanismo autorizado entrega
    #: a LOT Bot una sesion de uso (nunca la contrasena).
    SESSION_HANDOFF = "session_handoff"
    #: Wallapop emite una credencial delegada por cuenta (no un client_secret
    #: global): el usuario la pega una vez y LOT Bot la guarda cifrada.
    DELEGATED_CREDENTIAL = "delegated_credential"
    #: El usuario inicia sesión él mismo en un navegador controlado por LOT
    #: Bot. La sesión queda en un perfil de navegador propio de la cuenta; LOT
    #: Bot no ve ni guarda la contraseña.
    BROWSER_SESSION = "browser_session"


#: Etiquetas legibles para la interfaz.
AUTH_KIND_LABELS: dict[AuthKind, str] = {
    AuthKind.DEMO: "Modo demostración",
    AuthKind.OAUTH: "Autorización OAuth de Wallapop",
    AuthKind.SESSION_HANDOFF: "Inicio de sesión autorizado",
    AuthKind.DELEGATED_CREDENTIAL: "Credencial delegada por cuenta",
    AuthKind.BROWSER_SESSION: "Sesión en navegador (uso personal autorizado)",
}


class RequirementSource(str, enum.Enum):
    """Quien debe proporcionar un dato que falta."""

    WALLAPOP = "wallapop"
    USUARIO = "usuario"
    INSTALACION = "instalacion"


@dataclass(slots=True)
class AuthRequirement:
    """Un dato tecnico que el mecanismo necesita para poder funcionar.

    La interfaz muestra esta lista tal cual: es la respuesta honesta a
    «¿por qué no puedo conectar todavía?».
    """

    key: str
    label: str
    description: str
    source: RequirementSource
    satisfied: bool = False
    #: Donde se configura (fichero, pantalla...).
    where: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "clave": self.key,
            "dato": self.label,
            "descripcion": self.description,
            "lo_proporciona": self.source.value,
            "cumplido": self.satisfied,
            "donde": self.where,
        }


@dataclass(slots=True)
class AuthCredential:
    """Material de acceso de UNA cuenta.

    Es opaco para el resto de la aplicacion: solo la capa de transporte lo
    aplica a la peticion, y solo `AccountManager` lo guarda (cifrado).

    NUNCA se muestra en pantalla, ni se registra en los logs, ni se pasa a la
    IA. `__repr__` esta sobrescrito para que ni siquiera un volcado accidental
    lo exponga.
    """

    kind: AuthKind
    #: Cabeceras a anadir a cada peticion (p. ej. Authorization).
    headers: dict[str, str] = field(default_factory=dict)
    #: Cookies de sesion, si el mecanismo autorizado las usa.
    cookies: dict[str, str] = field(default_factory=dict)
    #: Cuando caduca, si se sabe.
    expires_at: datetime | None = None
    #: Material para renovar sin volver a molestar al usuario.
    renewal_material: str | None = None
    #: Datos NO sensibles (identificador de usuario, ambito concedido...).
    metadata: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - proteccion, no logica
        return (
            f"<AuthCredential {self.kind.value} "
            f"headers={len(self.headers)} cookies={len(self.cookies)} "
            f"expira={self.expires_at.isoformat() if self.expires_at else 'nunca'}>"
        )

    __str__ = __repr__

    @property
    def is_empty(self) -> bool:
        return not self.headers and not self.cookies

    def is_expired(self, margin_seconds: int = 0) -> bool:
        if self.expires_at is None:
            return False
        reference = datetime.now(UTC)
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return (expires - reference).total_seconds() <= margin_seconds

    def secret_values(self) -> list[str]:
        """Valores que el filtro de logs debe tachar. No se muestran.

        Se registra tanto el valor completo de la cabecera («Bearer abc123»)
        como la parte que sigue al esquema («abc123»): si solo se registrara
        el valor completo, un log que imprimiera unicamente el token se
        escaparia del filtro.
        """
        values: list[str] = []
        for raw in (*self.headers.values(), *self.cookies.values()):
            if not raw:
                continue
            values.append(raw)
            # «Bearer abc123» -> tambien «abc123»
            if " " in raw:
                tail = raw.split(" ", 1)[1].strip()
                if tail:
                    values.append(tail)
        if self.renewal_material:
            values.append(self.renewal_material)
        return values

    # -- Serializacion para el almacen cifrado -------------------------
    def to_storage(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "headers": dict(self.headers),
            "cookies": dict(self.cookies),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "renewal_material": self.renewal_material,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_storage(cls, data: dict[str, Any]) -> AuthCredential:
        expires_raw = data.get("expires_at")
        expires_at = None
        if expires_raw:
            try:
                expires_at = datetime.fromisoformat(str(expires_raw))
            except ValueError:
                expires_at = None
        return cls(
            kind=AuthKind(data.get("kind", AuthKind.DEMO.value)),
            headers=dict(data.get("headers") or {}),
            cookies=dict(data.get("cookies") or {}),
            expires_at=expires_at,
            renewal_material=data.get("renewal_material"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class AuthOutcome:
    """Resultado de intentar autenticar una cuenta."""

    success: bool
    credential: AuthCredential | None = None
    message: str = ""
    #: Requisitos que impidieron completar el flujo.
    missing: list[AuthRequirement] = field(default_factory=list)

    @property
    def blocked_by_missing_data(self) -> bool:
        return not self.success and bool(self.missing)


class AuthMethod(ABC):
    """Contrato de un mecanismo de acceso autorizado."""

    kind: AuthKind
    display_name: str = ""
    #: Explicacion que se muestra al usuario al elegir el metodo.
    description: str = ""
    #: True si el flujo abre el navegador y requiere accion del usuario.
    interactive: bool = True

    # ------------------------------------------------------------------
    @abstractmethod
    def requirements(self) -> list[AuthRequirement]:
        """Datos que necesita este mecanismo y cuáles están ya disponibles."""

    def missing_requirements(self) -> list[AuthRequirement]:
        return [r for r in self.requirements() if not r.satisfied]

    @property
    def is_ready(self) -> bool:
        """True si se puede iniciar el flujo con lo que hay configurado."""
        return not self.missing_requirements()

    # ------------------------------------------------------------------
    @abstractmethod
    def authenticate(self, account_ref: str, **context: Any) -> AuthOutcome:
        """Inicia el flujo y devuelve la credencial obtenida.

        No debe guardar nada: de eso se encarga `AccountManager`.
        """

    def renew(self, credential: AuthCredential) -> AuthCredential | None:
        """Renueva la credencial sin molestar al usuario, si el mecanismo lo
        permite. `None` significa que hay que volver a autenticarse."""
        return None

    def revoke(self, credential: AuthCredential) -> bool:
        """Invalida la credencial en el lado de Wallapop, si se puede."""
        return False

    def describe(self) -> str:
        return self.display_name or self.kind.value

    def summary(self) -> dict[str, Any]:
        """Resumen para la interfaz. Nunca incluye material sensible."""
        return {
            "metodo": self.kind.value,
            "nombre": self.describe(),
            "descripcion": self.description,
            "listo": self.is_ready,
            "requisitos": [r.to_dict() for r in self.requirements()],
        }
