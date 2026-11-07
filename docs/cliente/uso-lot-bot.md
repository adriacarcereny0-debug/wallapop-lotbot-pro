# Uso diario de LOT Bot

Esta guía resume todo lo que necesitas para trabajar con LOT Bot cada día.

## 1. Abrir el programa

Haz doble clic en **LOT-Bot.exe**. Se abre en la pantalla **Asistente**, que es el
centro de todo: le escribes lo que quieres, en español normal, y él lo prepara.

Si ves arriba una franja que dice **MODO DEMOSTRACIÓN**, estás en el modo de
prueba: todo funciona igual, pero **nada se envía a Wallapop**. Es perfecto para
aprender sin miedo a equivocarte.

## 2. Hablar con el asistente

Escribe como hablarías con una persona. Ejemplos:

| Quieres… | Escribe |
|---|---|
| Ver el anuncio de canapés | «prepara el anuncio de canapé» |
| Publicarlo | «sube el canapé» |
| Empezar a publicar | «empieza a subir anuncios cada 60 segundos» (te pregunta la cuenta y cuántos) |
| Publicar varias copias | «publica 10 canapés en las cuentas 1 y 2» |
| Ver cómo va la publicación | «¿cómo va la cola?» |
| Pausar / seguir | «pausa la cola» / «reanuda la cola» |
| Cambiar el precio de lo que estás viendo | «cambia el precio de este anuncio a 250 €» |
| Ver estadísticas | «estadísticas» / «¿qué anuncios tienen más favoritos?» |
| Leer estadísticas nuevas | «actualiza las estadísticas» |
| Saber qué funciona mejor | «¿qué habitación funciona mejor?» / «recomendaciones» |
| Imágenes | «genera una imagen» / «mejora la imagen 3» / «cambia la habitación de la imagen 3 a dormitorio beige» |
| Revisar la calidad | «revisa los anuncios con problemas» |

Si el asistente no está seguro de a qué te refieres, **te pregunta** en lugar de
adivinar.

## 3. Confirmar siempre

Antes de publicar, cambiar o borrar algo, el asistente te enseña exactamente lo
que va a hacer. Por ejemplo:

> Voy a cambiar el precio del anuncio «Canapé…» de 230,00 € a 270,00 €. ¿Confirmas?

Pulsa **Confirmar** si es correcto o **Cancelar** si no. Sin tu confirmación no se
hace nada.

## 4. La plantilla única de canapés

Todos los anuncios automáticos usan **exactamente** tu plantilla:

* Título: «Canapé canapé canapé canapé canapé»
* Precio: 11,44 €
* Estado: Nuevo · Categoría/uso: Dormitorio · Color: Gris y Blanco · Material: Madera
  (van en los campos del formulario de Wallapop, no en la descripción)
* La descripción exacta, con las tres medidas y tu WhatsApp.

Mientras la casilla **«Plantilla única activa»** esté marcada (en **Anuncio
principal**), LOT Bot no cambia nunca por su cuenta el título, el precio ni la
descripción. Para cambiarlos, edítalos en esa pantalla y pulsa **Guardar**.
**Restaurar datos originales** vuelve a tus datos.

## 5. Fotos

Desde **Anuncio principal** o **Productos**, pulsa **Fotografías** para:
añadir fotos, verlas, cambiar su orden (la primera es la portada), eliminarlas y
comprobar que tienen buena calidad. LOT Bot avisa si una foto es demasiado pequeña
o está repetida.

## 6. Conectar tus cuentas de Wallapop

1. En **Cuentas**, pulsa **«Añadir cuenta Wallapop»** (si LOT Bot está en modo
   demostración, te pregunta si activas la integración) y ponle un nombre.
2. Se abre **tu Chrome (o Edge) normal** con Wallapop, en una ventana propia
   de esa cuenta, **y se queda abierta** (Paso 1/2). **Inicia sesión tú** con
   esa cuenta (Paso 2/2). Si Wallapop te pide un código o un CAPTCHA,
   complétalo allí.
3. Cuando veas tu cuenta en Wallapop, pulsa **«Ya he iniciado sesión»** en LOT
   Bot y **cierra esa ventana del navegador**: así se guarda la sesión.
4. LOT Bot **comprueba de verdad** que la sesión es válida. Solo entonces te
   pregunta si quieres conectarla. Si todavía no has entrado, te lo dice y
   puedes volver a intentarlo.

Cada cuenta tiene su propio navegador guardado, separado de las demás, y LOT
Bot lo reutiliza para publicar: no tendrás que volver a iniciar sesión en cada
anuncio. LOT Bot **nunca ve ni guarda tu contraseña**.

Botones de cada cuenta: **Abrir cuenta** (abre su navegador, por ejemplo para
completar una verificación), **Comprobar conexión**, **Reconectar**,
**Desconectar** (borra la sesión guardada) y **Eliminar cuenta**.

Si la sesión de una cuenta caduca mientras se publica, se detienen **solo** los
anuncios de esa cuenta; las demás siguen. Pulsa **Reconectar** y después
**Reanudar** en Publicación automática.

Estas cuentas se conectan para tu uso personal autorizado; no es una
aplicación oficial de Wallapop.

## 7. Publicación automática

Cuando dices «Publica 10 canapés» y confirmas, LOT Bot crea una **cola**:
publica un anuncio, espera **al menos 60 segundos**, publica el siguiente, y
así hasta terminar, repartiéndolos entre tus cuentas.

En la pantalla **Publicación automática** ves en todo momento cuántos van
publicados, cuál es la cuenta actual, la hora de la última publicación y la
de la siguiente. Puedes **Pausar**, **Reanudar**, **Cancelar** y
**Reintentar** un anuncio que haya fallado.

Si Wallapop pide una verificación o la sesión ha caducado, la cola **se
detiene sola**. Ve a **Cuentas**, selecciona la cuenta, pulsa **Abrir
navegador**, haz lo que te pida Wallapop, cierra la ventana y pulsa
**Reanudar**.

El intervalo se puede ampliar en Configuración → Wallapop, pero nunca bajar
de 60 segundos.

## 8. Una foto distinta para cada anuncio (FLUX.2 Pro)

LOT Bot puede crear una fotografía realista distinta para cada anuncio.

1. Consigue una clave de API en Black Forest Labs (bfl.ai).
2. Ve a **Configuración → IA / Imágenes**, pégala y pulsa **Guardar**.
3. Pulsa **Probar conexión y ver saldo** para comprobarla.

La clave se guarda cifrada en tu ordenador. Cada imagen consume créditos de
tu cuenta de Black Forest Labs. En el modo demostración no se gastan
créditos: se usan imágenes de prueba.

## 9. Estadísticas

En **Estadísticas** ves, por cuenta y anuncio: visualizaciones, favoritos,
visualizaciones por día, favoritos por cada 100 visitas, fecha de publicación,
estado y enlace. Puedes ordenar por visualizaciones, favoritos o rendimiento.

* Pulsa **Actualizar estadísticas** para leerlas de nuevo. Cada lectura se
  guarda, y así se ve cómo evoluciona cada anuncio (**Ver histórico del anuncio**).
* Si Wallapop no muestra un dato, aparece **«No disponible»**. LOT Bot nunca se
  inventa cifras.
* Abajo verás el análisis: los **DATOS** (lo que ha pasado) separados de las
  **RECOMENDACIONES** (lo que podrías probar). Con pocos datos, LOT Bot dice
  que todavía no puede sacar conclusiones.

## 10. Imágenes / IA

Selecciona una imagen de la lista y elige qué hacer:

* **Generar**: una foto nueva desde cero con los datos de tu anuncio (o con el
  producto que escribas en «Producto»).
* **Mejorar**: más resolución y nitidez (se hace en tu ordenador, sin gastar créditos).
* **Cambiar estilo** / **Cambiar habitación**: el mismo producto con otro ambiente.
* **Usar como referencia**: una foto nueva basada en el producto de la imagen
  elegida. Con **Subir foto propia…** puedes usar una foto tuya.

La imagen original nunca se modifica, las fotos repetidas se descartan y nunca
se añaden precios, teléfonos, logos ni texto. Generar y editar con FLUX.2 Pro
consume créditos y pide confirmación.

Los **mensajes sin leer** ya no aparecen en LOT Bot: no se pueden leer de
forma fiable desde el navegador. Consulta los mensajes en Wallapop.

## 11. Si algo no funciona

* **«Esta operación no está disponible con el acceso actual a Wallapop»**: esa
  función todavía no está habilitada para tus cuentas. No es un error tuyo.
* **«La sesión ha caducado»**: ve a **Cuentas** y pulsa **Reconectar**.
* **La cola se ha pausado con «No se ha podido completar el paso…»**: la web
  de Wallapop puede haber cambiado. Avisa a tu técnico; no hace falta
  reinstalar LOT Bot.
* Para cualquier otra cosa, la pantalla **Historial** muestra lo que ha pasado.
