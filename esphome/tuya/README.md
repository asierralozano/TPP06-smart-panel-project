# UI estilo Tuya para el TPP06 (ESPHome + LVGL)

Interfaz que recrea la UI original de Tuya del panel TPP06, conectada a Home
Assistant, montada sobre la base de hardware de este repositorio (drivers
`fbdev`/`evdev`/`host`/`tpp06_*`, supervisor y OTA). Las coordenadas, colores y
tamaños salen del clon C/LVGL de [TPP06-custom-ui](https://github.com/asierralozano/TPP06-custom-ui)
(tag `c-lvgl-v1`), derivado del firmware Tuya 1.2.3.

Se configura en **YAML** y se actualiza por **OTA**: cada estancia y cada
dispositivo es una línea de `packages:` en tu fichero del panel.

## Estructura

| Fichero | Qué es |
|---|---|
| `../tpp06-tuya.yaml` | Fichero del panel (el que copias a Device Builder). |
| `hw/tpp06.yaml` | Hardware del panel (drivers, API, OTA, reposo de pantalla). |
| `hw/preview.yaml` | Sustituto para previsualizar en el ordenador (SDL headless). |
| `core.yaml` | Tema, fuentes, imágenes y pantallas globales: home, centro de control, ajustes, brillo, reinicio, escenas, salvapantallas. |
| `room.yaml` | Página de estancia (wallpaper + título + tarjetas apiladas). |
| `cards/*.yaml` | Tipos de tarjeta, cada uno con su página de detalle si la tiene. |
| `preview/` | Configuración de demo y `run.sh` para generar capturas. |

## Assets de Tuya (privados)

Los PNG (wallpaper, iconos, tiempo) son de Tuya y **no están en git**. Cópialos
desde un firmware extraído:

```sh
sh tools/tpp06_tuya_assets.sh /ruta/a/tuya-fw/img        # -> esphome/tuya-assets/
```

En Home Assistant deben quedar en `/config/esphome/tuya-assets/`.

## Instalar en el panel

1. Requisitos de la base (ver `docs/en/START-HERE.md`): panel compatible,
   acceso de red, backup y ESPHome Device Builder **2026.10.2** en HA.
2. Copia a `/config/esphome/` de HA: `tpp06-tuya.yaml` (renómbralo por panel),
   la carpeta `tuya/`, la carpeta `packages/` y tus `tuya-assets/`.
3. Edita las `substitutions` (nombre, clave API en `secrets.yaml`, entidades) y
   la lista de estancias/tarjetas en `packages:`.
4. En la integración ESPHome de HA, activa *Permitir que el dispositivo realice
   acciones de Home Assistant* (las tarjetas encienden/apagan con acciones).
5. Compila. Primera instalación: Tuya Cutter con prueba acotada
   (`docs/en/07-tuya-cutter.md`); después, cada cambio de UI va por OTA.
6. Relés físicos: empiezan en observación (`control_enabled: false`). Actívalos
   solo tras comprobar el cableado en sitio.

## Navegación

- Home (reloj, fecha, tiempo) → deslizar a la izquierda para pasar por las estancias.
- Deslizar hacia abajo: centro de control (brillo, modo noche, todo on/off,
  Ajustes, Escenas). Deslizar hacia arriba lo cierra.
- Tarjeta: toque = encender/apagar; pulsación larga = detalle (brillo, posición…).
- Salvapantallas por inactividad y apagado de pantalla configurables en Ajustes.

## Tipos de tarjeta

Todas reciben `uid` (identificador único: minúsculas, números y `_`) y `room`
(la estancia donde se apilan).

| Fichero | Variables | Detalle |
|---|---|---|
| `cards/light.yaml` | `entity`, `name` | Barra de brillo vertical |
| `cards/switch.yaml` | `entity`, `name` | — |
| `cards/sensor_th.yaml` | `name`, `temp_entity`, `hum_entity` | — (solo lectura) |

## Convenciones para crear tarjetas (y pantallas)

- **Contratos de `core.yaml`** (no depender del hardware): estilos `tuya_card`,
  `tuya_row`, `tuya_dark_btn`, `tuya_back_btn`; fuentes Manrope `f14 f15 f16 f17
  f18 f20 f22 f26` (latín completo) y `f19 f36 f40 f48 f67 f90 f150` (solo dígitos y
  algunos signos: mira `glyphs:` en `core.yaml` antes de usarlas); imágenes `img_*`.
- **Añadir a una estancia:** `lvgl: pages: - id: !extend page_${room}` con
  `widgets:`. La página es flex en columna, ancho útil 288 px.
- **Página de detalle:** `id: "page_${uid}_detail"`, `skip: true`, fondo
  `0x0C0C10`, flecha de volver = `button` con `styles: tuya_back_btn` en x 9, y 9,
  48×48 y la imagen `img_left` recoloreada en blanco; título `f20` en x 58, y 22;
  volver con `lvgl.page.show: page_${room}`.
- **YAML:** dentro de mapas en línea `{...}`, entrecomilla los ids con
  sustituciones (`id: "${uid}_x"`); las lambdas en línea con `? :` van entre
  comillas simples (`!lambda 'return a ? b : c;'`) o en bloque `|-`.
- **Datos de HA:** plataformas `homeassistant` con `internal: true`, que
  actualizan los widgets en `on_value`. Las acciones van con `homeassistant.action`
  (en HA hay que activar *Permitir que el dispositivo realice acciones de Home Assistant*
  en las opciones de la integración ESPHome).
- **Coordenadas** relativas al área de contenido del padre (descontado el padding).

## Previsualizar en el ordenador

```sh
python3 -m venv .venv && .venv/bin/pip install esphome==2026.10.2
ESPHOME=.venv/bin/esphome sh esphome/tuya/preview/run.sh \
    esphome/tuya/preview/tpp06-tuya-preview.yaml /tmp/shots
```

Genera un PNG por pantalla en `/tmp/shots`. Requiere SDL2 e ImageMagick.
