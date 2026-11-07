#!/usr/bin/env bash
# Comprueba la receta de empaquetado en Linux/macOS (util durante el desarrollo).
# El ejecutable para el cliente se genera en Windows con build_windows.ps1.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
    echo "No se encuentra $PYTHON. Ejecuta antes scripts/install_dev.sh" >&2
    exit 1
fi

echo "=== Comprobando importaciones ==="
"$PYTHON" -c "import lot_bot; from lot_bot.app import main; print('OK', lot_bot.__version__)"

echo "=== Empaquetando con PyInstaller ==="
"$PYTHON" -m PyInstaller build/LOT-Bot.spec --noconfirm --clean \
    --workpath build/work --distpath dist

echo "=== Resultado ==="
ls -lh dist/LOT-Bot/ | head -20
