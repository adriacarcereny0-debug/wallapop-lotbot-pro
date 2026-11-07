<#
.SYNOPSIS
    Prepara el entorno de desarrollo de LOT Bot en Windows.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_dev.ps1

    Fichero ASCII a proposito (ver build\build_windows.ps1).
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Fail([string]$message) {
    Write-Host "ERROR: $message" -ForegroundColor Red
    exit 1
}

Write-Host "=== LOT Bot - entorno de desarrollo ===" -ForegroundColor Cyan

$pythonCmd = $null
foreach ($candidate in @(@("py", "-3.12"), @("py", "-3.13"), @("py", "-3.11"), @("python"))) {
    if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
    $extra = @($candidate | Select-Object -Skip 1)
    try { $v = & $candidate[0] @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } catch { continue }
    if ($LASTEXITCODE -eq 0 -and $v -and [version]$v.Trim() -ge [version]"3.11" -and [version]$v.Trim() -le [version]"3.13") {
        $pythonCmd = $candidate; break
    }
}
if (-not $pythonCmd) { Fail "Instala Python 3.12 desde https://www.python.org/downloads/ (marca 'Add python.exe to PATH')." }
$pyExe = $pythonCmd[0]; $pyArgs = @($pythonCmd | Select-Object -Skip 1)

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creando entorno virtual..." -ForegroundColor Yellow
    & $pyExe @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "No se ha podido crear el entorno virtual." }
}

Write-Host "Instalando dependencias..." -ForegroundColor Yellow
& .venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .venv\Scripts\python.exe -m pip install -r requirements-dev.txt --quiet
if ($LASTEXITCODE -ne 0) { Fail "La instalacion de dependencias ha fallado." }

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Creado .env a partir de .env.example (MODO DEMO por defecto)."
}

Write-Host ""
Write-Host "=== Listo ===" -ForegroundColor Green
Write-Host "Arrancar:          .venv\Scripts\python.exe run_lot_bot.py   (o doble clic en iniciar_lot_bot.bat)"
Write-Host "Pruebas:           .venv\Scripts\python.exe -m pytest"
Write-Host "Generar el .exe:   powershell -ExecutionPolicy Bypass -File build\build_windows.ps1"
