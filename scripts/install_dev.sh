#!/usr/bin/env bash
# Prepara el entorno de desarrollo de LOT Bot en Linux/macOS.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== LOT Bot · entorno de desarrollo ==="

if ! command -v python3 >/dev/null; then
    echo "Instala Python 3.11 o superior." >&2
    exit 1
fi

VERSION=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
echo "Python detectado: $VERSION"

if [ ! -d ".venv" ]; then
    echo "Creando entorno virtual..."
    python3 -m venv .venv
fi

echo "Instalando dependencias..."
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements-dev.txt

if [ ! -f ".env" ]; then
    echo "Creando .env a partir de .env.example..."
    cp .env.example .env
    echo "Edita .env con tus credenciales. Sin ellas, LOT Bot funciona en MODO DEMO."
fi

cat <<'MSG'

=== Listo ===
Arrancar la aplicación:   .venv/bin/python run_lot_bot.py
Ejecutar las pruebas:     .venv/bin/python -m pytest
Comprobar el empaquetado: ./build/build.sh

Nota: en Linux, Qt necesita algunas librerías del sistema:
  sudo apt-get install -y libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1
MSG
