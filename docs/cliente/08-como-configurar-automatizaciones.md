# Cómo configurar las automatizaciones

Las automatizaciones son tareas que LOT Bot repite cada cierto tiempo mientras el
programa está abierto.

## Lo importante

**Ninguna automatización publica, modifica ni borra nada.** Todas son de revisión:
miran, comparan y te avisan. Los cambios los decides siempre tú.

## Tareas disponibles

| Tarea | Qué hace | Cada cuánto (sugerido) |
|---|---|---|
| **Sincronizar anuncios** | Trae el estado actual de tus anuncios | 1 hora |
| **Sincronizar mensajes** | Trae los mensajes nuevos | 15 minutos |
| **Revisar anuncios** | Busca anuncios con información incompleta | 12 horas |
| **Revisar catálogo** | Busca productos con datos incorrectos | 12 horas |
| **Comprobar duplicados** | Detecta productos, anuncios y fotos repetidos | 1 día |
| **Sincronizar inventario** | Avisa de productos publicados sin stock | 6 horas |
| **Preparar publicaciones** | Deja listos los productos marcados como «listos» | 1 día |
| **Revisar errores** | Repasa los fallos recientes | 6 horas |

## Activar una tarea

1. Entra en **Automatizaciones**
2. Selecciona la tarea
3. Pulsa **Activar / desactivar**

La columna «Activa» pasará a **Sí** y verás cuándo será la próxima ejecución.

## Cambiar cada cuánto se ejecuta

1. Selecciona la tarea
2. Cambia el número en «Frecuencia (min)»
3. Pulsa **Guardar frecuencia**

Ejemplos: 15 = cada cuarto de hora · 60 = cada hora · 1440 = una vez al día.

## Ejecutar una ahora mismo

Selecciónala y pulsa **Ejecutar ahora**. Al terminar te enseña el resultado.

## Ver cómo van

La tabla te muestra, para cada tarea:

* Si está activa
* Cuándo se ejecutó por última vez
* Cuándo le toca
* Si terminó bien o con errores
* Un resumen del último resultado

Abajo, al seleccionar una tarea, tienes el detalle completo.

## Recomendación para empezar

Activa estas tres:

* **Sincronizar anuncios** — cada 60 minutos
* **Sincronizar mensajes** — cada 15 minutos
* **Revisar anuncios** — cada 720 minutos (12 horas)

Con eso tendrás siempre los datos al día y te avisará si algún anuncio tiene un
problema.

## Cosas a tener en cuenta

* Las tareas **solo se ejecutan con LOT Bot abierto**. Si lo cierras, se pausan y se
  reanudan al volver a abrirlo.
* No pongas frecuencias muy bajas en la sincronización: Wallapop limita el número de
  peticiones y podría bloquearte temporalmente.
* Todo lo que hacen queda registrado en **Historial de acciones**.
