#!/bin/sh
# Entrada del aprovisionador TPP06. Deja un panel nuevo igual que el de referencia.
#
#   ./tpp06-install.sh --config panel-salon.conf          # ejecuta la siguiente fase
#   ./tpp06-install.sh --config panel-salon.conf --status  # ver progreso
#   ./tpp06-install.sh --config panel-salon.conf --reset   # olvidar progreso
#
# Es multi-fase: varias fases terminan pidiendo un POWER-CYCLE del panel. Tras
# enchufarlo de nuevo, vuelve a ejecutar el MISMO comando y continúa donde iba.
HERE=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
    echo "ERROR: necesito python3 en el PATH." >&2
    exit 1
fi
if ! command -v adb >/dev/null 2>&1; then
    echo "AVISO: 'adb' no está en el PATH. Hace falta para las fases de backup/rescate." >&2
fi
exec "$PY" "$HERE/tpp06_provision.py" "$@"
