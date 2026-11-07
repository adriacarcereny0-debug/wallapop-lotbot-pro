#!/usr/bin/env bash
# Comprobacion completa antes de entregar: estilo, pruebas y empaquetado.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
PYTHON="${PYTHON:-.venv/bin/python}"

echo "=== 1/3 · Estilo (ruff) ==="
if "$PYTHON" -m ruff --version >/dev/null 2>&1; then
    "$PYTHON" -m ruff check lot_bot tests
else
    echo "ruff no instalado; se omite."
fi

echo "=== 2/3 · Pruebas ==="
QT_QPA_PLATFORM=offscreen "$PYTHON" -m pytest -q

echo "=== 3/3 · Importacion de la aplicacion ==="
"$PYTHON" -c "from lot_bot.app import main; import lot_bot; print('OK', lot_bot.__version__)"

echo "Todo correcto."
