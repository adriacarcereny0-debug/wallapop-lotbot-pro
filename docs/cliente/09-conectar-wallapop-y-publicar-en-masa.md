# Conectar Wallapop y publicar muchos anuncios

Con esta versión, LOT Bot puede publicar **sin abrir ningún navegador**: se
comunica directamente con Wallapop usando el acceso que Wallapop te haya
autorizado. Solo necesitas dos pantallas: **Conectar Wallapop** y
**Publicación masiva**.

> El sistema anterior (con navegador) sigue estando en «Cuentas de Wallapop»,
> «Publicación automática» y «Configuración». No hace falta que lo uses.

---

## Parte 1 · Conectar tu cuenta

Abre **Conectar Wallapop** en el menú de la izquierda.

### 1. Tipo de conexión

LOT Bot se conecta con la **credencial (token)** que Wallapop te da para cada
cuenta. Si arriba aparece el botón **Activar conexión por credencial**,
púlsalo una vez.

Si falta algo para poder conectar, aparece debajo en naranja, con lo que
falta exactamente. Normalmente lo prepara tu técnico una sola vez.

### 2. Cuenta

Elige la cuenta o pulsa **Añadir cuenta** y ponle un nombre que reconozcas
(por ejemplo, «Tienda Madrid»). Verás:

* **Estado de la cuenta** y **Estado de autenticación** (con un color).
* **Última conexión** y **Última comprobación**.
* **Cuándo caduca la credencial**, si Wallapop lo indica.

### 3. Conectar

Sigue los pasos numerados que aparecen en pantalla. Son:

1. Pulsa **Iniciar conexión**. Si Wallapop tiene una página para obtener la
   credencial, se abre en tu navegador habitual.
2. Copia la credencial y pégala en **Credencial de integración**.
3. Pulsa **Validar conexión**. LOT Bot la guarda cifrada (no se vuelve a ver)
   y comprueba con Wallapop que funciona.

Otros botones:

| Botón | Qué hace |
|---|---|
| **Comprobar estado** | Pregunta a Wallapop si la conexión sigue activa |
| **Renovar** | Cambia la credencial por la nueva que te dé Wallapop: pégala en el campo y pulsa Renovar |
| **Reconectar** | Vuelve a conectar, por ejemplo con una credencial nueva |
| **Desconectar** | Borra la credencial de este ordenador. Los anuncios pendientes no se pierden |

Abajo tienes el **historial de conexión** y el número de anuncios
pendientes, publicados y con error.

---

## Parte 2 · Publicar en masa

Abre **Publicación masiva**.

### Añadir anuncios

* **Añadir anuncio…**: rellenas cuenta, título, descripción, precio,
  categoría, estado y fotos.
* **Importar CSV…**: cientos de anuncios de una vez desde una hoja de
  cálculo guardada como CSV. Ejemplo: [ejemplo-anuncios.csv](ejemplo-anuncios.csv).

Columnas del CSV (la primera fila son los nombres):

| Columna | Obligatoria | Ejemplo |
|---|---|---|
| `titulo` | Sí | Canapé abatible 135x190 |
| `descripcion` | Sí | Nuevo, con montaje incluido |
| `precio` | Sí | 269,00 |
| `cuenta` | Si tienes varias cuentas | Tienda Madrid |
| `categoria` | No | Hogar y jardín |
| `estado` | No | Nuevo |
| `fotos` | No | fotos/canape1.jpg\|fotos/canape2.jpg |
| `prioridad` | No | 100 (menor = antes) |

Las fotos se separan con `|`. Si la ruta no empieza por la unidad (C:\\…),
se busca junto al fichero CSV.

LOT Bot **no añade duplicados**: si un anuncio ya está en la cola o ya está
publicado igual en esa cuenta, lo salta y te lo dice.

### Publicar

Pulsa **Iniciar publicación**. LOT Bot publica de uno en uno, en orden,
esperando al menos el tiempo indicado entre anuncios (60 segundos como
mínimo, o lo que indique Wallapop). Puedes **Pausar** y **Continuar** cuando
quieras.

Ajustes:

* **Segundos entre anuncios**: nunca menos del mínimo.
* **Máx. por cuenta y día**: para repartir la publicación en varios días.
* **Continuar sola al abrir LOT Bot**: si el ordenador se apaga a mitad,
  sigue sola al volver a abrir el programa.

### Qué significa cada estado

| Estado | Qué significa | Qué hacer |
|---|---|---|
| **Pendiente** | Espera su turno | Nada |
| **Publicando** | Se está publicando ahora | Nada |
| **Comprobando** | Se cortó a mitad: LOT Bot mira en Wallapop si llegó a publicarse antes de repetirlo | Nada |
| **Publicado** | Ya está en Wallapop | Nada |
| **Error** | Wallapop no lo aceptó (el motivo aparece al lado) | Corrige y pulsa **Reintentar seleccionados** |
| **Revisar** | No se ha podido saber si se publicó | Míralo en Wallapop: si está, **Marcar como publicado**; si no, **Reintentar** |
| **Cancelado** | Lo cancelaste tú | Puedes reintentarlo o quitarlo |

### Si algo va mal, no se pierde nada

* **Se cae internet**: espera y reintenta solo.
* **Caduca la conexión de una cuenta**: sus anuncios esperan; las demás
  cuentas siguen. Ve a **Conectar Wallapop → Reconectar**; al validar, la
  publicación sigue sola.
* **Se cierra el programa o se apaga el ordenador**: al volver a abrirlo, todo
  sigue en la cola. Lo que se estaba publicando se comprueba antes de
  repetirlo, para no duplicar anuncios.
* **Wallapop pide ir más despacio**: LOT Bot espera lo que Wallapop indique.
* **Wallapop pide una verificación**: la publicación se pausa. Hazla en
  Wallapop y pulsa **Continuar**. LOT Bot nunca intenta saltársela.
