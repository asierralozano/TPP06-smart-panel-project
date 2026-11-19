#!/bin/sh
# TPP06_CLOCKSYNC
# Fija el reloj del SO con la cabecera HTTP "Date" de un servidor (por defecto HA).
# Necesario porque este kernel Anyka no deja a ESPHome usar settimeofday(tz) y el
# panel no trae NTP. ESPHome aplica luego la zona horaria de HA sobre este reloj UTC.
URL=${TPP06_CLOCK_URL:-http://192.168.18.77:8123/}
INTERVAL=${TPP06_CLOCK_INTERVAL:-600}

sync_once() {
    d=$(wget -S -q -O /dev/null "$URL" 2>&1 | sed -n 's/^[[:space:]]*Date:[[:space:]]*//Ip' | head -1)
    [ -n "$d" ] || return 1
    # "Thu, 19 Nov 2026 23:32:49 GMT" -> "2026-11-19 23:32:49"
    fmt=$(echo "$d" | awk '{
        split("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec", m, " ");
        for (i=1;i<=12;i++) M[m[i]]=i;
        if ($4 ~ /^[0-9]{4}$/ && M[$3] != "") printf "%s-%02d-%02d %s", $4, M[$3], $2, $5
    }')
    [ -n "$fmt" ] || return 1
    date -u -s "$fmt" >/dev/null 2>&1
}

case "$1" in
    loop)
        # Espera a que haya red (reintenta ~2 min), luego sincroniza periodicamente.
        i=0
        while [ "$i" -lt 30 ]; do sync_once && break; sleep 4; i=$((i + 1)); done
        while true; do sleep "$INTERVAL"; sync_once; done ;;
    *)
        sync_once ;;
esac
