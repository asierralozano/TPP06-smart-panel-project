#!/bin/sh
# Copia, desde un firmware de Tuya ya extraído, SOLO las imágenes que usa la UI
# estilo Tuya (esphome/tuya/). Los assets son de Tuya (copyright de terceros):
# se quedan en local / en tu Home Assistant y NUNCA se suben a git.
#
# Uso:  sh tools/tpp06_tuya_assets.sh /ruta/a/tuya-fw/img [destino]
#       destino por defecto: esphome/tuya-assets  (ignorado por git)
# En Home Assistant: copia la carpeta resultante a /config/esphome/tuya-assets.
set -eu

SRC=${1:?Uso: sh tools/tpp06_tuya_assets.sh /ruta/a/tuya-fw/img [destino]}
DEST=${2:-$(dirname "$0")/../esphome/tuya-assets}

FILES="
theme/bg/00.png
icon/on.png
icon/off.png
icon/left.png
icon/right.png
icon/network.png
icon/brightness.png
icon/display.png
icon/dev_sys.png
icon/dev_info.png
icon/date_time.png
icon/5inch_volume.png
icon/menu_home.png
icon/menu_leave_home.png
icon/menu_in_home.png
icon/menu_shortcut.png
icon/menu_setting.png
weather/36/sunny.png
weather/36/sun_cloud.png
weather/36/cloud.png
weather/36/light_rain.png
weather/36/moderate_rain.png
weather/36/big_rain.png
weather/36/thundershower.png
weather/36/snow.png
weather/36/sleet.png
weather/36/hailstone.png
weather/36/haze.png
"

missing=0
for f in $FILES; do
    if [ ! -f "$SRC/$f" ]; then
        echo "FALTA: $SRC/$f" >&2
        missing=1
        continue
    fi
    mkdir -p "$DEST/$(dirname "$f")"
    cp "$SRC/$f" "$DEST/$f"
done
[ "$missing" = 0 ] || { echo "Algunos assets no existen; revisa la ruta del firmware." >&2; exit 1; }
echo "Assets copiados a $DEST"
