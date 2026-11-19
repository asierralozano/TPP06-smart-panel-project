#!/bin/sh
# TPP06_ESPHOME_PERMANENT
# Lanzador PERMANENTE de ESPHome. Se coloca como /etc/init.d/S99tuya-app (overlay),
# sombreando el Tuya stock. En CADA arranque: levanta WiFi (Tuya no lo hace en
# reemplazo), sincroniza el reloj, y lanza ESPHome. NO revierte a Tuya.
#
# Rescate siempre disponible (arrancan antes, en S50adbd): ADB por USB + guardian SSH.
# Fallback: si falta/no es ejecutable el binario, arranca el Tuya stock.
# Revertir a Tuya a mano: cp S99tuya-app.stock /etc/init.d/S99tuya-app  (por SSH/USB).
BIN=/data/tpp06-home/program
STOCK=/data/tpp06-home/S99tuya-app.stock
LOG=/data/tpp06-home/boot.log

start() {
    echo "$(cut -d. -f1 /proc/uptime 2>/dev/null)s start" >> "$LOG" 2>/dev/null
    # WiFi con el perfil de Tuya (persistente en /data).
    ifconfig wlan0 up 2>/dev/null
    [ -f /tuya/data/wpa_0_8.conf ] && \
        wpa_supplicant -B -i wlan0 -D nl80211 -c /tuya/data/wpa_0_8.conf >>"$LOG" 2>&1
    udhcpc -b -t 8 -i wlan0 -s /usr/share/udhcpc/default.script >>"$LOG" 2>&1
    # Reloj (boot + periodico) en segundo plano; no bloquea el arranque.
    [ -x /data/tpp06-home/clocksync.sh ] && \
        nohup sh /data/tpp06-home/clocksync.sh loop >>"$LOG" 2>&1 &
    # ESPHome permanente, o fallback a Tuya si el binario no esta.
    if [ -x "$BIN" ]; then
        mkdir -p /tmp/tpp06-prefs
        ESPHOME_PREFDIR=/tmp/tpp06-prefs start-stop-daemon -S -q -b -x "$BIN"
        echo "esphome lanzado" >> "$LOG" 2>/dev/null
    elif [ -f "$STOCK" ]; then
        echo "FALLBACK: binario ausente -> Tuya stock" >> "$LOG" 2>/dev/null
        sh "$STOCK" start
    fi
}

case "$1" in
    start) start ;;
    *) ;;
esac
exit 0
