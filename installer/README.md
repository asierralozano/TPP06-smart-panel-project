# Aprovisionador TPP06 — replicar un panel preparado en otros

Deja un panel TPP06 nuevo **exactamente** como el panel de referencia ya preparado:
rescate ADB por cable + SSH por clave (independientes de Tuya), **modo reemplazo** con
la UI de ESPHome y sincronización de reloj. Está pensado para los 3 paneles de casa,
partiendo del de pruebas como molde.

## Qué reproduce (verificado contra el panel de referencia)

| En el panel                         | Origen en el repo                          |
|-------------------------------------|--------------------------------------------|
| `/data/customize/app/adbd`          | `device/bin/tpp06-adb-network-final.tar.gz` (overlay) |
| `/data/misc/adb/adb_keys`           | idem (overlay)                             |
| `/data/etc/init.d/S50adbd`          | `device/tpp06-S50adbd-usb.sh` (ADB-USB + arranca el guardián SSH) |
| `/data/tpp06-ssh/dropbear(+key)`    | `device/bin/dropbear`, `dropbearkey`       |
| `/data/tpp06-ssh/tpp06-ssh-guardian.sh` | `device/tpp06-ssh-guardian.sh`         |
| `/data/tpp06-ssh/authorized_keys`   | tu `~/.ssh/tpp06_ed25519.pub`              |
| `/data/tpp06-home/program`          | binario ESPHome del panel (`[ui] program`) |
| `/data/tpp06-home/esphome-launcher.sh` | `device/tpp06-esphome-launcher.sh`      |
| `/data/tpp06-home/clocksync.sh`     | `device/tpp06-clocksync.sh`                |
| `/data/etc/init.d/S99tuya-app`      | := el launcher (arma el reemplazo)         |

Cada fichero se empuja **byte a byte** y se verifica por md5 contra el valor capturado
del panel de referencia. Si un fichero del repo se hubiera desviado, el script para.

> **Nota de la auditoría repo ↔ panel de referencia:** todo coincide md5 a md5 salvo el
> `S50adbd`, que en el panel aparece sin indentación (se la comió el método de copia
> antiguo por heredoc). Es **cosmético**: la lógica es idéntica. El instalador empuja la
> versión limpia del repo con transferencia byte-exacta (adb push / pipe SSH), así que
> los paneles nuevos quedan incluso más limpios que el de referencia.

## Requisitos previos (NO hace falta UART)

El primer acceso es **100 % por software**, igual que en el panel de referencia:

1. El panel arranca con su firmware **Tuya de fábrica** y está **en la WiFi** (por la app
   Tuya o con su perfil ya guardado), con una **IP conocida** en tu red.
2. Con Tuya en marcha, el panel escucha en el **puerto product-test 8090**: de ahí se lee
   el AUTHKEY (sin modificar nada) y se deriva la contraseña root **en memoria**.
3. `adb` instalado en el Mac y la clave SSH `~/.ssh/tpp06_ed25519` (la misma que usas con
   el panel de referencia).

UART queda solo como **rescate** para desbrickear el arranque si algo fuese mal; no
interviene en la preparación.

## Uso

```sh
cp panel.conf.example panel-salon.conf     # y rellénalo (IP, ruta del binario, etc.)
./tpp06-install.sh --config panel-salon.conf
```

Es **multi-fase y reanudable**. Tres fases terminan pidiendo un **power-cycle** del panel
(desenchufar/enchufar). Tras cada uno, vuelve a ejecutar el **mismo comando**:

| Fase            | Qué hace                                               | Al acabar |
|-----------------|--------------------------------------------------------|-----------|
| `discover`      | Detecta el panel por 8090, deriva la contraseña root   | sigue     |
| `enable_adb`    | Sube el overlay ADB por 8090                           | **power-cycle** |
| `backup`        | Backup NAND (factory/env) + overlay de datos           | sigue     |
| `harden`        | ADB por cable permanente + SSH por clave               | **power-cycle** |
| `verify_access` | Comprueba SSH por clave en frío                        | sigue     |
| `install_ui`    | Copia el binario ESPHome y **arma** el modo reemplazo  | **power-cycle** |
| `finish`        | Verificación final + siguientes pasos de HA            | fin       |

Comandos útiles:

```sh
./tpp06-install.sh --config panel-salon.conf --status   # ver progreso
./tpp06-install.sh --config panel-salon.conf --only install_ui --yes
./tpp06-install.sh --config panel-salon.conf --reset    # empezar de cero
```

## Seguridad / rescate

- El **rescate (ADB + SSH) arranca en `S50`, antes que la UI (`S99`)** y es independiente
  de Tuya: aunque el binario de la UI falle, el panel sigue accesible, y el launcher cae
  a Tuya si no encuentra el binario.
- **Revertir el modo reemplazo** (por SSH):
  ```sh
  cp /data/tpp06-home/S99tuya-app.stock /etc/init.d/S99tuya-app && sync
  ```
- La **NAND no se escribe** en ningún momento (solo se lee para el backup).
- **Secretos:** la contraseña root se deriva en memoria y nunca se escribe; el perfil WiFi
  se copia dentro del propio panel (nunca pasa por el Mac ni por git). Los `.conf` reales
  y el estado (`installer/state/`) están ignorados en git.

## Estado

v1: construido sobre las piezas ya validadas en el panel de referencia, pero el recorrido
completo sobre un panel **de fábrica** aún no se ha ejecutado de principio a fin. Las fases
usan transferencia byte-exacta y verificación md5; si alguna falla en un panel real, el
estado queda guardado y se reanuda tras corregir.
