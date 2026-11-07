# LOT Bot

Aplicación de escritorio para gestionar y automatizar **varias cuentas de Wallapop**
desde un único programa, con un asistente de IA que interpreta órdenes en español.

> El uso de este software requiere la autorización de Wallapop para la automatización
> de las cuentas gestionadas. LOT Bot está diseñado para trabajar **exclusivamente**
> con los endpoints y permisos concedidos en esa autorización.

```
┌─────────────────────────────────────────────────────────────────┐
│  Usuario                                                        │
│     ↓                                                           │
│  Interfaz de escritorio (PySide6)                               │
│     ↓                                                           │
│  Agente IA  →  Herramientas registradas  →  Servicios           │
│     ↓                                                           │
│  WallapopService                                                │
│     ├── MockWallapopService        (MODO DEMO)                  │
│     └── AuthorizedWallapopService  (acceso autorizado)          │
│            ↑ credencial                                         │
│         AuthMethod  (OAuth · sesión · credencial delegada)      │
└─────────────────────────────────────────────────────────────────┘
```

## Qué hace

* **Multicuenta real.** Cada cuenta está aislada: anuncios, estadísticas, sesión de navegador y
  credenciales nunca se mezclan.
* **Asistente en lenguaje natural.** «Cambia el precio de todos los canapés de
  135x190 a 269 €» → busca los anuncios, te enseña el plan, espera tu confirmación y
  los cambia.
* **Confirmación obligatoria.** Nada se publica, modifica ni elimina sin que el usuario
  pulse Confirmar. Hay tres barreras independientes que lo garantizan.
* **Catálogo con control de calidad.** Puntuación, errores bloqueantes y detección de
  duplicados por SKU, título, características e imágenes.
* **Plantillas de anuncio** con variables, para que todos los anuncios sean homogéneos.
* **Mensajería con asistente de ventas** que solo afirma datos reales. Si no sabe algo,
  responde «No dispongo de esa información».
* **Automatizaciones** programables, todas de revisión: ninguna publica por su cuenta.
* **Historial completo** de acciones y registro técnico sin secretos.
* **Modo DEMO** para probarlo todo sin tocar Wallapop.

## Empezar en 2 minutos (Windows)

1. Instala **Python 3.12** desde python.org (marca «Add python.exe to PATH»).
2. Descarga o clona este repositorio.
3. Doble clic en **`iniciar_lot_bot.bat`**. La primera vez prepara el entorno
   (unos minutos) y abre el programa.
4. Arranca en **MODO DEMO**: cuentas, anuncios, estadísticas simuladas y el anuncio principal de
   canapés vienen precargados. No hace falta API key, `client_id`, internet ni
   Wallapop. Nada de lo que hagas sale del ordenador.
5. Para generar el `.exe` del cliente: doble clic en **`compilar_exe.bat`**
   → `dist\LOT-Bot\LOT-Bot.exe`.

Prueba en el asistente: «sube el canapé», «publica 10 canapés en las cuentas 1 y 2»,
«estadísticas», «¿qué habitación funciona mejor?», «genera una imagen»,
«prepara una respuesta para este cliente».

## Anuncio principal (plantilla de canapés)

La pantalla **Anuncio principal** guarda el anuncio maestro del cliente con sus datos
exactos (título, características, precio 11,44 €, descripción con los precios
90x190 → 230 €, 135x190 → 270 €, 150x190 → 290 € y el WhatsApp). Los precios y el
teléfono son variables editables, no texto fijo.

* «sube el canapé» / «prepara el anuncio de canapé» → vista previa.
* «publica 10 canapés» → reparte copias entre cuentas y pide confirmación.
* Un cambio en una publicación concreta **no toca** la plantilla; solo
  «actualiza la plantilla» (con confirmación) la modifica.
* «Restaurar datos originales» devuelve los datos del cliente.

## Principios de diseño

### No se exige una API key, pero tampoco se inventa nada

LOT Bot **no presupone que el acceso a Wallapop sea una API con `client_id`**.
El mecanismo es el que Wallapop autorice, y se declara en un perfil de acceso.

Son **dos piezas independientes**, y hacen falta las dos:

```yaml
auth:                      # 1. CÓMO se identifica cada cuenta
  method: "session_handoff"
  session_handoff:
    login_url: "<lo indica Wallapop>"
    header_template:
      Authorization: "Bearer {session_token}"

api:                       # 2. QUÉ operaciones existen
  base_url: "<lo indica Wallapop>"
operations:
  update_item_price:
    method: PATCH
    path: "<ruta oficial>"
```

Resolver la autenticación **no** resuelve el transporte: aunque la sesión sea
válida, el programa sigue necesitando saber qué llamar. Lo que no esté declarado
responde `NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS` en vez de simularse.

Mecanismos soportados: `oauth`, `session_handoff`, `delegated_credential` y `demo`.

### Lo que LOT Bot no hace con tu sesión

* No pide, no maneja y no guarda contraseñas de Wallapop.
* No lee el perfil del navegador del usuario ni extrae cookies de él.
* No automatiza el formulario de inicio de sesión.
* No evita ni intenta resolver MFA, CAPTCHA ni ningún otro control.
* No deduce URLs: si no están declaradas, no hace nada y dice qué falta.

### La IA no puede salirse del guion

El agente solo puede invocar herramientas registradas. No accede a credenciales,
sesiones, cookies, HTTP ni a la base de datos. Si pide una función que no existe,
se le responde que no existe.

### Sin acceso configurado, no se finge

Si falta el perfil o el mecanismo está incompleto, la aplicación **se queda en
modo DEMO y enumera exactamente qué falta y quién debe proporcionarlo**, en vez
de aparentar estar conectada.

## Instalación (desarrollo)

```bash
git clone <url-del-repositorio> lot-bot
cd lot-bot

# Windows
powershell -ExecutionPolicy Bypass -File scripts\install_dev.ps1
.venv\Scripts\python.exe run_lot_bot.py

# Linux / macOS
./scripts/install_dev.sh
.venv/bin/python run_lot_bot.py
```

Sin configurar nada, arranca en **MODO DEMO** con cuatro cuentas simuladas.

## Generar el ejecutable para el cliente

```powershell
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
```

Resultado: `dist\LOT-Bot\LOT-Bot.exe`. El cliente descomprime la carpeta y hace doble
clic. No necesita instalar nada más.

## Configuración

Copia `.env.example` a `.env`:

```dotenv
LOT_BOT_DEMO_MODE=true            # false para el acceso real
WALLAPOP_ACCESS_PROFILE=          # ruta al perfil de acceso autorizado
WALLAPOP_REDIRECT_URI=http://127.0.0.1:8723/callback
LOT_BOT_AI_PROVIDER=rules         # 'anthropic' para lenguaje natural libre
ANTHROPIC_API_KEY=

# Solo si el mecanismo autorizado es OAuth:
WALLAPOP_CLIENT_ID=
WALLAPOP_CLIENT_SECRET=
```

`.env` está en `.gitignore`. **Nunca se suben secretos al repositorio.**

Detalle completo: [`docs/dev/variables-entorno.md`](docs/dev/variables-entorno.md).

## Pruebas

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

241 pruebas que cubren: anuncio principal, comprensión de frases del chat,: mock de Wallapop, perfil de acceso, mecanismos de
autenticación, aislamiento de credenciales entre cuentas, sesiones caducadas y
reautenticación, no filtrado de credenciales en logs, catálogo, control de calidad,
duplicados, plantillas, imágenes, agente IA y confirmaciones, asistente de ventas,
flujo de extremo a extremo en DEMO y humo de la interfaz.

## Estructura

```
lot_bot/
├── config/           ajustes, rutas, secretos cifrados
├── logs/             logging con redacción de secretos
├── database/         modelo SQLite (SQLAlchemy 2.0)
├── wallapop/         contrato, mock, acceso real, perfil de acceso, cuentas
│   └── auth/         mecanismos autorizados (OAuth, sesión, credencial)
├── catalog/          productos, calidad, duplicados
├── templates_engine/ plantillas de anuncio
├── images/           importación y deduplicación de fotos
├── master_ad/        anuncio principal (plantilla fija de canapés)
├── publishing/       vista previa, publicación, espejo de anuncios
├── messages/         bandeja de entrada y asistente de ventas
├── market/           análisis de precios
├── automation/       tareas programadas
├── ai/               proveedores, herramientas y orquestador
├── core/             auditoría y eventos
├── ui/               ventana principal, tema y 13 pantallas
└── bootstrap.py      ensamblado de servicios
```

## Documentación

**Para desarrollo**

| Documento | Contenido |
|---|---|
| [Arquitectura](docs/dev/arquitectura.md) | Diseño, capas, agente IA, multicuenta |
| [Instalación](docs/dev/instalacion.md) | Entorno de desarrollo y pruebas |
| [Variables de entorno](docs/dev/variables-entorno.md) | Todas las opciones |
| [Conexión con Wallapop](docs/dev/conexion-wallapop.md) | Configurar el acceso autorizado |
| [Qué pedir a Wallapop](docs/dev/que-pedir-a-wallapop.md) | Lista exacta de datos técnicos a solicitar |
| [Navegador, cola e imágenes](docs/dev/integracion-navegador.md) | Publicación por navegador, cola de 60 s, FLUX.2 Pro |
| [Compilación](docs/dev/compilacion.md) | Generar el `.exe` |
| [Solución de errores](docs/dev/solucion-errores.md) | Problemas y causas |
| [Actualización](docs/dev/actualizacion.md) | Versiones, migraciones, ampliaciones |

**Para el cliente**

[`docs/cliente/`](docs/cliente/README.md) — guías paso a paso, escritas sin
tecnicismos. Empieza por [uso-lot-bot.md](docs/cliente/uso-lot-bot.md).

## Estado de las funciones

| Función | Estado |
|---|---|
| Multicuenta con aislamiento (datos y credenciales) | ✅ |
| Acceso sin API key, con mecanismo intercambiable | ✅ |
| Asistente IA con herramientas y confirmación | ✅ |
| Catálogo, inventario y calidad | ✅ |
| Plantillas con variables | ✅ |
| Anuncio principal de canapés (plantilla fija) | ✅ |
| Gestión y deduplicación de imágenes | ✅ |
| Publicación con vista previa | ✅ |
| Mensajes sin leer | ⛔ Desactivados: no se pueden leer de forma fiable desde el navegador |
| Estadísticas con histórico, análisis y recomendaciones | ✅ «No disponible» si Wallapop no muestra el dato |
| Imágenes: generar, mejorar, cambiar estilo/habitación, referencia | ✅ |
| Automatizaciones programadas | ✅ |
| Historial y registro seguro | ✅ |
| Modo DEMO completo | ✅ |
| Empaquetado para Windows | ✅ |
| Publicar en Wallapop mediante navegador (sesión del usuario, uso personal autorizado) | ✅ Selectores pendientes de verificar en la web real ([detalles](docs/dev/integracion-navegador.md)) |
| Cola de publicación con intervalo mínimo de 60 s | ✅ |
| Una imagen generada por anuncio (FLUX.2 Pro) y detección de repetidas | ✅ |
| Acceso por API/perfil de acceso | ⚙️ Arquitectura lista, sin datos de Wallapop (ver [qué pedir](docs/dev/que-pedir-a-wallapop.md)) |
| Datos de mercado externos | ⚙️ Solo con fuente autorizada; sin ella se avisa |

## Licencia

Software propietario. Ver [LICENSE](LICENSE).
