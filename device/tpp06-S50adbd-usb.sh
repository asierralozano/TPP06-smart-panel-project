#!/bin/sh
# TPP06 S50adbd  (USB-ADB de rescate, SIEMPRE, autorizado)
# Instalado por el proyecto (2026-11-19). Original de Philippe en S50adbd.tcp.bak
#
# Arranca desde /etc/init.d/rc.local (linea 33), ANTES de la UI (S99tuya-app),
# e independiente de Tuya. Monta el gadget USB (metodo de fabrica) y supervisa
# el adbd PARCHEADO (/data/customize/app/adbd), que entra autorizado sin prompt.
# Si el montaje USB fallara, adbd cae a TCP (red): nunca te quedas sin ADB.
DAEMON=/data/customize/app/adbd
GADGET=/sys/kernel/config/usb_gadget/iot
BOOTLOG=/data/tpp06-adbd-boot.log

usb_setup() {
	mount -t configfs none /sys/kernel/config 2>/dev/null
	grep -qs functionfs /proc/mounts && return 0
	ADB_SN=$(nvram get ADB_SN 2>/dev/null)
	case "$ADB_SN" in
		""|*error*|*ERROR*|*NULL*)
			ADB_SN=$(cat /proc/sys/kernel/random/uuid 2>/dev/null | md5sum | cut -c1-16) ;;
	esac
	mkdir -p "$GADGET" 2>/dev/null || return 1
	echo 0x2207 > "$GADGET/idVendor"
	echo 0x0006 > "$GADGET/idProduct"
	mkdir -p "$GADGET/strings/0x409"
	echo "$ADB_SN" > "$GADGET/strings/0x409/serialnumber"
	echo "TuyaIotDevice" > "$GADGET/strings/0x409/manufacturer"
	echo "TuyaIotModule" > "$GADGET/strings/0x409/product"
	mkdir -p "$GADGET/configs/b.1/strings/0x409"
	echo 500 > "$GADGET/configs/b.1/MaxPower"
	echo "adb" > "$GADGET/configs/b.1/strings/0x409/configuration"
	mkdir -p "$GADGET/functions/ffs.adb"
	ln -s "$GADGET/functions/ffs.adb" "$GADGET/configs/b.1/ffs.adb" 2>/dev/null
	mkdir -p /dev/usb-ffs/adb
	mount -o uid=2000,gid=2000 -t functionfs adb /dev/usb-ffs/adb
}

usb_bind() {
	[ -d "$GADGET" ] || return 1
	[ -n "$(cat "$GADGET/UDC" 2>/dev/null)" ] && return 0
	UDC=$(ls /sys/class/udc/ 2>/dev/null | head -n1)
	[ -n "$UDC" ] || return 1
	echo "$UDC" > "$GADGET/UDC" 2>/dev/null
}

start() {
	echo "$(cut -d. -f1 /proc/uptime 2>/dev/null)s start" >> "$BOOTLOG" 2>/dev/null
	source /etc/profile 2>/dev/null
	usb_setup
	# Supervisor en segundo plano: no bloquea el arranque.
	(
		while true; do
			"$DAEMON" >>/tmp/tpp06-adbd.log 2>&1 &
			dpid=$!
			sleep 1
			usb_bind
			wait "$dpid"
			sleep 2
		done
	) &
	# Guardián SSH (dropbear por red), independiente de Tuya. Su ausencia o fallo
	# nunca afecta al ADB-USB. El guardián se auto-supervisa (ignora SIGHUP).
	[ -x /data/tpp06-ssh/tpp06-ssh-guardian.sh ] && \
		sh /data/tpp06-ssh/tpp06-ssh-guardian.sh start >/dev/null 2>&1
}

stop() {
	echo "none" > "$GADGET/UDC" 2>/dev/null
	start-stop-daemon -K -q -n adbd 2>/dev/null
}

restart() {
	stop
	sleep 1
	start
}

case "$1" in
	start|stop|restart)
		"$1" ;;
	*)
		echo "Usage: $0 {start|stop|restart}"
		exit 1 ;;
esac
