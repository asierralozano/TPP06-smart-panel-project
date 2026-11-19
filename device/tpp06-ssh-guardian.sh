#!/bin/sh
# TPP06_SSH_GUARDIAN_V1
# Guardián de rescate SSH del TPP06 (BusyBox ash). Documentación:
# docs/es/21-ssh-rescate.md
#
# Principios:
# - Independiente de Tuya y de ESPHome: lo lanzan el supervisor multiboot y el
#   lanzador de comisionado, pero no depende de ninguno de ellos.
# - Nunca mata, reconfigura ni "adopta" procesos que no haya lanzado él. Sus
#   procesos se reconocen por la ruta de SUS ficheros pid en la línea de órdenes.
# - Nunca bloquea el arranque: "start" deja el bucle en segundo plano y vuelve.
# - Nunca escribe credenciales en el registro.
#
# Uso: tpp06-ssh-guardian.sh {start|stop|status|run}

num_or() {
    case "$1" in ''|*[!0-9]*) echo "$2" ;; *) echo "$1" ;; esac
}

BASE=${TPP06_SSH_BASE:-/data/tpp06-ssh}
RUN_DIR=${TPP06_SSH_RUN_DIR:-$BASE/run}
PROC_ROOT=${TPP06_SSH_PROC_ROOT:-/proc}
SYS_ROOT=${TPP06_SSH_SYS_ROOT:-/sys}
GADGET_ROOT=${TPP06_SSH_GADGET_ROOT:-$SYS_ROOT/kernel/config/usb_gadget}
UDC_ROOT=${TPP06_SSH_UDC_ROOT:-$SYS_ROOT/class/udc}
IFACE=${TPP06_SSH_IFACE:-wlan0}
SSH_PORT=$(num_or "${TPP06_SSH_PORT:-}" 22)
INTERVAL=$(num_or "${TPP06_SSH_INTERVAL:-}" 30)
MAX_CYCLES=$(num_or "${TPP06_SSH_MAX_CYCLES:-}" 0)
NET_GRACE=$(num_or "${TPP06_SSH_NET_GRACE_SECONDS:-}" 300)
USB_GRACE=$(num_or "${TPP06_SSH_USB_GRACE_SECONDS:-}" 60)
BACKOFF_MAX=$(num_or "${TPP06_SSH_BACKOFF_MAX:-}" 600)
STABLE_SECONDS=$(num_or "${TPP06_SSH_STABLE_SECONDS:-}" 120)
LOG_LIMIT=$(num_or "${TPP06_SSH_LOG_LIMIT:-}" 131072)
HOST_KEY=${TPP06_SSH_HOST_KEY:-$BASE/host_ed25519}
WIFI_PROFILES=${TPP06_SSH_WIFI_PROFILES:-/data/tpp06-cutter/network/wpa_supplicant.conf $BASE/wifi.conf}
DHCP_SCRIPT=${TPP06_SSH_DHCP_SCRIPT:-/usr/share/udhcpc/default.script}
WPA_DRIVER=${TPP06_SSH_WPA_DRIVER:-nl80211}
[ "$INTERVAL" -ge 1 ] || INTERVAL=1

LOG_FILE=$BASE/guardian.log
DROPBEAR_LOG=$BASE/dropbear.log
WIFI_LOG=$BASE/wifi.log
LOCK=$RUN_DIR/guardian.lock
DROPBEAR_PID_FILE=$RUN_DIR/dropbear.pid
WPA_PID_FILE=$RUN_DIR/wpa_supplicant.pid
DHCP_PID_FILE=$RUN_DIR/udhcpc.pid

case "$0" in
    /*) SELF=$0 ;;
    *) SELF=$(pwd)/$0 ;;
esac
SELF_NAME=${0##*/}

# Estado del bucle (memoria del proceso "run").
db_child=
db_attempts=0
db_next_try=0
db_launched_at=0
keygen_pid=
net_attempts=0
net_next_try=0
note_ssh=
note_net=
note_usb=

uptime_seconds() {
    us_value=$(cut -d. -f1 "$PROC_ROOT/uptime" 2>/dev/null)
    # Ilegible = 0: nunca adelanta la red de último recurso.
    num_or "$us_value" 0
}

boot_id() {
    bi_value=$(cat "$PROC_ROOT/sys/kernel/random/boot_id" 2>/dev/null)
    echo "${bi_value:-unknown}"
}

log() {
    mkdir -p "$BASE" 2>/dev/null
    if [ -f "$LOG_FILE" ]; then
        log_size=$(wc -c <"$LOG_FILE" 2>/dev/null | tr -d ' ')
        log_size=$(num_or "$log_size" 0)
        [ "$log_size" -gt "$LOG_LIMIT" ] && mv -f "$LOG_FILE" "$LOG_FILE.previous"
    fi
    echo "$(date '+%Y-%m-%dT%H:%M:%S' 2>/dev/null) up=$(uptime_seconds) $*" >>"$LOG_FILE" 2>/dev/null
}

# Registra solo cuando cambia el estado de un apartado (evita spam cada 30 s).
note() {
    case "$1" in
        ssh) [ "$note_ssh" = "$2" ] && return 0; note_ssh=$2 ;;
        net) [ "$note_net" = "$2" ] && return 0; note_net=$2 ;;
        usb) [ "$note_usb" = "$2" ] && return 0; note_usb=$2 ;;
    esac
    log "$2"
}

trim_log() {
    # Copia/trunca: los demonios abren con O_APPEND y siguen escribiendo en el
    # mismo descriptor; renombrar dejaría creciendo el inodo antiguo.
    [ -f "$1" ] && [ ! -L "$1" ] || return 0
    tl_size=$(wc -c <"$1" 2>/dev/null | tr -d ' ')
    tl_size=$(num_or "$tl_size" 0)
    [ "$tl_size" -gt "$2" ] || return 0
    tail -c 16384 "$1" >"$1.previous" 2>/dev/null
    : >"$1"
}

maintain_logs() {
    trim_log "$DROPBEAR_LOG" 65536
    trim_log "$WIFI_LOG" 65536
}

backoff_delay() {
    bd_delay=$INTERVAL
    bd_i=1
    while [ "$bd_i" -lt "$1" ] && [ "$bd_delay" -lt "$BACKOFF_MAX" ]; do
        bd_delay=$((bd_delay * 2))
        bd_i=$((bd_i + 1))
    done
    [ "$bd_delay" -le "$BACKOFF_MAX" ] || bd_delay=$BACKOFF_MAX
    echo "$bd_delay"
}

# --- Procesos ---------------------------------------------------------------

valid_pid() {
    case "$1" in ''|*[!0-9]*|0) return 1 ;; esac
    return 0
}

proc_alive() {
    # Existe y no es zombi (un zombi responde a kill -0).
    valid_pid "$1" || return 1
    [ -d "$PROC_ROOT/$1" ] || return 1
    pa_state=$(sed -n 's/^.*) \([A-Za-z]\).*$/\1/p' "$PROC_ROOT/$1/stat" 2>/dev/null)
    case "$pa_state" in Z|X|x) return 1 ;; esac
    return 0
}

proc_has_arg() {
    # proc_has_arg <pid> <subcadena fija de algún argumento>
    [ -r "$PROC_ROOT/$1/cmdline" ] || return 1
    tr '\000' '\n' <"$PROC_ROOT/$1/cmdline" 2>/dev/null | grep -Fq -- "$2"
}

owned_pid() {
    # owned_pid <fichero pid> <marca>: imprime el pid si es un proceso vivo
    # lanzado por este guardián (su línea de órdenes contiene la marca).
    op_pid=$(sed -n '1p' "$1" 2>/dev/null)
    proc_alive "$op_pid" && proc_has_arg "$op_pid" "$2" || return 1
    echo "$op_pid"
}

# --- Instancia única ----------------------------------------------------------
# El cerrojo es un enlace simbólico "pid:boot_id" (creación atómica con ln -s).

lock_holder_valid() {
    lh_value=$(readlink "$LOCK" 2>/dev/null) || return 1
    holder_pid=${lh_value%%:*}
    [ "${lh_value#*:}" = "$(boot_id)" ] || return 1
    proc_alive "$holder_pid" || return 1
    proc_has_arg "$holder_pid" "$SELF_NAME" || return 1
    tr '\000' '\n' <"$PROC_ROOT/$holder_pid/cmdline" 2>/dev/null | grep -Fxq run
}

acquire_lock() {
    mkdir -p "$RUN_DIR" 2>/dev/null
    my_token="$$:$(boot_id)"
    al_try=0
    while [ "$al_try" -lt 2 ]; do
        ln -s "$my_token" "$LOCK" 2>/dev/null && return 0
        lock_holder_valid && return 1
        # Cerrojo de otro arranque o de un proceso muerto.
        rm -f "$LOCK"
        al_try=$((al_try + 1))
    done
    return 1
}

lock_is_mine() {
    [ "$(readlink "$LOCK" 2>/dev/null)" = "$my_token" ]
}

release_lock() {
    lock_is_mine && rm -f "$LOCK"
}

# --- SSH (dropbear) -------------------------------------------------------------

auth_mode() {
    am_keys=no
    if [ -f "$BASE/authorized_keys" ] && grep -Eq \
        '(^|[[:space:]])(ssh-(ed25519|rsa|dss)|ecdsa-sha2-[a-z0-9]+|sk-[a-z0-9@.-]+)[[:space:]]+[A-Za-z0-9+/=]+' \
        "$BASE/authorized_keys" 2>/dev/null; then
        am_keys=yes
    fi
    if [ -e "$BASE/allow_password" ]; then
        [ "$am_keys" = yes ] && echo keys+password || echo password
    else
        [ "$am_keys" = yes ] && echo keys || echo none
    fi
}

dropbear_resolve() {
    # Prioridad: TPP06_SSH_DROPBEAR, $BASE/dropbear_path (reutilizar un dropbear
    # ya funcional en otra ruta), $BASE/dropbearmulti, $BASE/dropbear.
    DB_BIN=${TPP06_SSH_DROPBEAR:-}
    if [ -z "$DB_BIN" ] && [ -f "$BASE/dropbear_path" ]; then
        DB_BIN=$(sed -n '1p' "$BASE/dropbear_path" 2>/dev/null)
    fi
    if [ -z "$DB_BIN" ]; then
        if [ -x "$BASE/dropbearmulti" ]; then
            DB_BIN=$BASE/dropbearmulti
        elif [ -x "$BASE/dropbear" ]; then
            DB_BIN=$BASE/dropbear
        fi
    fi
    case "$DB_BIN" in /*) ;; *) return 1 ;; esac
    [ -f "$DB_BIN" ] && [ -x "$DB_BIN" ] || return 1
    case "${DB_BIN##*/}" in dropbearmulti*) DB_MULTI=yes ;; *) DB_MULTI=no ;; esac
    return 0
}

listen_interface() {
    # Opcional: $BASE/listen_interface con un nombre (p. ej. wlan0) -> "-l".
    [ -f "$BASE/listen_interface" ] || return 1
    li_name=$(sed -n '1p' "$BASE/listen_interface" 2>/dev/null)
    case "$li_name" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
    echo "$li_name"
}

port_listening() {
    netstat -lnt 2>/dev/null | grep -q ":$1 "
}

key_perm_warnings() {
    # dropbear rechaza authorized_keys si el fichero o algún directorio padre
    # es escribible por grupo/otros. Solo aviso; no cambia permisos ajenos.
    kp_path=$BASE/authorized_keys
    [ -e "$kp_path" ] || return 0
    while :; do
        kp_mode=$(ls -ld "$kp_path" 2>/dev/null | cut -c1-10)
        case "$kp_mode" in
            l*) ;;
            ?????w????|????????w?)
                log "WARNING $kp_path es escribible por grupo/otros: dropbear rechazará authorized_keys" ;;
        esac
        [ "$kp_path" = / ] && break
        kp_path=${kp_path%/*}
        [ -n "$kp_path" ] || kp_path=/
    done
}

ensure_host_key() {
    [ -s "$HOST_KEY" ] && return 0
    if [ -n "$keygen_pid" ]; then
        if proc_alive "$keygen_pid"; then
            note ssh "esperando a la generación de la clave de host"
            return 1
        fi
        wait "$keygen_pid" 2>/dev/null
        keygen_pid=
        if [ -s "$HOST_KEY.new" ]; then
            chmod 600 "$HOST_KEY.new" 2>/dev/null
            if mv -f "$HOST_KEY.new" "$HOST_KEY"; then
                sync
                log "clave de host generada: $HOST_KEY $(grep -i 'fingerprint' "$RUN_DIR/keygen.log" 2>/dev/null | head -n 1)"
                return 0
            fi
        fi
        rm -f "$HOST_KEY.new"
        log "ERROR no se pudo generar la clave de host (ver $RUN_DIR/keygen.log)"
        db_attempts=$((db_attempts + 1))
        db_next_try=$(($(uptime_seconds) + $(backoff_delay "$db_attempts")))
        return 1
    fi
    if [ "$DB_MULTI" = yes ]; then
        set -- "$DB_BIN" dropbearkey
    else
        hk_gen=${DB_BIN%/*}/dropbearkey
        [ -x "$hk_gen" ] || hk_gen=$(command -v dropbearkey 2>/dev/null)
        if [ -z "$hk_gen" ] || [ ! -x "$hk_gen" ]; then
            note ssh "ERROR falta $HOST_KEY y no hay dropbearkey para generarla"
            return 1
        fi
        set -- "$hk_gen"
    fi
    rm -f "$HOST_KEY.new"
    # En segundo plano: con poca entropía al arrancar getrandom() puede tardar
    # y el bucle (USB/red) no debe quedarse esperando.
    (umask 077; exec "$@" -t ed25519 -f "$HOST_KEY.new") \
        </dev/null >"$RUN_DIR/keygen.log" 2>&1 &
    keygen_pid=$!
    log "generando clave de host ed25519 en segundo plano pid=$keygen_pid"
    return 1
}

ssh_tick() {
    now=$(uptime_seconds)
    if db_pid=$(owned_pid "$DROPBEAR_PID_FILE" "$DROPBEAR_PID_FILE"); then
        if [ "$db_attempts" -gt 0 ] && [ $((now - db_launched_at)) -ge "$STABLE_SECONDS" ]; then
            db_attempts=0
        fi
        note ssh "dropbear en marcha pid=$db_pid puerto=$SSH_PORT"
        return 0
    fi
    if [ -n "$db_child" ]; then
        wait "$db_child" 2>/dev/null   # recoge el zombi de nuestro hijo
        log "dropbear pid=$db_child ha terminado; se relanzará con espera progresiva"
        db_child=
    fi
    if port_listening "$SSH_PORT"; then
        note ssh "puerto $SSH_PORT ya escucha (otro servidor SSH): no se lanza dropbear"
        return 0
    fi
    auth=$(auth_mode)
    if [ "$auth" = none ]; then
        note ssh "dropbear NO lanzado: no hay $BASE/authorized_keys ni $BASE/allow_password"
        return 0
    fi
    if ! dropbear_resolve; then
        note ssh "dropbear NO lanzado: no hay binario ejecutable (dropbear_path, dropbearmulti o dropbear en $BASE)"
        return 0
    fi
    [ "$now" -ge "$db_next_try" ] || return 0
    ensure_host_key || return 0

    # -E (log a stderr) no existe en todos los builds de dropbear: algunos abortan
    # con "Invalid option -E". Usarlo solo si el binario lo acepta.
    db_logopt=
    if [ "$DB_MULTI" = yes ]; then
        "$DB_BIN" dropbear -E -V >/dev/null 2>&1 && db_logopt=-E
    else
        "$DB_BIN" -E -V >/dev/null 2>&1 && db_logopt=-E
    fi
    set -- -F $db_logopt -p "0.0.0.0:$SSH_PORT" -r "$HOST_KEY" -P "$DROPBEAR_PID_FILE" \
        -D "$BASE" -K 60
    # Solo clave por defecto; contraseña únicamente con $BASE/allow_password.
    [ "$auth" = keys ] && set -- "$@" -s -g
    if db_iface=$(listen_interface); then
        set -- "$@" -l "$db_iface"
    fi
    [ "$DB_MULTI" = yes ] && set -- dropbear "$@"
    mkdir -p "$RUN_DIR" 2>/dev/null
    "$DB_BIN" "$@" </dev/null >>"$DROPBEAR_LOG" 2>&1 &
    db_child=$!
    echo "$db_child" >"$DROPBEAR_PID_FILE"
    db_attempts=$((db_attempts + 1))
    db_launched_at=$now
    db_next_try=$((now + $(backoff_delay "$db_attempts")))
    note_ssh=
    log "dropbear lanzado pid=$db_child bin=$DB_BIN puerto=$SSH_PORT auth=$auth intento=$db_attempts"
}

# --- ADB por USB (configfs) -------------------------------------------------------

usb_tick() {
    [ -e "$BASE/usb_disabled" ] && return 0
    [ -d "$GADGET_ROOT" ] || return 0
    [ "$(uptime_seconds)" -ge "$USB_GRACE" ] || return 0
    ub_used=" "
    for ub_gadget in "$GADGET_ROOT"/*; do
        [ -f "$ub_gadget/UDC" ] || continue
        ub_udc=$(cat "$ub_gadget/UDC" 2>/dev/null)
        [ -n "$ub_udc" ] && ub_used="$ub_used$ub_udc "
    done
    ub_target=
    for ub_gadget in "$GADGET_ROOT"/*; do
        [ -f "$ub_gadget/UDC" ] || continue
        [ -z "$(cat "$ub_gadget/UDC" 2>/dev/null)" ] || continue
        # Solo gadgets ADB (functionfs), nunca otras funciones USB.
        for ub_function in "$ub_gadget"/functions/ffs.*; do
            [ -e "$ub_function" ] && ub_target=$ub_gadget
        done
        [ -n "$ub_target" ] && break
    done
    if [ -z "$ub_target" ]; then
        [ "$ub_used" = " " ] || note usb "USB: gadget atado ($ub_used)"
        return 0
    fi
    ub_free=
    for ub_controller in "$UDC_ROOT"/*; do
        [ -e "$ub_controller" ] || continue
        ub_name=${ub_controller##*/}
        case "$ub_used" in *" $ub_name "*) continue ;; esac
        ub_free=$ub_name
        break
    done
    if [ -z "$ub_free" ]; then
        note usb "USB: gadget ${ub_target##*/} sin UDC y ningún UDC libre"
        return 0
    fi
    if echo "$ub_free" >"$ub_target/UDC" 2>/dev/null; then
        note usb "USB: gadget ${ub_target##*/} atado a UDC $ub_free (ADB por USB)"
    else
        note usb "USB: no se pudo atar ${ub_target##*/} a $ub_free (se reintentará)"
    fi
}

# --- Red de último recurso ----------------------------------------------------------

wlan_ipv4() {
    # Misma regla que el supervisor: 0.0.0.0, 127/8, 169.254/16 y >=224 no valen.
    ifconfig "$IFACE" 2>/dev/null | awk '
        { for (i=1; i<NF; i++) if ($i == "inet") {
            address=$(i+1); sub(/^addr:/, "", address)
            count=split(address, octets, "."); valid=(count == 4)
            for (j=1; j<=count; j++)
                if (octets[j] !~ /^[0-9]+$/ || octets[j] > 255) valid=0
            if (valid && octets[1] > 0 && octets[1] < 224 && octets[1] != 127 &&
                !(octets[1] == 169 && octets[2] == 254) && found == "") found=address
        } }
        END { if (found == "") exit 1; print found }'
}

profile_valid() {
    [ -f "$1" ] && [ -s "$1" ] &&
        grep -q '^[[:space:]]*network[[:space:]]*=[[:space:]]*{' "$1" &&
        grep -q 'ssid[[:space:]]*=' "$1" &&
        { grep -q 'psk[[:space:]]*=' "$1" ||
          grep -q 'key_mgmt[[:space:]]*=[[:space:]]*NONE' "$1"; }
}

pick_profile() {
    for pp_candidate in $WIFI_PROFILES; do
        if profile_valid "$pp_candidate"; then
            echo "$pp_candidate"
            return 0
        fi
    done
    return 1
}

foreign_network_process() {
    # Cualquier wpa_supplicant/udhcpc/hostapd que no lleve nuestra marca es
    # ajeno (Tuya, multiboot, editor Wi-Fi...). Si no se puede leer, es ajeno.
    for fn_name in wpa_supplicant udhcpc hostapd; do
        for fn_pid in $(pidof "$fn_name" 2>/dev/null); do
            proc_has_arg "$fn_pid" "$RUN_DIR/" && continue
            foreign_name=$fn_name
            return 0
        done
    done
    return 1
}

network_tick() {
    if [ -e "$BASE/network_disabled" ]; then
        note net "red de último recurso desactivada ($BASE/network_disabled)"
        return 0
    fi
    now=$(uptime_seconds)
    if nt_address=$(wlan_ipv4); then
        net_attempts=0
        note net "red: $IFACE tiene IPv4 $nt_address"
        return 0
    fi
    # Margen de arranque: Tuya o el supervisor montan su red en los primeros
    # ~60-110 s. Antes del margen el guardián nunca toca la red.
    [ "$now" -ge "$NET_GRACE" ] || return 0
    if foreign_network_process; then
        note net "red: $IFACE sin IPv4 pero hay un $foreign_name ajeno; no se toca"
        return 0
    fi
    nt_wpa=$(owned_pid "$WPA_PID_FILE" "$WPA_PID_FILE") || nt_wpa=
    nt_dhcp=$(owned_pid "$DHCP_PID_FILE" "$DHCP_PID_FILE") || nt_dhcp=
    if [ -n "$nt_wpa" ] && [ -n "$nt_dhcp" ]; then
        note net "red: wpa_supplicant/udhcpc propios en marcha, esperando asociación/concesión"
        return 0
    fi
    [ "$now" -ge "$net_next_try" ] || return 0
    if ! nt_profile=$(pick_profile); then
        note net "red: sin IPv4 ni demonios Wi-Fi, pero no hay perfil Wi-Fi válido en: $WIFI_PROFILES"
        return 0
    fi
    for nt_program in ifconfig wpa_supplicant udhcpc; do
        if ! command -v "$nt_program" >/dev/null 2>&1; then
            note net "ERROR red: falta $nt_program"
            return 0
        fi
    done
    if [ ! -x "$DHCP_SCRIPT" ]; then
        note net "ERROR red: falta el script DHCP $DHCP_SCRIPT"
        return 0
    fi
    net_attempts=$((net_attempts + 1))
    net_next_try=$((now + $(backoff_delay "$net_attempts")))
    note_net=
    log "RED DE ÚLTIMO RECURSO: uptime=${now}s, $IFACE sin IPv4 y sin wpa_supplicant/udhcpc; se arranca Wi-Fi propia con el perfil $nt_profile (intento $net_attempts)"
    mkdir -p "$RUN_DIR" 2>/dev/null
    ifconfig "$IFACE" up >>"$WIFI_LOG" 2>&1 || log "AVISO ifconfig $IFACE up ha fallado"
    if [ -z "$nt_wpa" ]; then
        rm -f "$WPA_PID_FILE"
        if ! wpa_supplicant -B -P"$WPA_PID_FILE" -D"$WPA_DRIVER" -i"$IFACE" \
            -c"$nt_profile" >>"$WIFI_LOG" 2>&1; then
            log "ERROR red: wpa_supplicant no arrancó (ver $WIFI_LOG)"
            return 0
        fi
    fi
    if [ -z "$nt_dhcp" ]; then
        rm -f "$DHCP_PID_FILE"
        if ! udhcpc -b -t 8 -p "$DHCP_PID_FILE" -i "$IFACE" -s "$DHCP_SCRIPT" \
            >>"$WIFI_LOG" 2>&1; then
            log "ERROR red: udhcpc no arrancó (ver $WIFI_LOG)"
        fi
    fi
}

# --- Bucle y órdenes ------------------------------------------------------------------

guardian_sleep() {
    # sleep + wait: una señal TERM se atiende sin esperar al final del sleep.
    sleep "$INTERVAL" &
    wait $! 2>/dev/null
}

guardian_on_signal() {
    release_lock
    log "guardián detenido por señal pid=$$"
    exit 0
}

guardian_run() {
    umask 077
    cd / 2>/dev/null
    mkdir -p "$BASE" "$RUN_DIR" 2>/dev/null
    acquire_lock || exit 0
    trap '' HUP
    trap guardian_on_signal TERM INT
    log "guardián iniciado pid=$$ intervalo=${INTERVAL}s margen_red=${NET_GRACE}s margen_usb=${USB_GRACE}s auth=$(auth_mode)"
    key_perm_warnings
    cycles=0
    while :; do
        if ! lock_is_mine; then
            log "otra instancia tiene el cerrojo; esta termina pid=$$"
            exit 0
        fi
        maintain_logs
        ssh_tick
        usb_tick
        network_tick
        cycles=$((cycles + 1))
        if [ "$MAX_CYCLES" -gt 0 ] && [ "$cycles" -ge "$MAX_CYCLES" ]; then
            break
        fi
        guardian_sleep
    done
    release_lock
}

guardian_start() {
    if lock_holder_valid; then
        echo "guardián ya en marcha pid=$holder_pid"
        return 0
    fi
    mkdir -p "$RUN_DIR" 2>/dev/null
    sh "$SELF" run </dev/null >/dev/null 2>&1 &
    echo "guardián lanzado pid=$!"
    return 0
}

guardian_stop() {
    if lock_holder_valid; then
        gs_holder=$(readlink "$LOCK" 2>/dev/null)
        kill "$holder_pid" 2>/dev/null
        sleep 1
        [ "$(readlink "$LOCK" 2>/dev/null)" = "$gs_holder" ] && rm -f "$LOCK"
        echo "guardián detenido pid=$holder_pid"
    else
        echo "guardián no estaba en marcha"
    fi
    if gs_db=$(owned_pid "$DROPBEAR_PID_FILE" "$DROPBEAR_PID_FILE"); then
        kill "$gs_db" 2>/dev/null
        echo "dropbear propio detenido pid=$gs_db (las sesiones abiertas siguen)"
    fi
    rm -f "$DROPBEAR_PID_FILE"
    # La Wi-Fi propia (si la hubo) se deja en marcha a propósito: detenerla
    # podría cortar la sesión desde la que se ejecuta esta orden.
    log "stop: guardián y dropbear propio detenidos; Wi-Fi propia (si la hay) intacta"
    return 0
}

guardian_status() {
    if lock_holder_valid; then
        echo "guardian=running pid=$holder_pid"
        gst_rc=0
    else
        echo "guardian=stopped"
        gst_rc=1
    fi
    if gst_db=$(owned_pid "$DROPBEAR_PID_FILE" "$DROPBEAR_PID_FILE"); then
        echo "dropbear=running pid=$gst_db port=$SSH_PORT"
    elif port_listening "$SSH_PORT"; then
        echo "dropbear=not-owned (puerto $SSH_PORT en uso por otro servicio)"
    else
        echo "dropbear=stopped"
    fi
    echo "auth=$(auth_mode)"
    if dropbear_resolve; then echo "binary=$DB_BIN"; else echo "binary=missing"; fi
    if [ -s "$HOST_KEY" ]; then echo "host_key=present"; else echo "host_key=missing"; fi
    if gst_ip=$(wlan_ipv4); then echo "network=$IFACE ipv4=$gst_ip"; else echo "network=$IFACE no-ipv4"; fi
    gst_wpa=$(owned_pid "$WPA_PID_FILE" "$WPA_PID_FILE") || gst_wpa=no
    gst_dhcp=$(owned_pid "$DHCP_PID_FILE" "$DHCP_PID_FILE") || gst_dhcp=no
    echo "own_wpa_supplicant=$gst_wpa own_udhcpc=$gst_dhcp"
    for gst_gadget in "$GADGET_ROOT"/*; do
        [ -f "$gst_gadget/UDC" ] || continue
        gst_udc=$(cat "$gst_gadget/UDC" 2>/dev/null)
        echo "usb_gadget=${gst_gadget##*/} udc=${gst_udc:-none}"
    done
    echo "uptime=$(uptime_seconds) net_grace=$NET_GRACE usb_grace=$USB_GRACE"
    return "$gst_rc"
}

case "$1" in
    start) guardian_start ;;
    stop) guardian_stop ;;
    status) guardian_status ;;
    run) guardian_run ;;
    *) echo "Uso: $0 {start|stop|status|run}" >&2; exit 2 ;;
esac
