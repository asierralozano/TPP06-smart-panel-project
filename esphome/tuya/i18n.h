#pragma once
// ─────────────────────────────────────────────────────────────────────────────
// TPP06 · UI estilo Tuya — TEXTOS DE LA INTERFAZ (i18n)
//
// AÑADIR UN IDIOMA (solo alfabeto latino: las fuentes no traen otros):
//   1. Sube TR_LANG_COUNT y añade el nombre del idioma (en su propio idioma)
//      al final de TR_LANG_NAMES.
//   2. Añade una columna más (al final) en CADA fila de TR_TEXTS.
//      Un texto vacío "" o ausente usa el español como respaldo.
//   3. Compila y actualiza por OTA. En el panel: Ajustes → Idioma.
//
// Los nombres de estancias y dispositivos los escribe cada usuario en su YAML
// y no se traducen.
// ─────────────────────────────────────────────────────────────────────────────

static const int TR_LANG_COUNT = 2;
static const char *const TR_LANG_NAMES[TR_LANG_COUNT] = {"Español", "English"};

enum TrKey : int {
  // Estados genéricos
  TR_ON_F, TR_OFF_F, TR_ON_M, TR_OFF_M, TR_UNAVAILABLE, TR_UNKNOWN,
  // Ajustes
  TR_SETTINGS, TR_WIFI, TR_SCREEN_BRIGHTNESS, TR_SCREEN_OFF, TR_SCREENSAVER,
  TR_SOUND, TR_LANGUAGE, TR_HOME_ASSISTANT, TR_RESTART_PANEL,
  TR_CONNECTED, TR_NO_LINK, TR_NEVER, TR_ENABLED, TR_DISABLED, TR_OK, TR_NO,
  TR_RESTART_TEXT, TR_RESTART, TR_CANCEL,
  // Centro de control y escenas
  TR_ALL_ON, TR_ALL_OFF, TR_NIGHT_MODE, TR_SCENES,
  // Tarjetas
  TR_BRIGHTNESS, TR_TEMPERATURE, TR_HUMIDITY,
  TR_COVER_OPEN, TR_COVER_CLOSED, TR_COVER_OPENING, TR_COVER_CLOSING,
  TR_OPEN, TR_STOP, TR_CLOSE,
  TR_MODE_HEAT, TR_MODE_COOL, TR_MODE_AUTO, TR_MODE_DRY, TR_MODE_FAN, TR_MODE_OFF,
  TR_CURRENT,
  TR_NOTHING_PLAYING, TR_PLAYING,
  // Fecha (abreviaturas)
  TR_SUN, TR_MON, TR_TUE, TR_WED, TR_THU, TR_FRI, TR_SAT,
  TR_JAN, TR_FEB, TR_MAR, TR_APR, TR_MAY, TR_JUN,
  TR_JUL, TR_AUG, TR_SEP, TR_OCT, TR_NOV, TR_DEC,
  TR_KEY_COUNT
};

static const char *const TR_TEXTS[TR_KEY_COUNT][TR_LANG_COUNT] = {
  // Español                 English
  {"Encendida",              "On"},                 // TR_ON_F
  {"Apagada",                "Off"},                // TR_OFF_F
  {"Encendido",              "On"},                 // TR_ON_M
  {"Apagado",                "Off"},                // TR_OFF_M
  {"No disponible",          "Unavailable"},        // TR_UNAVAILABLE
  {"Desconocido",            "Unknown"},            // TR_UNKNOWN
  {"Ajustes",                "Settings"},           // TR_SETTINGS
  {"WiFi",                   "WiFi"},               // TR_WIFI
  {"Brillo de pantalla",     "Screen brightness"},  // TR_SCREEN_BRIGHTNESS
  {"Apagar pantalla",        "Screen off"},         // TR_SCREEN_OFF
  {"Salvapantallas",         "Screensaver"},        // TR_SCREENSAVER
  {"Sonido",                 "Sound"},              // TR_SOUND
  {"Idioma",                 "Language"},           // TR_LANGUAGE
  {"Home Assistant",         "Home Assistant"},     // TR_HOME_ASSISTANT
  {"Reiniciar panel",        "Restart panel"},      // TR_RESTART_PANEL
  {"Conectado",              "Connected"},          // TR_CONNECTED
  {"Sin enlace",             "No link"},            // TR_NO_LINK
  {"Nunca",                  "Never"},              // TR_NEVER
  {"Activado",               "On"},                 // TR_ENABLED
  {"Desactivado",            "Off"},                // TR_DISABLED
  {"OK",                     "OK"},                 // TR_OK
  {"No",                     "No"},                 // TR_NO
  {"Se reiniciará la aplicación del panel. Tarda unos segundos y no cambia los relés.",
   "The panel app will restart. It takes a few seconds and does not change the relays."},
  {"Reiniciar",              "Restart"},            // TR_RESTART
  {"Cancelar",               "Cancel"},             // TR_CANCEL
  {"Todo encendido",         "All on"},             // TR_ALL_ON
  {"Todo apagado",           "All off"},            // TR_ALL_OFF
  {"Modo noche",             "Night mode"},         // TR_NIGHT_MODE
  {"Escenas",                "Scenes"},             // TR_SCENES
  {"Brillo",                 "Brightness"},         // TR_BRIGHTNESS
  {"Temperatura",            "Temperature"},        // TR_TEMPERATURE
  {"Humedad",                "Humidity"},           // TR_HUMIDITY
  {"Abierta",                "Open"},               // TR_COVER_OPEN
  {"Cerrada",                "Closed"},             // TR_COVER_CLOSED
  {"Abriendo…",              "Opening…"},           // TR_COVER_OPENING
  {"Cerrando…",              "Closing…"},           // TR_COVER_CLOSING
  {"Abrir",                  "Open"},               // TR_OPEN
  {"Parar",                  "Stop"},               // TR_STOP
  {"Cerrar",                 "Close"},              // TR_CLOSE
  {"Calefacción",            "Heating"},            // TR_MODE_HEAT
  {"Refrigeración",          "Cooling"},            // TR_MODE_COOL
  {"Automático",             "Auto"},               // TR_MODE_AUTO
  {"Deshumidificar",         "Dry"},                // TR_MODE_DRY
  {"Ventilador",             "Fan"},                // TR_MODE_FAN
  {"Apagado",                "Off"},                // TR_MODE_OFF
  {"Actual",                 "Current"},            // TR_CURRENT
  {"Sin reproducción",       "Nothing playing"},    // TR_NOTHING_PLAYING
  {"Reproduciendo",          "Playing"},            // TR_PLAYING
  {"dom", "Sun"}, {"lun", "Mon"}, {"mar", "Tue"}, {"mié", "Wed"},
  {"jue", "Thu"}, {"vie", "Fri"}, {"sáb", "Sat"},
  {"ene", "Jan"}, {"feb", "Feb"}, {"mar", "Mar"}, {"abr", "Apr"},
  {"may", "May"}, {"jun", "Jun"}, {"jul", "Jul"}, {"ago", "Aug"},
  {"sep", "Sep"}, {"oct", "Oct"}, {"nov", "Nov"}, {"dic", "Dec"},
};

// Idioma activo (lo fija la UI desde el global `ui_lang`).
inline int &tr_language() {
  static int lang = 0;
  return lang;
}

inline void tr_set_language(int lang) {
  tr_language() = (lang >= 0 && lang < TR_LANG_COUNT) ? lang : 0;
}

inline const char *T(int key) {
  if (key < 0 || key >= TR_KEY_COUNT) return "";
  const char *text = TR_TEXTS[key][tr_language()];
  return (text != nullptr && text[0] != '\0') ? text : TR_TEXTS[key][0];
}
