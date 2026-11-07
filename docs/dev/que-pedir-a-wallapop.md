# Qué pedirle exactamente a tu contacto de Wallapop

Este documento existe para que puedas copiar y pegar. Tener autorización verbal
para automatizar es la condición legal, pero **no es información técnica**: el
programa necesita saber *cómo* identificarse y *qué* puede llamar.

## Resumen de una línea

> «Tengo autorización para automatizar mis cuentas. Necesito los datos técnicos
> del mecanismo de acceso que debo usar: cómo se autentica cada cuenta y qué
> operaciones tengo disponibles.»

---

## Bloque 1 — Autenticación: ¿cómo se identifica cada cuenta?

Pregunta primero **cuál de estos tres es tu caso**. Solo hace falta uno.

### Opción A — OAuth 2.0

> ¿Existe un flujo OAuth para esta integración? Si es así necesito:
>
> 1. URL de autorización
> 2. URL de token
> 3. URL de revocación (si existe)
> 4. `client_id` para esta integración
> 5. `client_secret` (si el flujo lo requiere; con PKCE puede no hacer falta)
> 6. URI(s) de redirección que debo registrar
> 7. Lista de *scopes* concedidos
> 8. ¿Se emiten *refresh tokens*? ¿Cuánto dura el token de acceso?

### Opción B — Inicio de sesión autorizado (sesión)

> Si el acceso es mediante una sesión iniciada por el usuario, necesito:
>
> 1. **URL exacta** a la que debo enviar al usuario para iniciar el flujo
> 2. **A dónde devuelve** el flujo cuando termina, y si puedo registrar una
>    dirección local (`http://127.0.0.1:8723/callback`)
> 3. **Qué campos me devuelve** al volver (nombre exacto de cada uno)
> 4. **Cómo debo enviar esa sesión** en cada petición posterior:
>    ¿en una cabecera? ¿cuál? ¿con qué formato? ¿en una cookie? ¿cuál?
> 5. **Cuánto dura** la sesión y si existe forma de renovarla sin que el
>    usuario vuelva a entrar
> 6. **Cómo sé que ha caducado** (¿un 401? ¿otro código?)
> 7. **Cómo se revoca** una sesión desde mi lado

### Opción C — Credencial delegada por cuenta

> Si Wallapop emite una credencial por cuenta, necesito:
>
> 1. Cómo la obtiene el titular de cada cuenta
> 2. En qué cabecera y con qué formato debe viajar
> 3. Si caduca y cada cuánto
> 4. Cómo se revoca

---

## Bloque 2 — Operaciones: ¿qué puedo hacer?

**Esto hace falta en los tres casos.** Resolver la autenticación no le dice al
programa qué llamar.

> Necesito, para cada operación que tenga autorizada:
>
> * Método HTTP y ruta exacta
> * Parámetros de consulta y cuerpo esperado
> * Estructura de la respuesta (o un ejemplo real)
> * Límites de uso (peticiones por minuto/hora)

Operaciones que LOT Bot sabe usar, por orden de importancia:

| Prioridad | Operación | Para qué |
|---|---|---|
| **Imprescindible** | Listar mis anuncios | Sin esto el programa no tiene datos |
| **Imprescindible** | Datos de la cuenta | Verificar que la sesión funciona |
| **Alta** | Cambiar el precio de un anuncio | La operación más habitual |
| **Alta** | Modificar un anuncio (título, descripción) | Mantenimiento diario |
| **Alta** | Crear un anuncio | Publicar productos nuevos |
| **Media** | Subir / cambiar fotografías | Completar los anuncios |
| **Media** | Listar categorías | Rellenar la categoría correcta |
| **Media** | Listar conversaciones y leerlas | Bandeja de entrada |
| **Media** | Enviar un mensaje | Responder a compradores |
| **Baja** | Eliminar un anuncio | Limpieza |
| **Baja** | Datos de mercado | Análisis de precios |

**LOT Bot funciona con las que haya.** Las que falten se marcan como
`NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS` y la aplicación lo dice en pantalla
en vez de simularlas.

---

## Bloque 3 — Reglas de uso

> ¿Hay límites de peticiones que deba respetar? ¿Alguna restricción sobre
> frecuencia de publicación o de cambios de precio? ¿Debo identificar la
> aplicación con algún `User-Agent` concreto?

---

## Mensaje listo para enviar

```
Hola,

Estoy desarrollando una aplicación de escritorio para gestionar mis propias
cuentas de Wallapop, al amparo de la autorización que me habéis confirmado.

Para implementarla correctamente necesito los datos técnicos del mecanismo de
acceso que debo utilizar. Son dos cosas independientes:

1) AUTENTICACIÓN — ¿cuál es mi caso?
   a) OAuth: URL de autorización, URL de token, client_id, URIs de redirección
      a registrar y scopes concedidos.
   b) Sesión: URL exacta del flujo, a dónde devuelve, qué campos devuelve, en
      qué cabecera o cookie debo enviarlos después, cuánto dura y cómo se
      revoca.
   c) Credencial por cuenta: cómo se obtiene, en qué cabecera viaja, caducidad
      y revocación.

2) OPERACIONES — para cada una que tenga autorizada: método HTTP, ruta,
   parámetros y un ejemplo de respuesta. Las prioritarias son: listar mis
   anuncios, datos de la cuenta, cambiar precio, modificar anuncio y crear
   anuncio.

3) LÍMITES — peticiones por minuto/hora y cualquier restricción de uso.

La aplicación no almacena contraseñas, no automatiza el formulario de acceso y
no elude ningún control de seguridad: el usuario se autentica en Wallapop con
todos los pasos que exijáis.

Gracias.
```

---

## Qué puedes hacer mientras tanto

Todo, menos conectar con Wallapop real:

* LOT Bot funciona en **MODO DEMO** con cuatro cuentas simuladas.
* Puedes cargar tu catálogo real, tus fotografías y tus plantillas: ese trabajo
  no se pierde cuando llegue el acceso.
* Puedes configurar tus datos de negocio y probar el asistente de IA.
* La pantalla **Cuentas de Wallapop → Ver qué falta para conectar** muestra en
  todo momento el estado exacto.

Cuando tengas las respuestas, se rellena `config/access_profile.local.yaml` y el
acceso real se activa **sin tocar el código ni recompilar el `.exe`**.
