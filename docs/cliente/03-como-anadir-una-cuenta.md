# Cómo añadir una cuenta de Wallapop

LOT Bot puede gestionar varias cuentas a la vez. Cada una está separada de las demás:
sus anuncios, sus mensajes y sus permisos nunca se mezclan.

## Añadir una cuenta

1. Entra en **Cuentas Wallapop**
2. Pulsa **Añadir cuenta**
3. Escribe un nombre para identificarla (por ejemplo, «Tienda principal» o «Cuenta 2»)
4. Pulsa Aceptar

La cuenta aparece en la lista como **Desconectada**.

## Conectarla con Wallapop

1. Selecciona la cuenta en la lista
2. Pulsa **Conectar**
3. LOT Bot lanza el proceso que corresponda a tu tipo de acceso:
   * Si es por inicio de sesión, se abre tu navegador en Wallapop. Entra con esa
     cuenta, completa los pasos de seguridad que Wallapop te pida y autoriza.
   * Si es por credencial de cuenta, se te pide ese código una sola vez.
4. Cuando termine, la cuenta pasa a **Conectada**.

> LOT Bot no te pide la contraseña de Wallapop en ningún momento. Si el botón
> **Conectar** está desactivado, es que todavía falta algún dato técnico: pulsa
> «Ver qué falta para conectar con Wallapop».

> Repite el proceso con cada una de tus cuentas. Usa el navegador en modo privado o
> cierra la sesión entre una y otra para no autorizar dos veces la misma.

## Qué significa cada estado

| Estado | Qué quiere decir | Qué hacer |
|---|---|---|
| **Conectada** | Todo correcto | Nada |
| **Desconectada** | Aún no la has autorizado | Pulsa Conectar |
| **Sesión caducada** | El permiso ha expirado | Pulsa **Volver a autenticar** |
| **Error** | Algo ha fallado | Mira el detalle en la misma fila |

## Traer los anuncios de la cuenta

Selecciona la cuenta y pulsa **Sincronizar**. LOT Bot descarga sus anuncios y sus
conversaciones para que puedas trabajar con ellos.

También puedes sincronizar todas a la vez desde el **Panel** → **Sincronizar ahora**.

La columna «Última sincronización» te dice cuándo se hizo por última vez.

## Volver a autenticar una cuenta

Si una cuenta aparece como **Sesión caducada**, selecciónala y pulsa **Volver a
autenticar**. Se repite el mismo proceso que al conectarla por primera vez y se
sustituye el acceso guardado por uno nuevo.

LOT Bot detecta solo cuándo una sesión ha dejado de valer: no te deja creer que
sigue conectada cuando no lo está.

## Cambiar el nombre de una cuenta

Selecciónala y pulsa **Renombrar**. Es solo un nombre interno para que te aclares;
no afecta a Wallapop.

## Desconectar una cuenta

Selecciónala y pulsa **Desconectar**. Se borra el acceso guardado en tu ordenador
y, si Wallapop lo permite, se revoca también por su parte.

Tus anuncios siguen en Wallapop tal cual: solo se corta el acceso del programa.

## Eliminar una cuenta

Selecciónala y pulsa **Eliminar cuenta**. Se borra de LOT Bot junto con los anuncios y
mensajes que tenía guardados **en tu ordenador**.

**Los anuncios publicados en Wallapop no se tocan.**

## Repartir productos entre cuentas

En **Productos**, selecciona un producto y pulsa **Asignar a cuentas**. Marca en qué
cuentas quieres que se publique.

Por ejemplo:

```
Canapé 135x190 Gris  →  Cuenta 1, Cuenta 2, Cuenta 3
Canapé 90x190 Blanco →  Cuenta 2, Cuenta 4
```

Así controlas exactamente qué se publica dónde y evitas duplicados no deseados.
