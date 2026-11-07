# Integración por navegador, cola de publicación e imágenes FLUX.2 Pro

## Alcance y autorización

La integración por navegador existe para el **uso personal autorizado del
titular de LOT Bot**: Wallapop España le ha autorizado a publicar sus
anuncios con este bot, desde el navegador, sin API key ni OAuth y con un
mínimo de 60 segundos entre anuncios. No es una integración oficial de
Wallapop ni una autorización transferible, y la aplicación lo dice en las
pantallas de Cuentas y Configuración.

Lo que **no** hace, por diseño:

* No usa ninguna API de Wallapop ni endpoints internos: repite en la web
  pública (es.wallapop.com) los pasos que haría el usuario.
* No pide, ve ni guarda contraseñas: el usuario inicia sesión él mismo.
* No intenta resolver CAPTCHA ni verificaciones, ni ocultar que el navegador
  está automatizado. Si Wallapop pide algo, la cola se pausa y el usuario lo
  completa a mano (Cuentas → «Abrir navegador»).
* No baja nunca de 60 segundos entre publicaciones.

## Arquitectura

```
Chat «Publica 10 canapés» ─► publish_master_ad (plan + confirmación)
                                   │
                                   ▼
                     PublishQueue (publishing/queue.py)
            por anuncio: imagen única ─► espera ≥ intervalo ─► publicar
                  │                                   │
                  ▼                                   ▼
   ImageGenerationService               MasterAdService.publish_single
   (images/generation/)                          │
   ├─ FluxImageService  (real)                    ▼
   └─ DemoImageGenerator (DEMO)          WallapopService
                                          ├─ MockWallapopService (DEMO)
                                          └─ BrowserWallapopService
                                               │  ListingForm (form.py) + wallapop_browser.yaml
                                               ▼
                                     Playwright ─► Chrome/Edge (perfil por cuenta)
```

| Pieza | Fichero |
|---|---|
| Servicio de Wallapop por navegador | `lot_bot/wallapop/browser/service.py` |
| Conexión de cuentas (inicio de sesión del usuario) | `lot_bot/wallapop/browser/auth.py` |
| Perfiles aislados por cuenta | `lot_bot/wallapop/browser/profiles.py` |
| Control del navegador (Playwright) | `lot_bot/wallapop/browser/driver.py` |
| Flujo completo de publicación (un método por paso) | `lot_bot/wallapop/browser/form.py` |
| **Selectores y pasos de la web (único sitio)** | `lot_bot/resources/wallapop_browser.yaml` |
| Cola de publicación | `lot_bot/publishing/queue.py` |
| Cliente FLUX.2 Pro | `lot_bot/images/generation/flux.py` |
| Prompts | `lot_bot/images/generation/prompts.py` |
| Registro y detección de repetidas | `lot_bot/images/generation/registry.py` |
| Clave de API cifrada | `lot_bot/config/api_keys.py` |

## Selectores de la web de Wallapop

Todos los selectores, direcciones y pasos del formulario están en
`lot_bot/resources/wallapop_browser.yaml`. El código no contiene ninguno.

**Se escribieron a partir de los textos visibles de la web y no se han
podido comprobar con una sesión real** (`verificado: false`). La primera vez:

1. Conecta una cuenta y lanza una cola de **1** anuncio.
2. Mira la ventana del navegador: si un paso falla, la cola se pausa, el
   error dice qué paso y se guarda una captura en `<datos>/logs/navegador/`.
3. Copia el YAML a `<datos>/config/wallapop_browser.local.yaml` (la ruta
   exacta aparece en Configuración → Wallapop), corrige el paso y pon
   `verificado: true`. No hay que recompilar.

Cada objetivo admite varias alternativas (CSS, `text=`, `role=...`); se usa
la primera visible. Los campos están en `publicar.formulario`: `tipo_anuncio`,
`titulo`, `categoria`, `subcategoria`, `caracteristicas` (`estado`, `uso`,
`color`, `material`), `descripcion`, `precio`, `fotos` (con `miniaturas` y
`rechazada`), `publicar` y `errores`. `lectura` (opcional) indica dónde releer
un campo para la comprobación previa.

## Publicación automática completa

`ListingForm.run()` (en `form.py`) hace TODO el formulario; el usuario solo
confirma en LOT Bot:

1. `verify_session` en el perfil persistente de la cuenta (el mismo de su hilo
   en `BrowserSessionPool`, reutilizado entre anuncios).
2. `open_create_listing` → `choose_listing_type` → `fill_title` →
   `select_category` → `fill_attributes` → `fill_description` → `fill_price` →
   `upload_photos` (espera la miniatura; si no aparece o se rechaza, no sigue).
3. `verify_form`: relee título, precio, descripción, categoría,
   características, fotos y el perfil de la cuenta. Si algo no coincide
   (`FormMismatchError`) NO se pulsa «Publicar».
4. `submit_listing` → `wait_for_publish_confirmation` →
   `get_published_listing_url`. Sin confirmación de Wallapop el anuncio queda
   como **Resultado no confirmado** (estado `unconfirmed`), no se reintenta
   solo y la cola se pausa.

CAPTCHA/verificación: `ListingForm.guard` guarda captura, pausa la cola con
«Wallapop requiere una verificación. Complétala en el navegador y pulsa
Continuar.» y espera en el mismo paso con el navegador abierto; al pulsar
«Continuar» sigue desde ahí. Nunca se toca la verificación.

Cada error guarda `<fecha>-<cuenta>-<paso>.png` y `.json` (paso, URL sin
parámetros, pasos hechos, diagnóstico; nunca cookies ni tokens) en
`<datos>/logs/navegador/`.

### Por qué antes solo se abría la ventana

`PlaywrightLauncher.open` lanzaba Chrome con la carpeta de perfil de la
cuenta aunque el Chrome NORMAL de esa cuenta (abierto con «Abrir cuenta» o
durante el inicio de sesión) siguiera abierto. Chrome no admite dos procesos
con el mismo perfil: entrega la orden a la ventana existente —que muestra la
cuenta de Wallapop— y el proceso lanzado por Playwright termina. LOT Bot se
quedaba sin página que controlar, probaba el siguiente navegador (Edge) con el
perfil de Chrome, sin sesión, y el formulario nunca se rellenaba. Ahora:

* `profile_locked` (en `profiles.py`) detecta el perfil en uso ANTES de
  lanzar nada → `ProfileInUseError`: la cola pide cerrar esa ventana y pulsar
  «Continuar», y sigue.
* Cada perfil recuerda con qué navegador se creó (`lotbot-navegador.txt`) y
  solo se abre con ese.
* Si Wallapop no confirma, ya no se da por publicado.

### Prueba manual

`python scripts/prueba_publicar_canape.py --simulado` (web local que imita
Wallapop) o `--cuenta "<alias>"` (Wallapop de verdad; publica). Siempre con
el navegador visible.

## Conexión de cuentas (comprobación real)

Antes, una cuenta se daba por conectada al ver un enlace a `/app/chat`, que
Wallapop muestra también sin sesión: el navegador se cerraba al instante y la
cuenta quedaba «conectada» sin haber iniciado sesión. Ahora:

1. `LoginSession` abre el perfil persistente de la cuenta en un navegador
   visible y lo deja abierto.
2. El usuario inicia sesión y pulsa «Ya he iniciado sesión».
3. `verify_session` abre una página privada (`sesion.comprobacion` del YAML) y
   exige a la vez: que no haya redirección fuera de `/app/`, que no se vea el
   botón de acceso ni una verificación y que se vea contenido privado.
4. Solo con esa comprobación correcta `authenticate` crea la credencial (sin
   secretos: la sesión vive en el perfil) y la interfaz pide confirmación.

Probado con Chromium real contra una web local que imita ese comportamiento
(incluido el enlace `/app/chat` visible sin sesión).

## Reutilización y caducidad

`BrowserSessionPool` mantiene un navegador por cuenta, en su propio hilo,
entre publicaciones (se cierra solo tras `mantener_abierto_s`). Publicar 50
anuncios en una cuenta abre su navegador una vez. Si la sesión caduca, la cola
marca la cuenta como «Sesión caducada», deja sus anuncios pendientes y sigue
con las demás cuentas; si solo quedan anuncios de cuentas caducadas, se pausa.

## Estadísticas

`estadisticas:` en el YAML define patrones para leer visualizaciones y
favoritos del texto de la página pública del anuncio (sin verificar contra la
web real). Lo que no aparece se guarda como `None` («No disponible»). Cada
lectura se guarda en `listing_stats` (histórico). `StatsAnalyzer` compara
grupos (habitación, luz, estilo, tipo de imagen, día, franja, título,
descripción, cuenta) solo con ≥3 anuncios por grupo y ≥2 grupos, sin afirmar
causalidad; `Optimizer` recomienda y hace que 2 de cada 3 imágenes nuevas usen
las habitaciones que mejor funcionan (la tercera sigue explorando). Nunca
cambia el título, el precio ni la descripción de la plantilla única.

## Mensajes

Desactivados: no hay API de mensajes y leer el chat de la web sería frágil.
`MessageService.messaging_available` es falso con el navegador y en DEMO.

## Sesiones

* Un perfil de Chromium por cuenta en `<datos>/browser_profiles/<cuenta>/`
  (en Windows, `%LOCALAPPDATA%\LOT Bot\browser_profiles`). Cookies, caché e
  historial nunca se comparten. Las cookies las cifra el propio navegador
  (DPAPI en Windows).
* Se rechaza cualquier carpeta de perfiles dentro de un repositorio Git.
* «Desconectar» y «Eliminar cuenta» borran el perfil de esa cuenta.
* `.gitignore` bloquea además `browser_profiles/`, `Cookies`, `Login Data`,
  `storage_state*.json`, `*.har`, `*.local.yaml`.

Inicio de sesión en el navegador NORMAL (`normal.py`):

* Para «Añadir cuenta Wallapop», «Reconectar» y «Abrir cuenta» se lanza el
  Chrome o Edge instalado como un proceso normal, **sin Playwright**, con solo
  `--user-data-dir=<perfil de la cuenta> --no-first-run
  --no-default-browser-check <url>`. Es el mismo navegador que el usuario usa a
  diario: reCAPTCHA y las verificaciones se comportan como en su Chrome.
* Pasos: «Paso 1/2 — Navegador abierto», «Paso 2/2 — Inicia sesión
  manualmente en Wallapop». «Ya he iniciado sesión» NO conecta: pide cerrar la
  ventana (Chrome guarda la sesión en disco al cerrarse) y después comprueba la
  sesión con Playwright sobre ese mismo perfil.
* CAPTCHA en la comprobación: «Completa la verificación en el navegador y
  vuelve a LOT-Bot» y botón «Abrir de nuevo el navegador».
* Si no hay Chrome ni Edge instalados, se usa el navegador integrado
  (Playwright), con el mismo flujo de comprobación.
* LOT Bot no intercepta ni bloquea peticiones (ni JavaScript, imágenes,
  iframes, cookies, almacenamiento ni recursos de Google): no hay `route()`,
  `abort()` ni filtros; una prueba lo vigila.

Diagnóstico: al abrir el navegador normal se registra navegador, versión,
ruta, perfil y argumentos; si una comprobación falla, se registra la URL (sin
parámetros), si JavaScript funciona y los errores de navegación, de consola y
de red de la página. Nunca cookies, contraseñas, tokens ni «storage state».

Navegador para comprobar y publicar (`driver.py`):

* En Windows se buscan primero Chrome y Edge instalados en sus rutas
  habituales (`%ProgramFiles%`, `%ProgramFiles(x86)%`, `%LOCALAPPDATA%`), luego
  los canales de Playwright y por último su Chromium.
  `LOT_BOT_BROWSER_EXECUTABLE` fuerza una ruta.
* Se abre con `launch_persistent_context(user_data_dir=<perfil de la cuenta>)`:
  perfil propio y persistente, ventana normal (ni invitado ni incógnito).
  Un perfil nuevo se ve «vacío» (sin marcadores ni extensiones): es normal, es
  el perfil exclusivo de esa cuenta.
* **Sandbox**: Playwright añade `--no-sandbox` salvo que se pase
  `chromium_sandbox=True`. LOT Bot lo pasa siempre en Windows y macOS (en Linux
  solo se puede desactivar a propósito con `LOT_BOT_BROWSER_SANDBOX=0`, para
  contenedores de desarrollo). Nunca se añaden `--no-sandbox`,
  `--disable-setuid-sandbox`, `--no-zygote` ni `--single-process`.
* Se quitan de los argumentos por defecto de Playwright los que reducen la
  seguridad sin necesidad: `--disable-client-side-phishing-detection`,
  `--disable-popup-blocking`, `--disable-component-update` y
  `--unsafely-disable-devtools-self-xss-warnings`. No se toca nada relacionado
  con la detección de automatización.
* Si el navegador no arranca, se registra navegador, ejecutable, perfil,
  sandbox, argumentos y el error (nunca cookies, contraseñas ni tokens).
* CAPTCHA o verificación: el flujo se detiene, el navegador sigue abierto y el
  usuario la completa; después vuelve a pulsar «Ya he iniciado sesión». Si no se
  puede confirmar la sesión: «Estado de sesión no confirmado» y la cuenta no se
  conecta.

## Cola de publicación

* `minimum_publish_interval_seconds` = 60 por defecto; valores inferiores se
  rechazan (también si alguien los escribe a mano en la base de datos).
* El intervalo cuenta entre **cualquier** par de publicaciones, también de
  cuentas distintas y también tras un intento fallido. Es un mínimo: si
  generar la imagen tarda más, no se espera extra.
* Por anuncio se registra: inicio, publicación, cuenta, anuncio, resultado,
  error, intentos, imagen, id/URL de Wallapop. Todo pasa también por el
  Historial.
* Fallos: verificación, sesión caducada, límite de peticiones, sin clave o
  sin créditos → **pausa** sin reintentar. Otros errores → un reintento
  automático esperando el doble del intervalo. Dos fallos seguidos → pausa.
* Iniciar, pausar, reanudar, cancelar y reintentar desde la pantalla
  «Publicación automática» o el chat («¿cómo va la cola?», «pausa la cola»,
  «reintenta los fallidos»).
* Si LOT Bot se cierra con una cola en marcha, al abrirlo queda en pausa.
* Las pruebas usan `FakeClock`; con un servicio que no sea el DEMO, la cola
  se niega a usarlo.

## Imágenes (FLUX.2 Pro)

Flujo documentado por Black Forest Labs: `POST https://api.bfl.ai/v1/flux-2-pro`
con cabecera `x-key` → `polling_url` → consultar hasta `"Ready"` →
descargar `result.sample` (caduca a los 10 minutos). Saldo:
`GET /v1/credits`. Cualquier estado distinto de `Pending`/`Ready` es un
fallo y se muestra tal cual.

* El prompt se construye con los datos del anuncio (tipo, color, material,
  medida si la hay, colchón si la descripción lo menciona); lo que no se sabe
  clasificar se pasa literal. Solo varían encuadre, luz y escena. Siempre:
  fotorrealista, sin personas, sin texto, logos, marcas ni precios.
* Cada imagen se registra (SHA-256, dHash de 64 bits, prompt, semilla,
  fecha, anuncio, cuenta). Si sale igual o casi igual a otra, se descarta y
  se genera otra variación (hasta 3 intentos).
* La imagen generada es la portada del anuncio; las del anuncio principal van
  detrás.
* La clave se introduce en Configuración → IA / Imágenes, se guarda cifrada
  con la clave maestra del sistema y se tacha en los registros.
* En DEMO se generan imágenes de prueba locales: ni red ni créditos.
* Edición con referencias (`input_image`, `input_image_2`…, FLUX.2 [pro]):
  «Cambiar estilo», «Cambiar habitación» y «Usar como referencia» envían la
  imagen elegida y piden mantener el producto sin cambios. «Mejorar» es local
  (Pillow: hasta 2048 px, nitidez y contraste), sin IA.
* Cada imagen guarda su escena (habitación, luz, ángulo, estilo) y su imagen de
  partida; una edición idéntica a la original se descarta.

## Las cuentas no caducan

Una cuenta conectada sigue conectada hasta que el usuario la desconecta o la
elimina. Ninguna comprobación fallida, pestaña cerrada ni aviso de Wallapop
la marca como caducada (`AccountManager._mark_expired` solo registra el aviso;
`restore_expired_accounts` deshace el estado «caducada» de versiones
anteriores). La sesión vive en el perfil persistente de la cuenta:

* Si el usuario cierra la ventana, `BrowserSessionPool` reabre el MISMO perfil
  en la siguiente operación (y repite una vez la que estaba en curso).
* Si Wallapop pide entrar de nuevo, la publicación se detiene en esa misma
  ventana, el usuario inicia sesión ahí y pulsa «Continuar»; sigue sola.

## Formulario por pantallas

Tras el título, `ListingForm` pulsa el «Continuar» DEL FORMULARIO
(`publicar.formulario.continuar`). Si un campo no está en la pantalla actual,
prueba primero con «Continuar». Los selectores de categoría y características
van limitados a `main`/`form` y a nombres que empiezan por «Categoría» (no
«Categorías»): el menú «Categorías» de la cabecera es el buscador de la web y
nunca se pulsa.

## Categoría (CategoryPicker, `category.py`)

Flujo: medir el campo «Categoría» ANTES de abrirlo → marcar los textos de la
ruta que ya existen en la página (enlaces del pie, textos ocultos: nunca son
opciones) → abrir → esperar a que la lista esté visible → buscar la categoría
final dentro del panel (sugeridas) → si no, recorrer la ruta nivel a nivel
esperando a que aparezca cada nivel → pulsar la FILA de la opción → esperar a
que la lista se cierre → VERIFICAR (lista cerrada + el campo muestra la
categoría). Si no se verifica, error con captura, `.json` con los pasos y el
HTML del panel.

Selectores (`publicar.formulario.categoria` en el YAML): `objetivos` (campo),
`panel` (lista abierta) y `seleccionada` (dónde se lee lo elegido). Si el
selector del panel deja de valer, la lista abierta se reconoce por sus
opciones visibles fuera del campo.

Causa del fallo anterior: la comprobación leía el texto del componente del
desplegable; si la lista está DENTRO de ese componente, con la lista abierta
el texto incluía «Estructura de camas» y se daba por seleccionada → el bot
seguía con el selector abierto y se bloqueaba. Reproducido con la página de
prueba (`?cat=dentro`).

Diagnóstico detallado: `LOT_BOT_BROWSER_DEBUG=1` registra cada paso de la
categoría en el log y guarda también captura cuando sale bien. Sin él, solo se
guarda (en `logs/navegador`) cuando falla.
