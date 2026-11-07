# Solución de problemas

## Dónde mirar primero

1. Pantalla **Logs y errores** dentro de la aplicación.
2. Fichero de registro:
   * Windows: `%LOCALAPPDATA%\LOT Bot\logs\lot_bot.log`
   * Linux: `~/.local/share/lot-bot/logs/lot_bot.log`
3. Pantalla **Historial de acciones**: qué se intentó, cuándo y con qué resultado.

Los registros **nunca contienen tokens ni claves**: se pueden enviar a soporte tal cual.

## La aplicación no arranca

| Síntoma | Causa | Solución |
|---|---|---|
| Se cierra sin decir nada | Falta una librería de Qt | Linux: `apt-get install libegl1 libgl1 libxkbcommon0`. Windows: usa el `.exe` compilado |
| «No se ha podido iniciar» | Base de datos corrupta o sin permisos | Renombra `lot_bot.db`; se crea una nueva |
| Tarda mucho la primera vez | Se está creando el esquema y los datos DEMO | Normal, solo la primera vez |

## Sigue en MODO DEMO aunque he puesto DEMO_MODE=false

Es intencionado: LOT Bot no finge una conexión real. Ve a **Configuración → Wallapop**;
ahí se explica exactamente qué falta. Suele ser:

* Falta `WALLAPOP_CLIENT_ID`, `WALLAPOP_CLIENT_SECRET` o `WALLAPOP_REDIRECT_URI`.
* `WALLAPOP_ENDPOINT_MAP` apunta a un fichero que no existe.
* El fichero existe pero no declara `api.base_url` ni ninguna operación.

## NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS

Esa operación **no está declarada** en el fichero de endpoints, o no está concedida.
No es un fallo del programa: es la respuesta honesta.

Solución: si Wallapop te ha concedido ese endpoint, decláralo en
`endpoint_map.local.yaml` (ver `conexion-wallapop.md`). Si no, la función no está
disponible y así se lo dirá la aplicación al usuario.

## Errores al conectar una cuenta

| Mensaje | Causa | Solución |
|---|---|---|
| «No se ha completado la autorización» | El usuario cerró el navegador o tardó más de 5 minutos | Reintentar |
| «La respuesta de autorización no es válida» | El parámetro `state` no coincide (posible intento de manipulación) | Reintentar desde la aplicación |
| «Wallapop ha rechazado la autorización» | `client_id`, `client_secret` o `redirect_uri` incorrectos | Revisar el `.env`; la URI debe coincidir **exactamente** con la registrada |
| El navegador no se abre | Entorno sin navegador por defecto | Copiar la URL del log y abrirla a mano |

## «La sesión de la cuenta ha caducado»

El token expiró y no hay *refresh token*, o el refresco fue rechazado. Vuelve a
conectar la cuenta. Si pasa constantemente, comprueba que la integración concede
*refresh tokens*.

## «No se ha podido descifrar un token guardado»

La clave maestra ha cambiado (equipo distinto, llavero reiniciado, `LOT_BOT_MASTER_KEY`
modificada). Los tokens antiguos ya no sirven: desconecta y vuelve a conectar las
cuentas. Es el comportamiento seguro esperado.

## Errores de Wallapop al publicar

| Error | Significado | Qué hacer |
|---|---|---|
| `ValidationRejectedError` (400/422) | Wallapop rechaza los datos | Revisa los campos obligatorios; el detalle está en Logs |
| `AuthorizationError` (403) | Falta un permiso | Revisa los *scopes* concedidos |
| `RateLimitError` (429) | Demasiadas peticiones | Espera y baja la frecuencia de las automatizaciones |
| `ServiceUnavailableError` (5xx) | Problema en Wallapop | Reintenta más tarde |
| `NetworkError` | Sin conexión | Revisa internet, proxy o cortafuegos |

## Los anuncios llegan incompletos al sincronizar

El `response.fields` del fichero de endpoints no apunta a los campos correctos.
Compara la respuesta real (nivel de log `DEBUG`) con lo declarado en el YAML.

## El asistente no entiende lo que escribo

Si estás en modo **Órdenes directas** solo entiende instrucciones concretas. Pulsa uno
de los ejemplos del chat para ver el formato. Para lenguaje libre, configura
`ANTHROPIC_API_KEY` y pon `LOT_BOT_AI_PROVIDER=anthropic`.

## El asistente responde «No dispongo de esa información»

Es correcto: ese dato no está configurado y el asistente tiene prohibido inventarlo.
Rellena el dato en **Configuración → Negocio** o en la ficha del producto.

## Un producto no se deja publicar

Selecciónalo en **Productos**: el panel derecho lista los errores que lo bloquean
(sin precio, sin fotografías, sin categoría, variables sin rellenar, medida
contradictoria…). Los avisos no bloquean; los errores sí, a propósito.

## Las automatizaciones no se ejecutan

* Comprueba que estén **activadas** (columna «Activa»).
* El planificador solo corre con la aplicación abierta.
* Revisa la columna «Estado» y el detalle de la última ejecución.

## Reinicio total (se pierden los datos locales)

Cierra la aplicación y borra la carpeta de datos
(`%LOCALAPPDATA%\LOT Bot`). Los anuncios publicados en Wallapop **no se tocan**:
se pueden volver a sincronizar.

## Recoger información para soporte

1. **Logs y errores → Abrir carpeta**.
2. Envía `lot_bot.log`.
3. Indica qué modo aparece en la barra de estado y qué acción falló.
