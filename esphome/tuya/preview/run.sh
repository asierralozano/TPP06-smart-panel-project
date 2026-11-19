#!/bin/sh
# Compila una configuración de previsualización (pantalla SDL headless), la
# ejecuta y deja una captura PNG por pantalla en OUT_DIR.
#
#   ESPHOME=/ruta/a/esphome sh esphome/tuya/preview/run.sh \
#       esphome/tuya/preview/tpp06-tuya-preview.yaml /tmp/shots
#
# Requisitos: ESPHome 2026.10.x, SDL2 y ImageMagick (`magick`).
set -eu
CONFIG=${1:?Uso: run.sh CONFIG.yaml OUT_DIR}
OUT=${2:?Uso: run.sh CONFIG.yaml OUT_DIR}
ESPHOME=${ESPHOME:-esphome}

"$ESPHOME" compile "$CONFIG" >"$OUT.build.log" 2>&1 || {
    echo "Fallo al compilar; ver $OUT.build.log" >&2
    grep -n -E 'error|Error|ERROR' "$OUT.build.log" | head -40 >&2
    exit 1
}
NAME=$(sed -n 's/^INFO Successfully compiled program to path .\(.*\).$/\1/p' "$OUT.build.log" | tail -1)
[ -x "$NAME" ] || { echo "No encuentro el programa compilado" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT"
ESPHOME_SNAPSHOT_DIR="$OUT" "$NAME" >"$OUT.run.log" 2>&1 &
PID=$!
i=0
while kill -0 "$PID" 2>/dev/null && [ "$i" -lt 60 ]; do sleep 1; i=$((i + 1)); done
kill "$PID" 2>/dev/null || true
grep -E '\[[EW]\]' "$OUT.run.log" | head -20 || true

for f in "$OUT"/*.bmp; do
    [ -e "$f" ] || continue
    magick "$f" "${f%.bmp}.png" && rm -f "$f"
done
ls "$OUT"
