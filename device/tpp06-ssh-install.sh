#!/bin/sh
# TPP06_SSH_INSTALL_V1
# Instala el guardián de rescate SSH en /data/tpp06-ssh. Se ejecuta EN el panel
# como root (por ADB o SSH). No reinicia nada, no toca rc.local, S99tuya-app,
# la NAND ni /etc/shadow. Documentación: docs/es/21-ssh-rescate.md

usage() {
    cat <<'EOF'
Uso: sh tpp06-ssh-install.sh [opciones]

  --guardian FICHERO     guardián a instalar (por defecto, junto a este script)
  --dropbear FICHERO     copia un binario dropbear/dropbearmulti a la base
  --use-dropbear RUTA    reutiliza un dropbear ya funcional en otra ruta absoluta
  --pubkey FICHERO.pub   añade la(s) clave(s) pública(s) a authorized_keys
  --allow-password       permite contraseña root (ver riesgos en la guía)
  --deny-password        vuelve a "solo clave" (borra allow_password)
  --start                (re)arranca el guardián ahora
  -h, --help             esta ayuda
EOF
}

die() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "AVISO: $*" >&2; }

BASE=${TPP06_SSH_BASE:-/data/tpp06-ssh}
LAUNCHER=${TPP06_SSH_LAUNCHER:-/data/etc/init.d/S99tuya-app}
SRC_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
GUARDIAN_SRC=$SRC_DIR/tpp06-ssh-guardian.sh
DROPBEAR_SRC=
DROPBEAR_REF=
PUBKEY_SRC=
PASSWORD=keep
DO_START=no
KEY_REGEX='^(.*[[:space:]])?(ssh-(ed25519|rsa|dss)|ecdsa-sha2-[a-z0-9]+|sk-[a-z0-9@.-]+)[[:space:]]+[A-Za-z0-9+/=]+([[:space:]].*)?$'

while [ $# -gt 0 ]; do
    case "$1" in
        --guardian|--dropbear|--use-dropbear|--pubkey)
            [ $# -ge 2 ] || die "falta el valor de $1"
            case "$1" in
                --guardian) GUARDIAN_SRC=$2 ;;
                --dropbear) DROPBEAR_SRC=$2 ;;
                --use-dropbear) DROPBEAR_REF=$2 ;;
                --pubkey) PUBKEY_SRC=$2 ;;
            esac
            shift 2 ;;
        --allow-password) PASSWORD=allow; shift ;;
        --deny-password) PASSWORD=deny; shift ;;
        --start) DO_START=yes; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "opción desconocida: $1" ;;
    esac
done
[ -z "$DROPBEAR_SRC" ] || [ -z "$DROPBEAR_REF" ] ||
    die "--dropbear y --use-dropbear son excluyentes"

if [ "${TPP06_SSH_INSTALL_ALLOW_NONROOT:-}" != 1 ]; then
    if command -v id >/dev/null 2>&1; then
        [ "$(id -u)" = 0 ] || die "ejecutar como root en el panel"
    else
        warn "no se puede comprobar el usuario (falta id)"
    fi
fi

abs_path() {
    ap_dir=$(cd "$(dirname "$1")" 2>/dev/null && pwd) || return 1
    echo "$ap_dir/${1##*/}"
}

install_file() {
    # install_file <origen> <destino> <modo>: copia + renombrado atómico. Un
    # binario en ejecución conserva su inodo antiguo; nunca se sobrescribe.
    if [ "$(abs_path "$1")" = "$(abs_path "$2")" ]; then
        chmod "$3" "$2" || die "chmod $2"
        return 0
    fi
    rm -f "$2.new"
    if cp "$1" "$2.new" && chmod "$3" "$2.new" && mv -f "$2.new" "$2"; then
        return 0
    fi
    rm -f "$2.new"
    die "no se pudo instalar $2"
}

check_dropbear_runs() {
    # check_dropbear_runs <binario>: -V detecta arquitectura/formato erróneos.
    # Un -V correcto NO prueba compatibilidad con el kernel (ver guía FR, time64).
    case "${1##*/}" in
        dropbearmulti*) "$1" dropbear -V >"$BASE/.dropbear-version" 2>&1 ;;
        *) "$1" -V >"$BASE/.dropbear-version" 2>&1 ;;
    esac
    cd_rc=$?
    head -n 2 "$BASE/.dropbear-version" 2>/dev/null
    rm -f "$BASE/.dropbear-version"
    case "$cd_rc" in
        126|127) return 1 ;;
    esac
    [ "$cd_rc" = 0 ] || warn "$1 -V devolvió $cd_rc"
    return 0
}

umask 077

# 1. Guardián
[ -f "$GUARDIAN_SRC" ] || die "no se encuentra el guardián: $GUARDIAN_SRC"
grep -q TPP06_SSH_GUARDIAN_V1 "$GUARDIAN_SRC" || die "$GUARDIAN_SRC no es el guardián TPP06"
sh -n "$GUARDIAN_SRC" || die "sintaxis inválida en $GUARDIAN_SRC"
mkdir -p "$BASE" || die "no se puede crear $BASE"
chmod 700 "$BASE" || die "chmod $BASE"
install_file "$GUARDIAN_SRC" "$BASE/tpp06-ssh-guardian.sh" 700
sh -n "$BASE/tpp06-ssh-guardian.sh" || die "guardián instalado con sintaxis inválida"
echo "guardián instalado: $BASE/tpp06-ssh-guardian.sh"

# 2. Dropbear (opcional)
if [ -n "$DROPBEAR_SRC" ]; then
    [ -f "$DROPBEAR_SRC" ] && [ -s "$DROPBEAR_SRC" ] || die "no existe $DROPBEAR_SRC"
    case "${DROPBEAR_SRC##*/}" in
        dropbearmulti*) db_dest=$BASE/dropbearmulti ;;
        *) db_dest=$BASE/dropbear ;;
    esac
    [ "$(head -c 4 "$DROPBEAR_SRC" 2>/dev/null | tail -c 3)" = ELF ] ||
        warn "$DROPBEAR_SRC no parece un ejecutable ELF"
    if [ "$(abs_path "$DROPBEAR_SRC")" != "$(abs_path "$db_dest")" ]; then
        rm -f "$db_dest.new"
        cp "$DROPBEAR_SRC" "$db_dest.new" && chmod 700 "$db_dest.new" ||
            { rm -f "$db_dest.new"; die "no se pudo copiar $DROPBEAR_SRC"; }
        if ! check_dropbear_runs "$db_dest.new"; then
            rm -f "$db_dest.new"
            die "$DROPBEAR_SRC no se puede ejecutar en este panel (arquitectura/formato)"
        fi
        mv -f "$db_dest.new" "$db_dest" || die "no se pudo instalar $db_dest"
    else
        chmod 700 "$db_dest" || die "chmod $db_dest"
        check_dropbear_runs "$db_dest" || die "$db_dest no se puede ejecutar"
    fi
    rm -f "$BASE/dropbear_path"
    echo "dropbear instalado: $db_dest"
elif [ -n "$DROPBEAR_REF" ]; then
    case "$DROPBEAR_REF" in /*) ;; *) die "--use-dropbear requiere una ruta absoluta" ;; esac
    [ -f "$DROPBEAR_REF" ] && [ -x "$DROPBEAR_REF" ] || die "$DROPBEAR_REF no es ejecutable"
    check_dropbear_runs "$DROPBEAR_REF" || die "$DROPBEAR_REF no se puede ejecutar"
    printf '%s\n' "$DROPBEAR_REF" >"$BASE/dropbear_path.new" &&
        mv -f "$BASE/dropbear_path.new" "$BASE/dropbear_path" || die "no se pudo escribir dropbear_path"
    echo "dropbear reutilizado: $DROPBEAR_REF"
fi

# 3. Claves públicas (opcional). Se valida todo antes de añadir nada.
if [ -n "$PUBKEY_SRC" ]; then
    [ -f "$PUBKEY_SRC" ] || die "no existe $PUBKEY_SRC"
    if grep -q 'PRIVATE KEY' "$PUBKEY_SRC"; then
        die "$PUBKEY_SRC es una clave PRIVADA: usa el fichero .pub"
    fi
    pk_count=0
    while IFS= read -r pk_line || [ -n "$pk_line" ]; do
        case "$pk_line" in ''|'#'*) continue ;; esac
        printf '%s\n' "$pk_line" | grep -Eq "$KEY_REGEX" ||
            die "línea no reconocida como clave pública en $PUBKEY_SRC"
        pk_count=$((pk_count + 1))
    done <"$PUBKEY_SRC"
    [ "$pk_count" -gt 0 ] || die "$PUBKEY_SRC no contiene ninguna clave pública"
    pk_added=0
    while IFS= read -r pk_line || [ -n "$pk_line" ]; do
        case "$pk_line" in ''|'#'*) continue ;; esac
        if [ -f "$BASE/authorized_keys" ] && grep -Fxq -- "$pk_line" "$BASE/authorized_keys"; then
            continue
        fi
        printf '%s\n' "$pk_line" >>"$BASE/authorized_keys" || die "no se pudo escribir authorized_keys"
        pk_added=$((pk_added + 1))
        case "$pk_line" in
            ssh-ed25519\ *|*\ ssh-ed25519\ *) ;;
            *) warn "clave no ed25519: un dropbear compilado con device/dropbear/localoptions.h solo acepta ed25519" ;;
        esac
    done <"$PUBKEY_SRC"
    chmod 600 "$BASE/authorized_keys" || die "chmod authorized_keys"
    echo "claves públicas añadidas: $pk_added (repetidas ignoradas)"
fi

# 4. Política de contraseña
case "$PASSWORD" in
    allow)
        : >"$BASE/allow_password" && chmod 600 "$BASE/allow_password" || die "allow_password"
        warn "contraseña root PERMITIDA. La contraseña de fábrica se deriva de la AUTHKEY que el puerto 8090 expone sin autenticación: cualquiera en la LAN podría entrar si no la has cambiado."
        ;;
    deny)
        rm -f "$BASE/allow_password"
        echo "contraseña root desactivada para SSH (solo clave)"
        ;;
esac
if [ -f "$BASE/authorized_keys" ]; then chmod 600 "$BASE/authorized_keys"; fi

# 5. Clave de host: se genera una vez por panel y nunca se regenera.
if [ -n "$DROPBEAR_REF" ]; then
    hk_bin=$DROPBEAR_REF
elif [ -f "$BASE/dropbear_path" ]; then
    hk_bin=$(sed -n '1p' "$BASE/dropbear_path")
elif [ -x "$BASE/dropbearmulti" ]; then
    hk_bin=$BASE/dropbearmulti
elif [ -x "$BASE/dropbear" ]; then
    hk_bin=$BASE/dropbear
else
    hk_bin=
fi
hk_gen=
if [ -n "$hk_bin" ]; then
    case "${hk_bin##*/}" in
        dropbearmulti*) hk_gen="$hk_bin dropbearkey" ;;
        *)
            if [ -x "${hk_bin%/*}/dropbearkey" ]; then
                hk_gen=${hk_bin%/*}/dropbearkey
            elif command -v dropbearkey >/dev/null 2>&1; then
                hk_gen=$(command -v dropbearkey)
            fi ;;
    esac
fi
if [ ! -s "$BASE/host_ed25519" ] && [ -n "$hk_gen" ]; then
    rm -f "$BASE/host_ed25519.new"
    # $hk_gen sin comillas a propósito: puede ser "<multi> dropbearkey".
    if $hk_gen -t ed25519 -f "$BASE/host_ed25519.new" >/dev/null 2>&1 &&
       [ -s "$BASE/host_ed25519.new" ]; then
        chmod 600 "$BASE/host_ed25519.new" && mv -f "$BASE/host_ed25519.new" "$BASE/host_ed25519"
        echo "clave de host generada: $BASE/host_ed25519"
    else
        rm -f "$BASE/host_ed25519.new"
        warn "no se pudo generar la clave de host; el guardián lo reintentará"
    fi
fi
if [ -s "$BASE/host_ed25519" ] && [ -n "$hk_gen" ]; then
    echo "huella de la clave de host (compárala en la primera conexión):"
    $hk_gen -y -f "$BASE/host_ed25519" 2>/dev/null | grep -i fingerprint
fi
sync

# 6. Resumen
if [ -f "$BASE/authorized_keys" ] && grep -Eq "$KEY_REGEX" "$BASE/authorized_keys"; then
    sm_keys=yes
else
    sm_keys=no
fi
[ -e "$BASE/allow_password" ] && sm_pw=yes || sm_pw=no
echo "autenticación: claves=$sm_keys contraseña=$sm_pw"
if [ "$sm_keys" = no ] && [ "$sm_pw" = no ]; then
    warn "sin authorized_keys ni allow_password el guardián NO arrancará dropbear (USB y red sí)"
fi
[ -n "$hk_bin" ] || warn "sin binario dropbear: el guardián solo vigilará USB y red"
for sm_dir in "$BASE" /data; do
    sm_mode=$(ls -ld "$sm_dir" 2>/dev/null | cut -c1-10)
    case "$sm_mode" in
        ?????w????|????????w?) warn "$sm_dir es escribible por grupo/otros: dropbear rechazará authorized_keys" ;;
    esac
done
if grep -q tpp06-ssh-guardian "$LAUNCHER" 2>/dev/null; then
    echo "arranque automático: sí ($LAUNCHER lanza el guardián)"
else
    warn "arranque automático: NO detectado en $LAUNCHER (instala el multiboot actualizado; ver guía)"
fi

# 7. Arranque opcional, sin reiniciar el panel
if [ "$DO_START" = yes ]; then
    if "$BASE/tpp06-ssh-guardian.sh" status >/dev/null 2>&1; then
        "$BASE/tpp06-ssh-guardian.sh" stop
    fi
    "$BASE/tpp06-ssh-guardian.sh" start
    sleep 2
    "$BASE/tpp06-ssh-guardian.sh" status
fi
exit 0
