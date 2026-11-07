# Conexión directa con Wallapop (sin navegador)

Esta versión de LOT Bot añade un **motor de publicación que no abre ni
controla ningún navegador**. Habla directamente con el servicio autorizado
por Wallapop, con la credencial de integración provisional que Wallapop
emita hoy y, sin reconstruir nada, con la **API oficial** cuando se conceda.

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

## Mecanismos autorizados (y lo que NO se hace)

LOT Bot solo usa lo que Wallapop declare en el perfil de acceso:

* **Credencial de integración provisional** (`delegated_credential`): el
  cliente la pega en «Conectar Wallapop». Si Wallapop documenta una página
  para obtenerla (transferencia manual), se declara en `portal_url` y el
  botón «Iniciar conexión» la abre en el navegador habitual del cliente.
* **OAuth** (`oauth`): previsiblemente, la API oficial.
* **Inicio de sesión autorizado** (`session_handoff`).

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
                                     │                 ├─ MockWallapopService      (demo)
                                     ▼                 ├─ AuthorizedWallapopService (perfil)
                                  SQLite               └─ AuthorizedWallapopService (oficial)
```

Todo vive en `lot_bot/integration/`, un fichero por módulo:

| Módulo | Fichero | Responsabilidad |
|---|---|---|
| `AuthenticationManager` | `auth_manager.py` | Iniciar conexión, credencial provisional, validar, comprobar, renovar, reconectar, desconectar, historial |
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

## Transición a la API oficial

El proveedor provisional y la API oficial usan **el mismo código**: solo
cambia el perfil.

1. Copia `config/official_api.example.yaml` a `config/official_api.local.yaml`
   (o apunta `WALLAPOP_OFFICIAL_PROFILE` a otro sitio).
2. Rellénalo con la documentación oficial: normalmente `auth.method: oauth`
   con sus URL y `client_id` (en `.env`), `api.base_url`, `operations:` y
   `limits:`.
3. En LOT Bot: **Conectar Wallapop → API oficial de Wallapop → Usar este
   tipo**, y conecta cada cuenta (Iniciar conexión → autorizar en Wallapop).

La cola, el historial y los anuncios pendientes se conservan: lo que quedara
en cola se publica con la API oficial. Si la API oficial necesitara algo que
el perfil no pueda expresar, se escribe una subclase de `WallapopService` y
se registra en `wallapop/factory.py`; `WallapopClient` y todo lo que hay
encima no cambian.

## Pruebas

```bash
python -m pytest tests/test_direct_integration.py tests/test_ui_direct.py -q
```

Usan un Wallapop simulado (`httpx.MockTransport`) y un reloj simulado: no
tocan Wallapop real ni esperan 60 s de verdad. El reloj simulado está
bloqueado frente a un servicio real (`test_el_reloj_de_pruebas_no_llega_a_wallapop_real`).
