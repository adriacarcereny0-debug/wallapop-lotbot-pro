<#
.SYNOPSIS
    Genera LOT-Bot.exe para Windows.

.DESCRIPTION
    Crea un entorno virtual limpio (.venv-build), instala las dependencias y
    empaqueta la aplicacion con PyInstaller.
    Resultado: dist\LOT-Bot\LOT-Bot.exe

    NOTA: este fichero es ASCII a proposito. Windows PowerShell 5.1 lee los
    .ps1 sin BOM como ANSI, y ciertos caracteres UTF-8 se convierten en
    comillas tipograficas que rompen las cadenas.

.PARAMETER SkipInstall
    No reinstala dependencias (mas rapido si ya se compilo antes).

.PARAMETER RunTests
    Ejecuta las pruebas automaticas antes de compilar.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
#>

param(
    [switch]$SkipInstall,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Fail([string]$message) {
    Write-Host ""
    Write-Host "ERROR: $message" -ForegroundColor Red
    exit 1
}

# En Windows PowerShell 5.1, un comando externo que falla NO detiene el
# script aunque ErrorActionPreference sea Stop: hay que mirar $LASTEXITCODE.
function Invoke-Checked([string]$description, [scriptblock]$command) {
    & $command
    if ($LASTEXITCODE -ne 0) {
        Fail "$description ha fallado (codigo $LASTEXITCODE)."
    }
}

Write-Host "=== LOT Bot - generacion del ejecutable ===" -ForegroundColor Cyan
Write-Host "Proyecto: $ProjectRoot"

# ---------------------------------------------------------------------
# 1. Buscar un Python real y compatible (3.11 a 3.13)
# ---------------------------------------------------------------------
# PySide6 6.8 publica paquetes para Python 3.9-3.13. Se prefiere el
# lanzador "py" porque "python" puede ser el acceso directo de la Microsoft
# Store, que existe pero no es Python.
$candidates = @(
    @("py", "-3.12"), @("py", "-3.13"), @("py", "-3.11"),
    @("python"), @("python3")
)
$pythonCmd = $null
foreach ($candidate in $candidates) {
    $exe = $candidate[0]
    $extra = @($candidate | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $version = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    } catch { continue }
    if ($LASTEXITCODE -ne 0 -or -not $version) { continue }
    $parsed = [version]$version.Trim()
    if ($parsed -ge [version]"3.11" -and $parsed -le [version]"3.13") {
        $pythonCmd = $candidate
        Write-Host "Python detectado: $version ($($candidate -join ' '))"
        break
    }
    Write-Host "Se ignora Python $version ($($candidate -join ' ')): se necesita 3.11, 3.12 o 3.13." -ForegroundColor Yellow
}
if (-not $pythonCmd) {
    Fail "No se ha encontrado Python 3.11, 3.12 o 3.13. Instala Python 3.12 desde https://www.python.org/downloads/ y marca 'Add python.exe to PATH'."
}
$pyExe = $pythonCmd[0]
$pyArgs = @($pythonCmd | Select-Object -Skip 1)

# ---------------------------------------------------------------------
# 2. Entorno virtual de compilacion
# ---------------------------------------------------------------------
$venv = Join-Path $ProjectRoot ".venv-build"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creando entorno virtual de compilacion..." -ForegroundColor Yellow
    Invoke-Checked "La creacion del entorno virtual" { & $pyExe @pyArgs -m venv $venv }
}

# ---------------------------------------------------------------------
# 3. Dependencias
# ---------------------------------------------------------------------
if (-not $SkipInstall) {
    Write-Host "Instalando dependencias (la primera vez tarda unos minutos)..." -ForegroundColor Yellow
    Invoke-Checked "La actualizacion de pip" { & $venvPython -m pip install --upgrade pip --quiet }
    Invoke-Checked "La instalacion de dependencias" { & $venvPython -m pip install -r requirements.txt --quiet }
    Invoke-Checked "La instalacion de PyInstaller" { & $venvPython -m pip install "pyinstaller>=6.8" --quiet }
}

# ---------------------------------------------------------------------
# 4. Comprobaciones previas
# ---------------------------------------------------------------------
Write-Host "Comprobando que la aplicacion importa correctamente..." -ForegroundColor Yellow
Invoke-Checked "La comprobacion de importacion" {
    & $venvPython -c "import lot_bot; from lot_bot.app import main; print('OK', lot_bot.__version__)"
}
foreach ($required in @("lot_bot\resources\lot_bot.ico", "config\access_profile.example.yaml", ".env.example", "build\LOT-Bot.spec")) {
    if (-not (Test-Path (Join-Path $ProjectRoot $required))) {
        Fail "Falta el fichero $required"
    }
}

if ($RunTests) {
    Write-Host "Ejecutando las pruebas..." -ForegroundColor Yellow
    Invoke-Checked "La instalacion de pytest" { & $venvPython -m pip install "pytest>=8" --quiet }
    $env:QT_QPA_PLATFORM = "offscreen"
    Invoke-Checked "Las pruebas" { & $venvPython -m pytest -q }
    Remove-Item Env:\QT_QPA_PLATFORM
}

# ---------------------------------------------------------------------
# 5. Limpiar compilaciones anteriores
# ---------------------------------------------------------------------
foreach ($folder in @("dist\LOT-Bot", "build\work")) {
    $path = Join-Path $ProjectRoot $folder
    if (Test-Path $path) {
        Write-Host "Limpiando $folder ..."
        Remove-Item $path -Recurse -Force
    }
}

# ---------------------------------------------------------------------
# 6. Empaquetar
# ---------------------------------------------------------------------
Write-Host "Empaquetando con PyInstaller (puede tardar varios minutos)..." -ForegroundColor Yellow
$specFile = Join-Path $PSScriptRoot "LOT-Bot.spec"
Invoke-Checked "PyInstaller" {
    & $venvPython -m PyInstaller $specFile --noconfirm --clean --workpath "build\work" --distpath "dist"
}

# ---------------------------------------------------------------------
# 7. Ficheros que el usuario puede editar sin recompilar
# ---------------------------------------------------------------------
$distDir = Join-Path $ProjectRoot "dist\LOT-Bot"
Copy-Item (Join-Path $ProjectRoot ".env.example") (Join-Path $distDir ".env.example") -Force
$configDir = Join-Path $distDir "config"
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
Copy-Item (Join-Path $ProjectRoot "config\access_profile.example.yaml") $configDir -Force
if (Test-Path (Join-Path $ProjectRoot "docs\cliente")) {
    Copy-Item (Join-Path $ProjectRoot "docs\cliente") (Join-Path $distDir "documentacion") -Recurse -Force
}

# Por seguridad: nunca se entrega un .env ni un perfil local con datos reales.
$leaked = Get-ChildItem -Path $distDir -Recurse -Force -Include ".env", "*.local.yaml", "*.db", "master.key" -ErrorAction SilentlyContinue
if ($leaked) {
    $leaked | ForEach-Object { Write-Host "  $($_.FullName)" -ForegroundColor Red }
    Fail "La carpeta a entregar contiene ficheros con datos privados. Revisalos antes de entregarla."
}

$exePath = Join-Path $distDir "LOT-Bot.exe"
if (-not (Test-Path $exePath)) {
    Fail "No se ha generado el ejecutable."
}
$sizeMb = [math]::Round(((Get-ChildItem $distDir -Recurse | Measure-Object -Property Length -Sum).Sum) / 1MB, 0)
Write-Host ""
Write-Host "=== Compilacion terminada ===" -ForegroundColor Green
Write-Host "Ejecutable: $exePath"
Write-Host "Tamano de la carpeta: $sizeMb MB"
Write-Host ""
Write-Host "Para entregarlo: comprime la carpeta dist\LOT-Bot en un .zip."
Write-Host "El cliente solo tiene que descomprimirla y abrir LOT-Bot.exe."
