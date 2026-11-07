"""Integración con Wallapop mediante un navegador controlado por LOT Bot.

Uso personal autorizado: el usuario inicia sesión él mismo, cada cuenta
tiene su propio perfil de navegador y la web se describe en
`resources/wallapop_browser.yaml`.
"""

from lot_bot.wallapop.browser.auth import (
    AUTHORIZED_USE_NOTE,
    BrowserSessionAuthMethod,
    LoginSession,
)
from lot_bot.wallapop.browser.config import BrowserSiteConfig, load_site_config
from lot_bot.wallapop.browser.driver import (
    BrowserLauncher,
    BrowserPage,
    BrowserUnavailable,
    PlaywrightLauncher,
)
from lot_bot.wallapop.browser.pool import BrowserSessionPool
from lot_bot.wallapop.browser.profiles import BrowserProfileStore, UnsafeProfileLocation
from lot_bot.wallapop.browser.service import BrowserWallapopService, SessionCheck

__all__ = [
    "AUTHORIZED_USE_NOTE",
    "BrowserSessionAuthMethod",
    "LoginSession",
    "SessionCheck",
    "BrowserSessionPool",
    "BrowserSiteConfig",
    "load_site_config",
    "BrowserLauncher",
    "BrowserPage",
    "BrowserUnavailable",
    "PlaywrightLauncher",
    "BrowserProfileStore",
    "UnsafeProfileLocation",
    "BrowserWallapopService",
]
