# Actualizar y mantener LOT Bot

## Publicar una versión nueva

1. Actualiza `__version__` en `lot_bot/__init__.py` y `version` en `pyproject.toml`.
2. Actualiza `build/version_info.txt` (`filevers`, `prodvers`, cadenas de versión).
3. Ejecuta `./scripts/check.sh`.
4. Compila: `powershell -ExecutionPolicy Bypass -File build\build_windows.ps1`.
5. Prueba el `.exe` en una máquina sin Python.
6. Comprime `dist\LOT-Bot` como `LOT-Bot-<versión>.zip`.
7. Etiqueta el repositorio: `git tag v1.0.1 && git push --tags`.

## Cómo actualiza el cliente

1. Cierra LOT Bot.
2. Renombra su carpeta actual a `LOT-Bot-anterior`.
3. Descomprime la nueva.
4. Copia de la carpeta anterior, si los había personalizado: `.env` y
   `config\endpoint_map.local.yaml`.
5. Abre `LOT-Bot.exe`.

**Sus datos no están en la carpeta del programa**, sino en `%LOCALAPPDATA%\LOT Bot`.
Al actualizar no se pierde nada: catálogo, cuentas, historial y ajustes siguen ahí.

## Cambios en la base de datos

El esquema se crea con `create_all()`, que **añade tablas nuevas pero no modifica las
existentes**. Para cambios en tablas con datos ya en producción:

### Cambios compatibles (columna nueva opcional)

SQLite acepta `ALTER TABLE ... ADD COLUMN`. Añade la columna al modelo y una migración
puntual en el arranque.

### Cambios incompatibles

`alembic` ya está en las dependencias. Para empezar a usarlo:

```bash
.venv/bin/alembic init migrations
# configurar sqlalchemy.url y target_metadata = lot_bot.database.models.Base.metadata
.venv/bin/alembic revision --autogenerate -m "descripcion"
.venv/bin/alembic upgrade head
```

Haz que el arranque ejecute `upgrade head` antes de abrir la ventana, y **copia la base
de datos antes de migrar**.

## Actualizar dependencias

```bash
.venv/bin/pip list --outdated
.venv/bin/pip install --upgrade <paquete>
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

Cuidado especial con **PySide6**: los saltos de versión mayor cambian APIs. Está fijada
a una versión concreta en `requirements.txt` a propósito.

## Añadir una herramienta nueva a la IA

1. Escribe el manejador en el módulo correspondiente de `lot_bot/ai/tools/`.
2. Declara el `Tool` con su esquema JSON, su categoría y, si escribe,
   `requires_confirmation=True` y la `capability` que necesita.
3. Añádelo a la lista del módulo (se registra solo en `build_registry()`).
4. Escribe una prueba: que pida confirmación y que no ejecute nada sin ella.

Con `requires_confirmation=True` el manejador **debe** devolver `confirm_first(...)`
cuando `context.confirmed` es `False`. El registro lo comprueba de todos modos.

## Añadir una operación de Wallapop

1. Añade el valor al enum `Capability`.
2. Añade el método abstracto a `WallapopService`.
3. Impleméntalo en `MockWallapopService` (datos simulados coherentes).
4. Impleméntalo en `ConnectWallapopService` usando `self._call(...)`.
5. Documenta la nueva entrada en `config/endpoint_map.example.yaml`.
6. Pruebas: que el mock funcione y que sin endpoint declarado se responda
   `NOT_AVAILABLE_WITH_CURRENT_WALLAPOP_ACCESS`.

## Añadir una pantalla

1. Crea la vista en `lot_bot/ui/views/` heredando de `BaseView`.
2. Impleméntale `build()` y `refresh()`.
3. Expórtala en `lot_bot/ui/views/__init__.py`.
4. Añádela a `NAVIGATION` en `lot_bot/ui/main_window.py`.
5. La prueba de humo la recorrerá automáticamente.

## Copias de seguridad

Lo que conviene respaldar del cliente:

```
%LOCALAPPDATA%\LOT Bot\lot_bot.db      catálogo, cuentas, historial
%LOCALAPPDATA%\LOT Bot\images\         fotografías importadas
%LOCALAPPDATA%\LOT Bot\config\         clave maestra (si no está en el llavero)
```

Sin la clave maestra, los tokens no se pueden descifrar y hay que volver a conectar
las cuentas (el catálogo y el historial se conservan igualmente).

## Mantenimiento periódico

* El historial se puede purgar con `AuditService.purge_older_than(días)`.
* Los logs rotan solos (5 ficheros de 2 MB).
* Las imágenes importadas no se borran solas: se guardan por hash, así que no se
  duplican.
