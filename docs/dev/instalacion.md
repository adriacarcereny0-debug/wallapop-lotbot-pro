# Instalación del entorno de desarrollo

## Requisitos

* Python 3.11 o superior
* Git
* Windows 10/11 para generar el `.exe` (el desarrollo funciona también en Linux y macOS)

## Windows

```powershell
git clone <url-del-repositorio> lot-bot
cd lot-bot
powershell -ExecutionPolicy Bypass -File scripts\install_dev.ps1
```

Arrancar:

```powershell
.venv\Scripts\python.exe run_lot_bot.py
```

## Linux / macOS

```bash
git clone <url-del-repositorio> lot-bot
cd lot-bot
./scripts/install_dev.sh
.venv/bin/python run_lot_bot.py
```

En Linux, Qt necesita algunas librerías del sistema:

```bash
sudo apt-get install -y libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1
```

## Primer arranque

Sin `.env`, LOT Bot arranca en **MODO DEMO**:

* Cuatro cuentas simuladas con anuncios de canapés.
* Conversaciones de compradores de ejemplo.
* Todo funciona (publicar, cambiar precios, responder) sin tocar Wallapop.

Es el modo recomendado para desarrollar y para enseñar el programa al cliente.

## Pruebas

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q     # todas
.venv/bin/python -m pytest tests/test_e2e_demo.py -v        # extremo a extremo
.venv/bin/python -m pytest -m "not ui"                      # sin las de interfaz
./scripts/check.sh                                          # estilo + pruebas + import
```

## Dónde se guardan los datos

| Sistema | Carpeta |
|---|---|
| Windows | `%LOCALAPPDATA%\LOT Bot\` |
| macOS | `~/Library/Application Support/LOT Bot/` |
| Linux | `~/.local/share/lot-bot/` |

Contiene: `lot_bot.db`, `logs/`, `images/`, `config/`, `exports/`.

Para trabajar con datos aislados (útil en pruebas):

```bash
LOT_BOT_DATA_DIR=/tmp/lot-bot-pruebas .venv/bin/python run_lot_bot.py
```

## Estructura del repositorio

```
lot_bot/          código de la aplicación
tests/            pruebas automáticas
docs/dev/         esta documentación
docs/cliente/     manual para el usuario final
build/            receta de PyInstaller y scripts de compilación
scripts/          instalación y comprobaciones
config/           plantilla del mapa de endpoints
```
