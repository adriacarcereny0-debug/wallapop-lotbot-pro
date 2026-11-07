# Conexión directa con Wallapop (sin navegador)

Esta versión de LOT Bot añade un **motor de publicación que no abre ni
controla ningún navegador**. Habla directamente con el servicio autorizado
por Wallapop con la **credencial (token) de integración** que Wallapop emite
para cada cuenta. Es la única forma de conexión directa de este repositorio:
la integración con la API oficial se desarrolla en otro repositorio.

El sistema anterior (navegador) **sigue disponible** y no se ha eliminado:
«Cuentas de Wallapop» y «Publicación automática» funcionan igual que antes.

## Qué se ha encontrado al analizar el proyecto

| Pieza | Dónde estaba | Cómo funcionaba |
|---|---|---|
| Autenticación | `lot_bot/wallapop/auth/` + `browser/auth.py` | Varios mecanismos declarables (OAuth, sesión autorizada, credencial delegada) y, en la práctica, sesión de navegador por cuenta |
| Motor de publicación | `publishing/queue.py` → `master_ad/service.py` → `browser/form.py` | Rellenaba el formulario web con Playwright |
| Fotografías | `images/service.py` y el formulario web | Validación local; subida arrastrando ficheros en la web |
| Cola | `publish_jobs` / `publish_tasks` | Persistente, ligada al «Anuncio principal» |
| Acceso autorizado por HTTP | `authorized_service.py` + perfil YAML | Ya existía, sin URL ni endpoints en el código |

La conclusión fue **reutilizar** la capa autorizada por HTTP (que ya cumplía
la regla «no inventar endpoints») y construir encima un motor independiente
del navegador, en lugar de reescribir la aplicación.

## Mecanismo: solo credencial (token)

La conexión directa admite un único mecanismo, `delegated_credential`: la
credencial (token) que Wallapop emite para cada cuenta. El cliente la pega en
«Conectar Wallapop». Si Wallapop documenta una página para obtenerla
(transferencia manual), se declara en `portal_url` y el botón «Iniciar
conexión» la abre en el navegador habitual del cliente.

Si el perfil declara otro mecanismo (`oauth`, `session_handoff`) y se activa
la conexión directa, LOT Bot se queda en DEMO y explica que solo admite
`delegated_credential` (prueba `test_la_conexion_directa_solo_admite_token`).

Lo que **no** hace, por diseño: no extrae cookies ni credenciales del
navegador, no usa tokens internos ni endpoints no declarados, no automatiza
formularios de acceso y no intenta saltarse CAPTCHA ni verificaciones. Si
Wallapop pide una verificación, el motor se pausa y lo hace el usuario.

## Arquitectura

```
Pantalla «Conectar Wallapop»         Pantalla «Publicación masiva»
            │                                     │
            ▼                                     ▼
  AuthenticationManager ◄──────────── PublicationEngine ───► ImageManager
            │                          │        │                 │
            │                          ▼        │                 │
            │                    QueueManager   │                 │
            │                          │        ▼                 ▼
            │                          │   WallapopClient ◄───────┘
            │                          │        │
            ▼                          ▼        ▼
     AccountManager (cifrado)   DatabaseManager   WallapopService (factory.py)
                                     │                 ├─ MockWallapopService       (demo)
                                     ▼                 └─ AuthorizedWallapopService (credencial/token)
                                  SQLite
```

Todo vive en `lot_bot/integration/`, un fichero por módulo:

| Módulo | Fichero | Responsabilidad |
|---|---|---|
| `AuthenticationManager` | `auth_manager.py` | Iniciar conexión, credencial (token), validar, comprobar, renovar, reconectar, desconectar, historial |
| `WallapopClient` | `wallapop_client.py` | Única puerta hacia Wallapop: límite de peticiones/minuto, clasificación de errores, idempotencia, búsqueda de anuncios ya publicados |
| `PublicationEngine` | `publication_engine.py` | Hilo que publica la cola de uno en uno, con intervalo mínimo, máximo diario y recuperación |
| `QueueManager` | `queue_manager.py` | Cola persistente, duplicados, CSV, estados, historial, recuperación tras cierre |
| `ImageManager` | `image_manager.py` | Valida, deduplica, limita y sube las fotos sin repetir subidas |
| `DatabaseManager` | `database_manager.py` | Esquema, sesiones, ajustes, integridad y copias de seguridad |

`build_integration(app)` (en `lot_bot/integration/__init__.py`) los ensambla y
`bootstrap.create_application` los expone como `app.auth_manager`,
`app.wallapop_client`, `app.publication_engine`, `app.queue_manager`,
`app.image_manager` y `app.database_manager`.

Una prueba (`test_el_motor_directo_no_importa_el_navegador`) garantiza que
ningún fichero del paquete importa Playwright ni el módulo del navegador.

## Persistencia: la cola nunca se pierde

Tablas nuevas (se crean solas al arrancar; no hay que migrar nada):

| Tabla | Contenido |
|---|---|
| `ad_queue_items` | Cada anuncio: datos, fotos, huella, clave de idempotencia, estado, intentos, error, id y URL en Wallapop |
| `ad_queue_events` | Historial de cada anuncio (cada cambio de estado) |
| `wallapop_connections` | Estado de conexión de cada cuenta (sin credenciales) |
| `wallapop_connection_events` | Historial de conexión |

Las credenciales siguen cifradas en `accounts.credential_enc` (no se
duplican). Cada cambio se confirma al momento (SQLite en modo WAL).

### Estados de un anuncio

```
            ┌──────────── reintento (red, 5xx) ─────────────┐
            ▼                                               │
PENDING ──► PROCESSING ──► PUBLISHED                        │
  ▲  ▲          │  │                                        │
  │  │          │  └── datos rechazados ──► FAILED ─(Reintentar)─► PENDING
  │  │          │
  │  │          └── respuesta perdida / cierre ──► VERIFYING
  │  │                                               │  ├─ encontrado ─► PUBLISHED
  │  └──────────── no encontrado ────────────────────┘  └─ no se puede comprobar ─► NEEDS_REVIEW
  │
  └── 401 / límite diario / 429 / pausa  (sin gastar intento)
```

| Situación | Qué pasa |
|---|---|
| Error de autenticación (401) | El anuncio vuelve a `PENDING` sin gastar intento; la cuenta pasa a «Hay que reconectar»; las demás cuentas siguen. Al reconectar, el motor continúa solo |
| Cierre inesperado | Al arrancar, lo que estaba en `PROCESSING` pasa a `VERIFYING`. Si el motor estaba en marcha, queda en pausa (o continúa solo si se activó «Continuar sola al abrir LOT Bot») |
| Sin conexión (la petición no salió) | Reintento con espera creciente (1, 2, 4… min, máx. 1 h) |
| Respuesta perdida (pudo llegar) | `VERIFYING`: se busca en Wallapop antes de repetir. Con `idempotency_header` declarado, se reintenta directamente |
| 429 | Espera lo que indique `Retry-After` (o 5 min) sin gastar intento |
| 400/409/422 | `FAILED` con el motivo; «Reintentar» lo vuelve a poner en cola |
| 403 / operación no declarada | Pausa con explicación |
| Verificación de Wallapop | Pausa: la completa el usuario |
| 5 fallos seguidos | Pausa |

### Duplicados

* Huella = cuenta + título + precio + descripción normalizados (sin acentos
  ni mayúsculas). No se admite otro anuncio con la misma huella si el
  primero está pendiente, en curso, a revisar o publicado.
* Tampoco uno idéntico (título y precio) a un anuncio activo de esa cuenta
  en «Anuncios».
* Una publicación interrumpida **nunca** se repite a ciegas (ver
  `VERIFYING`/`NEEDS_REVIEW`).
* Cada anuncio lleva una `idempotency_key` única que se envía si el servicio
  la admite (`limits.idempotency_header`).

## Límites y condiciones de uso

El perfil admite un bloque `limits:` con lo que indique Wallapop:

```yaml
limits:
  min_seconds_between_publications: 60
  max_publications_per_day: 200     # por cuenta
  max_requests_per_minute: 30
  idempotency_header: "Idempotency-Key"
  max_images_per_item: 10
  user_agent: "LOT-Bot/1.0 (+contacto)"
```

* Sin declarar, el mínimo entre publicaciones es **60 s** (el mismo que el
  sistema anterior). El usuario puede subirlo, nunca bajarlo del mínimo.
* El máximo diario efectivo es el menor entre el declarado y el del usuario.
* El intervalo cuenta también las publicaciones del sistema anterior.

## Configurar la credencial (token)

1. Copia `config/access_profile.example.yaml` a `config/access_profile.local.yaml`.
2. Rellénalo con lo que indique Wallapop:

```yaml
auth:
  method: "delegated_credential"
  delegated_credential:
    header_name: "Authorization"
    header_format: "Bearer {credential}"
    instructions: "Cómo obtiene el cliente su credencial."
    portal_url: ""          # página documentada por Wallapop, si existe
api:
  base_url: "<la que indique Wallapop>"
operations:
  account_profile: { ... }  # para validar la credencial
  create_item: { ... }      # imprescindible para publicar
  upload_image: { ... }     # opcional
  list_items: { ... }       # opcional: recuperar interrupciones sin duplicar
limits: { ... }
```

3. Deja el fichero en la carpeta `config/` junto a `LOT-Bot.exe` (o apunta
   `WALLAPOP_ACCESS_PROFILE` a él). No hay que recompilar.
4. En LOT Bot: **Conectar Wallapop → Activar conexión por credencial**.

La API oficial no forma parte de este repositorio. `WallapopClient` aísla al
motor, a la cola y a la interfaz del servicio concreto, por lo que una futura
implementación de `WallapopService` se puede registrar en
`wallapop/factory.py` sin cambiar nada de lo que hay encima.

## Pruebas

```bash
python -m pytest tests/test_direct_integration.py tests/test_ui_direct.py -q
```

Usan un Wallapop simulado (`httpx.MockTransport`) y un reloj simulado: no
tocan Wallapop real ni esperan 60 s de verdad. El reloj simulado está
bloqueado frente a un servicio real (`test_el_reloj_de_pruebas_no_llega_a_wallapop_real`).
