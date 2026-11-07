"""Errores de la integracion con Wallapop.

Todos llevan un mensaje entendible para el usuario (`user_message`) y un
detalle tecnico que va al log. Nunca se ocultan los errores de Wallapop.
"""

from __future__ import annotations


class WallapopError(Exception):
    """Error base de la capa de integracion."""

    user_message = "Se ha producido un error al comunicar con Wallapop."
    retryable = False

    def __init__(self, detail: str = "", *, user_message: str | None = None) -> None:
        self.detail = detail
        if user_message:
            self.user_message = user_message
        super().__init__(detail or self.user_message)


class NotAvailableWithCurrentAccessError(WallapopError):
    """La operacion no esta disponible con el acceso autorizado actual.

    Se lanza en lugar de simular la accion. El codigo
    `NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS` se muestra tal cual en la
    interfaz y se devuelve tal cual a la IA.

    El nombre habla de «acceso» y no de «API» a proposito: el mecanismo
    autorizado puede no ser una API.
    """

    code = "NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS"
    user_message = (
        "Esta función requiere una operación o un permiso que el acceso "
        "autorizado a Wallapop no proporciona actualmente."
    )

    def __init__(self, operation: str, reason: str = "") -> None:
        self.operation = operation
        self.reason = reason
        detail = f"{self.code}: operación '{operation}' no disponible."
        if reason:
            detail += f" {reason}"
        super().__init__(detail)


#: Nombre anterior, mantenido para no romper importaciones existentes.
NotAvailableWithCurrentAPIError = NotAvailableWithCurrentAccessError


class AuthenticationError(WallapopError):
    """Token ausente, caducado o rechazado."""

    user_message = (
        "Wallapop pide volver a iniciar sesión en esta cuenta. Inicia sesión en su "
        "ventana del navegador y pulsa «Continuar» (la cuenta sigue conectada)."
    )


class AuthorizationError(WallapopError):
    """El token es valido pero no tiene el permiso (scope) necesario."""

    user_message = (
        "La cuenta no tiene permisos suficientes para esta operación. "
        "Revisa los permisos concedidos a la integración."
    )


class RateLimitError(WallapopError):
    """Wallapop ha limitado el numero de peticiones."""

    user_message = "Wallapop ha limitado temporalmente las peticiones. Vuelve a intentarlo en unos minutos."
    retryable = True

    def __init__(self, detail: str = "", retry_after: int | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(detail)


class ValidationRejectedError(WallapopError):
    """Wallapop ha rechazado los datos enviados."""

    user_message = (
        "Wallapop no ha aceptado la publicación. Revisa los campos obligatorios del anuncio."
    )

    def __init__(self, detail: str = "", fields: dict[str, str] | None = None) -> None:
        self.fields = fields or {}
        super().__init__(detail)


class NotFoundError(WallapopError):
    user_message = "El elemento solicitado ya no existe en Wallapop."


class NetworkError(WallapopError):
    user_message = "No hay conexión con Wallapop. Comprueba tu conexión a internet."
    retryable = True


class ServiceUnavailableError(WallapopError):
    user_message = "Wallapop no está disponible en este momento. Vuelve a intentarlo más tarde."
    retryable = True


class ConfigurationError(WallapopError):
    """Falta configuracion obligatoria para usar el acceso real."""

    user_message = (
        "El acceso a Wallapop no está configurado. Revisa el perfil de acceso "
        "autorizado en Configuración → Wallapop."
    )


class AccessNotConfiguredError(WallapopError):
    """No hay ningun mecanismo de acceso autorizado declarado.

    Lleva la lista exacta de lo que falta, para poder mostrarla al usuario en
    vez de un mensaje generico.
    """

    user_message = (
        "Todavía no está configurado el mecanismo de acceso autorizado a Wallapop."
    )

    def __init__(self, missing: list[str] | None = None) -> None:
        self.missing = missing or []
        detail = "Acceso no configurado."
        if self.missing:
            detail += " Falta: " + " | ".join(self.missing)
        super().__init__(detail)


class VerificationRequiredError(WallapopError):
    """Wallapop pide una verificación (CAPTCHA, código, confirmación...).

    LOT Bot NUNCA intenta resolverla: se detiene y el usuario la completa a
    mano en la ventana del navegador de esa cuenta.
    """

    user_message = (
        "Wallapop requiere una verificación. Complétala en el navegador y pulsa Continuar."
    )


class BrowserStepError(WallapopError):
    """Un paso del formulario de Wallapop no se ha podido completar.

    Suele indicar que la web de Wallapop ha cambiado: el paso y los selectores
    están en wallapop_browser.yaml.
    """

    def __init__(self, step: str, detail: str = "", screenshot: str | None = None) -> None:
        self.step = step
        self.screenshot = screenshot
        super().__init__(
            detail or f"Paso «{step}» no completado.",
            user_message=(
                f"No se ha podido completar el paso «{step}» en la web de Wallapop. "
                "Puede que la web haya cambiado: revisa wallapop_browser.yaml."
            ),
        )


class ProfileInUseError(WallapopError):
    """El perfil de la cuenta está abierto en otra ventana del navegador.

    Chrome no deja que dos procesos usen el mismo perfil: si se lanza otro,
    abre una pestaña en la ventana que ya existe y termina, así que LOT Bot
    perdería el control del navegador. Se detiene y se pide cerrarla.
    """

    user_message = (
        "La ventana de Chrome/Edge de esta cuenta ya está abierta (por ejemplo, desde "
        "«Abrir cuenta»). Ciérrala y pulsa «Continuar»: LOT Bot necesita abrir ese mismo "
        "perfil para rellenar y publicar el anuncio."
    )


class FormMismatchError(WallapopError):
    """Antes de publicar, un campo del formulario no coincide con lo previsto.

    No se pulsa «Publicar».
    """

    def __init__(self, fields: dict[str, str], screenshot: str | None = None) -> None:
        self.fields = dict(fields)
        self.screenshot = screenshot
        detalle = "; ".join(f"{k}: {v}" for k, v in self.fields.items())
        super().__init__(
            f"Formulario no coincide: {detalle}",
            user_message="NO se ha publicado: antes de pulsar «Publicar» se ha comprobado el "
            f"formulario y no coincide → {detalle}.",
        )


class ImageUploadError(WallapopError):
    """Wallapop no ha cargado la foto (rechazada o no aparece la miniatura)."""

    def __init__(self, detail: str = "", screenshot: str | None = None) -> None:
        self.screenshot = screenshot
        super().__init__(
            detail or "La foto no se ha cargado.",
            user_message="NO se ha publicado: Wallapop no ha cargado la foto del anuncio "
            f"({detail or 'no aparece la miniatura'}).",
        )


class PublishCancelledError(WallapopError):
    user_message = "Publicación cancelada por el usuario."


class WindowClosedError(PublishCancelledError):
    """El usuario cerró la ventana del navegador durante la publicación.

    No se reintenta ni se abre otra ventana: se detiene."""

    user_message = (
        "Has cerrado la ventana del navegador: la publicación se ha detenido y NO se "
        "vuelve a intentar. Si quieres, pulsa «Continuar» o pide otra publicación."
    )
