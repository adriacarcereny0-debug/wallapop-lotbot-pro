# Conectar LOT Bot con Wallapop

LOT Bot **no exige una API key**. El mecanismo de acceso es el que Wallapop haya
autorizado, y se declara en un fichero de configuración.

> Si todavía no tienes los datos técnicos, ve primero a
> [**Qué pedirle a tu contacto de Wallapop**](que-pedir-a-wallapop.md).

## Lo primero: son dos piezas, no una

Es el error conceptual más frecuente, así que conviene dejarlo claro:

| Pieza | Pregunta que responde | Dónde se declara |
|---|---|---|
| **Autenticación** | ¿Cómo demuestra esta cuenta quién es? | `auth:` |
| **Transporte** | ¿Qué operaciones existen y cómo se llaman? | `api:` + `operations:` |

**Resolver la autenticación no resuelve el transporte.** Aunque tengas una
sesión perfectamente válida, el programa sigue necesitando saber qué llamar.
Por eso hacen falta las dos, y por eso LOT Bot no puede deducir la segunda a
partir de la primera.

## Mecanismos que LOT Bot sabe manejar

| Mecanismo | Cuándo se usa | Qué necesita |
|---|---|---|
| `oauth` | Wallapop ofrece un flujo OAuth 2.0 | URLs de autorización y token, `client_id`, URI de redirección |
| `session_handoff` | El usuario inicia sesión y el flujo devuelve una sesión | URL de entrada, dirección de retorno, campos devueltos, cómo aplicarlos |
| `delegated_credential` | Wallapop emite una credencial por cuenta | Formato de la cabecera; el valor lo introduce el usuario |
| `demo` | Sin acceso configurado | Nada. Datos simulados |

Si no se declara ninguno, LOT Bot se queda en **MODO DEMO** y dice por qué.

## Pasos

### 1. Crear el perfil de acceso

```bash
cp config/access_profile.example.yaml config/access_profile.local.yaml
```

`*.local.yaml` está en `.gitignore`: no se sube al repositorio.

### 2. Declarar la autenticación

**Si tu caso es inicio de sesión autorizado:**

```yaml
auth:
  method: "session_handoff"
  session_handoff:
    login_url: "<la URL exacta que te indique Wallapop>"
    return_uri: "http://127.0.0.1:8723/callback"
    session_fields: ["<los campos que devuelve el flujo>"]
    header_template:
      Authorization: "Bearer {session_token}"
    expires_after_minutes: 720
```

**Si tu caso es OAuth:**

```yaml
auth:
  method: "oauth"
  oauth:
    authorize_url: "<URL oficial>"
    token_url: "<URL oficial>"
    use_pkce: true
    scopes: ["<los concedidos>"]
```

Y en el `.env`: `WALLAPOP_CLIENT_ID=...` (el secreto, si hace falta, en
`WALLAPOP_CLIENT_SECRET`).

**Si tu caso es credencial delegada:**

```yaml
auth:
  method: "delegated_credential"
  delegated_credential:
    header_name: "Authorization"
    header_format: "Bearer {credential}"
    instructions: "Dónde obtiene el usuario esta credencial."
```

### 3. Declarar el transporte

```yaml
api:
  base_url: "<la dirección base que te indique Wallapop>"

operations:
  list_items:
    method: GET
    path: "<ruta oficial>"
    query:
      limit: "{limit}"
      offset: "{offset}"
    response:
      collection_path: "<ruta al array en la respuesta>"
      fields:
        item_id: "<campo del id>"
        title: "<campo del título>"
        price: "<campo del precio>"
```

Declara **solo** lo que te hayan autorizado.

### 4. Apuntar el `.env` al perfil

```dotenv
LOT_BOT_DEMO_MODE=false
WALLAPOP_ACCESS_PROFILE=C:\ruta\a\config\access_profile.local.yaml
WALLAPOP_REDIRECT_URI=http://127.0.0.1:8723/callback
```

### 5. Comprobar

Abre LOT Bot → **Configuración → Wallapop**. Debe decir **WALLAPOP REAL** y el
mecanismo activo. Si sigue en DEMO, ahí mismo aparece la lista exacta de lo que
falta.

### 6. Conectar una cuenta

**Cuentas de Wallapop → Añadir cuenta**. LOT Bot lanza automáticamente el flujo
que corresponda al mecanismo declarado. No hay pantalla de «introduce tu API
key».

## Qué hace LOT Bot con tu sesión, y qué no hace

**Lo que hace:**

* Abre tu navegador en la dirección autorizada y espera el retorno.
* Guarda la credencial cifrada, asociada a una cuenta concreta.
* La aplica en cada petición según las plantillas declaradas.
* Detecta cuándo deja de ser válida y te pide volver a autenticarte.
* Te permite revocarla desde la aplicación.

**Lo que no hace, y no va a hacer:**

* No pide, no maneja y no guarda tu contraseña de Wallapop.
* No lee el perfil de tu navegador ni extrae cookies de él.
* No automatiza el formulario de inicio de sesión.
* No evita ni intenta resolver MFA, CAPTCHA ni ningún otro control.
* No deduce la URL de inicio de sesión: si no está declarada, no hace nada.

Todo lo anterior sería eludir controles de acceso. LOT Bot se limita a escuchar
el retorno del flujo que Wallapop haya autorizado expresamente.

## Operaciones y su efecto si faltan

| Operación | Sin ella |
|---|---|
| `account_profile` | No se muestra el usuario de Wallapop |
| `list_items` | No hay sincronización: las pantallas quedan vacías |
| `get_item` | Se usa lo ya sincronizado |
| `search_items` | Se filtra en local |
| `create_item` | No se puede publicar |
| `update_item` | No se pueden editar anuncios |
| `update_item_price` | Se intenta con `update_item`; si tampoco está, no se puede |
| `update_item_images` | No se pueden cambiar fotos |
| `delete_item` | No se puede eliminar |
| `upload_image` | Se publica sin subir imágenes previamente |
| `list_categories` | La categoría se escribe a mano |
| `list_conversations` | La pantalla de Mensajes queda desactivada |
| `get_conversation` | Solo se ve el resumen |
| `send_message` | Se preparan borradores, no se envían |
| `market_data` | El análisis usa solo tus propios anuncios |

## Sustituciones disponibles

En `path`, `query`, `body`, `header_template` y `cookie_template`:

`{item_id}`, `{conversation_id}`, `{limit}`, `{offset}`, `{price}`, `{title}`,
`{description}`, `{currency}`, `{category}`, `{condition}`, `{images}`,
`{text}`, `{body}`, `{query}`, `{min_price}`, `{max_price}`, `{status}`,
más cualquier campo que devuelva tu flujo de autenticación.

## Problemas habituales

| Síntoma | Causa |
|---|---|
| Sigue en MODO DEMO | Falta el perfil, la autenticación o el transporte. El motivo aparece en Configuración → Wallapop |
| «Faltan datos técnicos que debe facilitar Wallapop» | El mecanismo declarado está incompleto. La pantalla lista qué falta |
| `NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS` | Esa operación no está declarada en `operations:` |
| «La sesión ha caducado» | Pulsa «Volver a autenticar» en Cuentas de Wallapop |
| 401 al sincronizar | La credencial ya no vale: reautentica la cuenta |
| 403 | La cuenta no tiene ese permiso concedido |
| Los anuncios llegan sin título o precio | El `response.fields` no apunta a los campos correctos |
| 429 | Límite de peticiones: baja la frecuencia de las automatizaciones |

## Migrar desde la versión anterior

Si ya tenías un `endpoint_map.local.yaml`:

1. Renómbralo a `access_profile.local.yaml` (o deja `WALLAPOP_ENDPOINT_MAP`
   apuntando a él: se sigue aceptando).
2. Añádele el bloque `auth:` con el mecanismo que corresponda. El bloque
   `oauth:` que tuvieras dentro de `endpoint_map` sigue siendo válido: muévelo a
   `auth.oauth`.
3. Las cuentas ya conectadas se migran solas: la base de datos añade las
   columnas nuevas al arrancar.
