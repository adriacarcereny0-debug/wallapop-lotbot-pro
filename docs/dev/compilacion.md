# Generar LOT-Bot.exe

El cliente final no tiene Python ni herramientas de desarrollo: recibe una carpeta con
un ejecutable y hace doble clic.

## Solución elegida: PyInstaller

Se ha optado por PyInstaller en modo **carpeta** (`--onedir`), no en fichero único:

| | Carpeta (elegido) | Fichero único |
|---|---|---|
| Arranque | 1–3 s | 10–20 s (se descomprime en cada arranque) |
| Antivirus | Menos falsos positivos | Más falsos positivos |
| Actualizar | Se sustituyen los ficheros cambiados | Se sustituye todo |
| Aspecto | Carpeta con el `.exe` dentro | Un solo fichero |

Se entrega la carpeta comprimida en `.zip`. Es la opción más fiable para un cliente
que va a usar el programa a diario.

Alternativas descartadas: **Nuitka** (compila más rápido pero complica el soporte de
Qt y alarga mucho la compilación) y **cx_Freeze** (menos maduro con PySide6).

## Compilar

En Windows:

```powershell
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1            # o doble clic en compilar_exe.bat
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1 -RunTests  # pasa las pruebas antes
```

El script: busca Python 3.11-3.13 (`py -3.12`, `py -3.13`, `py -3.11`, `python`;
ignora el acceso directo de Microsoft Store), crea `.venv-build`, instala
dependencias, comprueba que la aplicación importa, limpia compilaciones anteriores,
ejecuta PyInstaller, se detiene ante cualquier comando fallido y copia junto al
`.exe` el `.env.example`, `access_profile.example.yaml` y la documentación del
cliente. **Se niega a entregar** si en `dist` aparece un `.env`, un `*.local.yaml`,
una base de datos `*.db` o un `master.key`.

El `.exe` lee un `.env` y `config\access_profile.local.yaml` situados junto a él.
Los scripts están en ASCII puro para funcionar con Windows PowerShell 5.1.

Resultado: `dist\LOT-Bot\LOT-Bot.exe`

## Qué se entrega

```
LOT-Bot\
├── LOT-Bot.exe            ← doble clic
├── _internal\             ← librerías (no tocar)
├── .env.example           ← plantilla de configuración
├── config\
│   └── endpoint_map.example.yaml
└── documentacion\         ← manual del cliente
```

Comprime la carpeta en `LOT-Bot-1.0.0.zip` y entrégala.

## Tamaño

Entre 120 y 220 MB según la plataforma. La mayor parte es Qt. La receta ya descarta:

* Traducciones de Qt salvo español e inglés
* Complementos de Qt que no se usan (QML, multimedia, 3D, web, gráficas…)
* `tkinter`, `unittest`, `pytest` y librerías científicas

No se usa UPX: comprime algo más pero dispara los falsos positivos de los antivirus.

## Firma de código (recomendado)

Sin firma, Windows SmartScreen avisará al cliente la primera vez. Con un certificado
de firma de código:

```powershell
signtool sign /f certificado.pfx /p CONTRASEÑA /tr http://timestamp.digicert.com /td sha256 /fd sha256 dist\LOT-Bot\LOT-Bot.exe
```

## Comprobaciones antes de entregar

1. `./scripts/check.sh` (o `pytest`) en verde.
2. Compilar y abrir el `.exe` en una máquina **sin Python instalado**.
3. Comprobar que arranca en MODO DEMO y que las doce pantallas se abren.
4. Comprobar que el `.zip` **no contiene** `.env`, tokens ni `endpoint_map.local.yaml`.

```powershell
# Comprobación rápida de que no se cuela ningún secreto
Get-ChildItem -Recurse dist\LOT-Bot -Include .env,*.local.yaml,*.key
```

## Problemas habituales

| Síntoma | Solución |
|---|---|
| `ModuleNotFoundError` al ejecutar el `.exe` | Añade el módulo a `hiddenimports` en `build/LOT-Bot.spec` |
| Se abre una ventana negra de terminal | `console=False` en el spec (ya está) |
| No aparece el icono | Comprueba que existe `lot_bot/resources/lot_bot.ico` |
| El antivirus lo bloquea | Firma el ejecutable; evita UPX |
| «No se puede escribir la base de datos» | Los datos van a `%LOCALAPPDATA%`, no junto al `.exe`. Revisa permisos de esa carpeta |
