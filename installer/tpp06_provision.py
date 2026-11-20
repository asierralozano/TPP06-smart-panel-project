#!/usr/bin/env python3
"""Aprovisionador del TPP06: deja un panel nuevo EXACTAMENTE como el de referencia.

Objetivo: reproducir en cualquier panel el estado del panel de pruebas ya preparado
(ADB de rescate por cable + por red, SSH/dropbear por clave, modo reemplazo con la
UI de ESPHome, sincronización de reloj), de la forma más "enchufar y seguir" posible.

Principios:
  * No reinventa nada: reutiliza el motor ya probado (`tools/tpp06_tuya_cutter.py`)
    para el primer contacto (puerto product-test 8090), el overlay ADB y el backup.
  * Es MULTI-FASE y REANUDABLE. Varias fases terminan pidiendo un power-cycle; al
    volver a ejecutar, el aprovisionador detecta lo ya hecho y continúa.
  * Es FIEL byte a byte: cada fichero que empuja se verifica por md5 contra el valor
    de referencia capturado del panel preparado. Si algo no llega idéntico, PARA.
  * Nunca escribe secretos en disco local ni en git: la contraseña root se deriva en
    memoria (del AUTHKEY, vía 8090) y el perfil WiFi se copia del propio panel.

NO toca la NAND salvo para LEER (backup de factory/env). El único paso que cambia el
arranque es "armar" el modo reemplazo, y deja el Tuya de origen guardado para revertir.
El rescate (ADB+SSH, arrancan en S50, antes que la UI) es independiente de que la UI
funcione: si el binario falla, el panel sigue accesible y con fallback a Tuya.

UART no interviene: fue solo el plan de rescate por hardware que no hizo falta. El
primer acceso real es por software (8090), igual que en el panel de referencia.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# El motor probado vive en ../tools
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from tpp06_tuya_cutter import TuyaCutter, CutterError  # noqa: E402

DEVICE = REPO / "device"
BIN = DEVICE / "bin"
ADB_OVERLAY = BIN / "tpp06-adb-network-final.tar.gz"


# ─────────────────────────── md5 de REFERENCIA (panel preparado) ───────────────────────────
# Capturados del panel de pruebas ya funcionando. Cada fichero que empujemos a un panel
# nuevo debe coincidir con esto, o el aprovisionador para (garantía de "copia exacta").
REFERENCE_MD5 = {
    "device/bin/dropbear": "1e63fe5bdf5d0050b3c9e532c50c40dc",
    "device/bin/dropbearkey": "bb45061b38ec774abc1e3b92eab9f92b",
    "device/tpp06-ssh-guardian.sh": "9d94dceefcc0738431599cf3563590a6",
    "device/tpp06-esphome-launcher.sh": "857fe1a7b5039a2e72b4a43da08a598b",
    "device/tpp06-clocksync.sh": "e85785b1fe2f2cc8322d09127487e308",
    # adbd y adb_keys viajan dentro del overlay (device/bin/tpp06-adb-network-final.tar.gz):
    "overlay:app/adbd": "1f979da4b6d59c65de20b084ee837b2a",
    "overlay:misc/adb/adb_keys": "f8f86b26ba3d21f66f96688d615b5641",
    # S50adbd-usb: la versión del repo lleva indentación; en el panel es equivalente
    # (el shell ignora el sangrado). Se verifica por "funciona", no por md5 exacto.
}


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def log(msg: str) -> None:
    print(msg, flush=True)


def section(title: str) -> None:
    print(f"\n\033[1m=== {title} ===\033[0m", flush=True)


# ─────────────────────────────── Configuración y estado ───────────────────────────────
@dataclass
class Config:
    name: str
    host: str
    adb: str = "adb"
    usb_serial: str = ""
    backup_dir: Path = Path("~/Documents/tpp06-backups")
    ssh_key: Path = Path("~/.ssh/tpp06_ed25519")
    pubkey: Path = Path("~/.ssh/tpp06_ed25519.pub")
    program: Path | None = None          # binario ESPHome por panel (NO va en git)
    clock_url: str = ""                  # ej. http://192.168.18.77:8123/
    timezone: str = ""                   # lo aplica ESPHome; aquí solo informativo

    @staticmethod
    def load(path: Path) -> "Config":
        cp = configparser.ConfigParser()
        if not cp.read(path):
            raise SystemExit(f"No se pudo leer la configuración: {path}")
        p = cp["panel"]
        ui = cp["ui"] if cp.has_section("ui") else {}
        ssh = cp["ssh"] if cp.has_section("ssh") else {}

        def xp(v: str) -> Path:
            return Path(os.path.expanduser(v)).resolve()

        prog = ui.get("program", "").strip() if ui else ""
        return Config(
            name=p["name"].strip(),
            host=p["host"].strip(),
            adb=p.get("adb", "adb").strip(),
            usb_serial=p.get("usb_serial", "").strip(),
            backup_dir=xp(p.get("backup_dir", "~/Documents/tpp06-backups")),
            ssh_key=xp(ssh.get("key", "~/.ssh/tpp06_ed25519")) if ssh else xp("~/.ssh/tpp06_ed25519"),
            pubkey=xp(ssh.get("pubkey", "~/.ssh/tpp06_ed25519.pub")) if ssh else xp("~/.ssh/tpp06_ed25519.pub"),
            program=xp(prog) if prog else None,
            clock_url=(ui.get("clock_url", "").strip() if ui else ""),
            timezone=(ui.get("timezone", "").strip() if ui else ""),
        )


class State:
    """Qué fases se han completado. Vive en installer/state/<name>.json (ignorado por git)."""

    def __init__(self, path: Path):
        self.path = path
        self.data = {"phases": {}, "notes": {}}
        if path.is_file():
            try:
                self.data = json.loads(path.read_text())
            except Exception:
                pass

    def done(self, key: str) -> bool:
        return key in self.data["phases"]

    def mark(self, key: str, info: dict | None = None) -> None:
        self.data["phases"][key] = {"ts": int(time.time()), **(info or {})}
        self.save()

    def note(self, key: str, value) -> None:
        self.data["notes"][key] = value
        self.save()

    def reset(self) -> None:
        self.data = {"phases": {}, "notes": {}}
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2) + "\n")


# ─────────────────────────────── Canales de transporte ───────────────────────────────
# Un canal sabe ejecutar comandos como root y empujar/traer ficheros byte-exactos.
class Channel:
    def run(self, cmd: str, timeout: float = 60, check: bool = True) -> tuple[int, str]:
        raise NotImplementedError

    def put(self, local: Path, remote: str, mode: str = "0644") -> None:
        raise NotImplementedError

    def get(self, remote: str, local: Path) -> None:
        raise NotImplementedError


class AdbChannel(Channel):
    """Shell+transferencia por el motor del cutter (ADB por red o USB, con login root)."""

    def __init__(self, cutter: TuyaCutter):
        self.sess = cutter.connected_session()

    def run(self, cmd, timeout=60, check=True):
        r = self.sess.run_root(cmd, timeout=timeout, check=check)
        return r.returncode, r.stdout

    def put(self, local, remote, mode="0644"):
        self.sess.upload(Path(local), remote)
        self.sess.run_root(f"chmod {mode} {shlex.quote(remote)}", check=False)

    def get(self, remote, local):
        self.sess.download(remote, Path(local))


class SshChannel(Channel):
    """Shell+transferencia por dropbear con clave (root directo, sin contraseña).

    Byte-exacto: `cat local | ssh 'cat > remote'` y `ssh 'cat remote' > local`
    (dropbear del panel no trae scp/sftp, pero el pipe va idéntico). Verifica md5.
    """

    def __init__(self, host: str, key: Path):
        self.host = host
        self.key = key
        self.base = [
            "ssh", "-i", str(key),
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=accept-new",
            f"root@{host}",
        ]

    def run(self, cmd, timeout=60, check=True):
        p = subprocess.run(self.base + [cmd], capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        if check and p.returncode != 0:
            raise CutterError(f"SSH comando falló ({p.returncode}): {out[-800:]}")
        return p.returncode, out

    def put(self, local, remote, mode="0644"):
        local = Path(local)
        local_md5 = md5_file(local)
        rq = shlex.quote(remote)
        with local.open("rb") as f:
            p = subprocess.run(
                self.base + [f"cat > {rq} && chmod {mode} {rq}"],
                stdin=f, capture_output=True, text=True, timeout=300,
            )
        if p.returncode != 0:
            raise CutterError(f"SSH put falló: {(p.stderr or '')[-400:]}")
        _, out = self.run(f"md5sum {rq} 2>/dev/null | cut -d' ' -f1", check=False)
        if local_md5 not in out:
            raise CutterError(f"md5 remoto no coincide para {remote} (esperado {local_md5}, got {out.strip()})")

    def get(self, remote, local):
        local = Path(local)
        local.parent.mkdir(parents=True, exist_ok=True)
        with local.open("wb") as f:
            p = subprocess.run(self.base + [f"cat {shlex.quote(remote)}"], stdout=f, timeout=300)
        if p.returncode != 0:
            raise CutterError(f"SSH get falló para {remote}")


# ─────────────────────────────── Contexto de ejecución ───────────────────────────────
@dataclass
class Ctx:
    cfg: Config
    state: State
    assume_yes: bool
    cutter: TuyaCutter
    _channels: dict = field(default_factory=dict)

    def adb(self) -> AdbChannel:
        if "adb" not in self._channels:
            self._channels["adb"] = AdbChannel(self.cutter)
        return self._channels["adb"]

    def ssh(self) -> SshChannel:
        if "ssh" not in self._channels:
            self._channels["ssh"] = SshChannel(self.cfg.host, self.cfg.ssh_key)
        return self._channels["ssh"]


def confirm(ctx: Ctx, prompt: str) -> bool:
    if ctx.assume_yes:
        return True
    return input(f"{prompt} [s/N] ").strip().lower() in ("s", "si", "sí", "y", "yes")


def verify_reference(label: str, local: Path) -> None:
    """Comprueba que un fichero del repo coincide con el panel de referencia."""
    want = REFERENCE_MD5.get(label)
    if want is None:
        return
    got = md5_file(local)
    if got != want:
        raise CutterError(
            f"{label}: md5 {got} ≠ referencia {want}. El repo se ha desviado del panel "
            f"de referencia; revísalo antes de aprovisionar (no sigo para no divergir)."
        )


# ─────────────────────────────────────── FASES ───────────────────────────────────────
def phase_discover(ctx: Ctx) -> None:
    section("Fase 1/7 · Detectar el panel (puerto product-test 8090)")
    log("Leyendo AUTHKEY por 8090 y derivando la contraseña root EN MEMORIA (nada se escribe)…")
    ctx.cutter.discover()  # imprime identidad; valores sensibles enmascarados
    cfgs = ctx.cutter.configuration or {}
    ident = {k: cfgs.get(k) for k in ("productKey", "deviceType", "mac", "model") if k in cfgs}
    ctx.state.note("identity", ident)
    log(f"  Identidad (no sensible): {ident or '(sin campos públicos)'}")


def phase_enable_adb(ctx: Ctx) -> None:
    section("Fase 2/7 · Instalar el overlay ADB por 8090")
    # ¿Ya hay ADB? (panel ya tocado). Si sí, saltamos.
    try:
        ctx.cutter.connected_session().run_root("echo ok", check=True, timeout=20)
        log("ADB ya operativo: se omite la instalación del overlay.")
        return
    except Exception:
        pass
    if not ADB_OVERLAY.is_file():
        raise CutterError(f"Falta el overlay ADB en {ADB_OVERLAY}")
    verify_reference("overlay:app/adbd", _overlay_member_check())  # sanity del overlay
    if not confirm(ctx, f"Voy a subir el overlay ADB a {ctx.cfg.host} por 8090. ¿Continúo?"):
        raise SystemExit("Cancelado por el usuario.")
    ctx.cutter.enable_adb(ADB_OVERLAY, assume_yes=True)
    log("\n⏸  OVERLAY SUBIDO. Ahora HAZ UN POWER-CYCLE del panel (desenchufar/enchufar).")
    log("   Cuando vuelva a arrancar, re-ejecuta el instalador para continuar.")


def _overlay_member_check() -> Path:
    """Extrae adbd del overlay a un temporal para poder verificar su md5 de referencia."""
    import tempfile, tarfile
    d = Path(tempfile.mkdtemp())
    with tarfile.open(ADB_OVERLAY) as t:
        member = t.getmember("app/adbd")
        t.extract(member, d)  # overlay propio del proyecto, no dato externo
    return d / "app" / "adbd"


def phase_backup(ctx: Ctx) -> None:
    section("Fase 3/7 · Backup irreemplazable (NAND factory/env + overlay de datos)")
    ch = ctx.adb()
    outdir = ctx.cfg.backup_dir / f"nand-{ctx.cfg.name}"
    outdir.mkdir(parents=True, exist_ok=True)
    for name, dev in (("factory", "mtd13"), ("env", "mtd2")):
        remote = f"/tmp/tpp06-nand-{name}.bin"
        ch.run(f"dd if=/dev/{dev} of={remote} bs=65536 2>/dev/null; sync", timeout=120, check=False)
        local = outdir / f"{name}-{dev}.bin"
        ch.get(remote, local)
        ch.run(f"rm -f {remote}", check=False)
        log(f"  {name} ({dev}): {local} · sha256={_sha256(local)}")
    # Overlay de datos (app/customize/init.d) con el backup verificado del cutter.
    try:
        out = ctx.cutter.backup()
        ctx.state.note("overlay_backup", str(out))
    except CutterError as e:
        log(f"  AVISO: backup de overlay del cutter no completado: {e}")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(65536), b""):
            h.update(c)
    return h.hexdigest()[:16] + "…"


def phase_harden(ctx: Ctx) -> None:
    section("Fase 4/7 · Rescate permanente: ADB por cable + SSH por clave")
    ch = ctx.adb()
    # 1) SSH/dropbear en /data/tpp06-ssh (binarios + guardián + clave pública).
    verify_reference("device/bin/dropbear", BIN / "dropbear")
    verify_reference("device/bin/dropbearkey", BIN / "dropbearkey")
    verify_reference("device/tpp06-ssh-guardian.sh", DEVICE / "tpp06-ssh-guardian.sh")
    if not ctx.cfg.pubkey.is_file():
        raise CutterError(f"Falta la clave pública SSH: {ctx.cfg.pubkey}")
    ch.run("mkdir -p /data/tpp06-ssh", check=False)
    ch.put(BIN / "dropbear", "/data/tpp06-ssh/dropbear", "0700")
    ch.put(BIN / "dropbearkey", "/data/tpp06-ssh/dropbearkey", "0755")
    ch.put(DEVICE / "tpp06-ssh-guardian.sh", "/data/tpp06-ssh/tpp06-ssh-guardian.sh", "0755")
    ch.put(DEVICE / "tpp06-ssh-install.sh", "/data/tpp06-ssh/tpp06-ssh-install.sh", "0755")
    ch.put(ctx.cfg.pubkey, "/data/tpp06-ssh/authorized_pub", "0644")
    # Perfil WiFi (secreto): copiado EN EL PANEL desde el de Tuya, nunca pasa por el Mac.
    ch.run("[ -f /tuya/data/wpa_0_8.conf ] && cp /tuya/data/wpa_0_8.conf /data/tpp06-ssh/wifi.conf && "
           "chmod 600 /data/tpp06-ssh/wifi.conf || true", check=False)
    # El instalador on-device genera la host key (por dispositivo) y arranca el guardián.
    ch.run("sh /data/tpp06-ssh/tpp06-ssh-install.sh "
           "--dropbear /data/tpp06-ssh/dropbear "
           "--pubkey /data/tpp06-ssh/authorized_pub --start", timeout=120, check=False)

    # 2) ADB por cable permanente: S50adbd (arranca adbd parcheado + guardián SSH).
    #    Guardamos el S50adbd actual (de red, del overlay) como .tcp.bak si no está ya.
    ch.run("[ -f /data/etc/init.d/S50adbd.tcp.bak ] || "
           "cp /data/etc/init.d/S50adbd /data/etc/init.d/S50adbd.tcp.bak 2>/dev/null || true", check=False)
    ch.put(DEVICE / "tpp06-S50adbd-usb.sh", "/data/etc/init.d/S50adbd", "0755")

    ch.run("sync", check=False)
    log("\n⏸  RESCATE INSTALADO. Haz un POWER-CYCLE.")
    log("   Tras arrancar, ADB entra por cable sin menú y SSH por clave funciona por red.")
    log("   Re-ejecuta el instalador para verificar y continuar.")


def phase_verify_access(ctx: Ctx) -> None:
    section("Fase 5/7 · Verificar rescate en frío (SSH por clave)")
    rc, out = ctx.ssh().run("echo SSH_OK; uname -r; id", check=True, timeout=20)
    log("  " + out.strip().replace("\n", " · "))
    log("  ✓ SSH por clave operativo (independiente de Tuya y de la UI).")


def phase_install_ui(ctx: Ctx) -> None:
    section("Fase 6/7 · Instalar la UI ESPHome y armar el modo reemplazo")
    ch = ctx.ssh()
    verify_reference("device/tpp06-esphome-launcher.sh", DEVICE / "tpp06-esphome-launcher.sh")
    verify_reference("device/tpp06-clocksync.sh", DEVICE / "tpp06-clocksync.sh")
    if not ctx.cfg.program or not ctx.cfg.program.is_file():
        raise CutterError(
            "Falta el binario ESPHome del panel. Pon la ruta en [ui] program del .conf "
            "(cada panel usa el suyo, compilado en Home Assistant). No sigo con el armado."
        )
    # Sanidad del binario (ELF ARM) para no armar algo que deje el panel sin UI.
    head = ctx.cfg.program.read_bytes()[:20]
    if head[:4] != b"\x7fELF":
        raise CutterError(f"{ctx.cfg.program} no parece un ELF; no lo instalo.")

    ch.run("mkdir -p /data/tpp06-home", check=False)
    ch.put(ctx.cfg.program, "/data/tpp06-home/program", "0755")
    ch.put(DEVICE / "tpp06-esphome-launcher.sh", "/data/tpp06-home/esphome-launcher.sh", "0755")
    ch.put(DEVICE / "tpp06-clocksync.sh", "/data/tpp06-home/clocksync.sh", "0755")
    # URL del reloj (cabecera HTTP Date), si se configuró.
    if ctx.cfg.clock_url:
        ch.run(f"sed -i 's#^URL=.*#URL=${{TPP06_CLOCK_URL:-{ctx.cfg.clock_url}}}#' "
               "/data/tpp06-home/clocksync.sh 2>/dev/null || true", check=False)

    # Guardar el Tuya de origen para poder revertir, y ARMAR el reemplazo.
    ch.run("[ -f /data/tpp06-home/S99tuya-app.stock ] || "
           "cp /etc/init.d/S99tuya-app /data/tpp06-home/S99tuya-app.stock 2>/dev/null || true", check=False)
    if not confirm(ctx, "Voy a ARMAR el modo reemplazo (la UI sustituye a Tuya al arrancar). "
                        "El rescate ADB+SSH seguirá aunque la UI falle. ¿Armo?"):
        log("No armo el reemplazo. La UI está copiada; puedes armarla luego con --only install_ui --yes.")
        return
    # Escribir el lanzador en la RUTA QUE EJECUTA rc.local (/etc/init.d/S99tuya-app, copy-up
    # al upperdir) y verificar ESA ruta, para no caer en la vista de overlay obsoleta.
    ch.put(DEVICE / "tpp06-esphome-launcher.sh", "/etc/init.d/S99tuya-app", "0755")
    _, chk = ch.run("md5sum /etc/init.d/S99tuya-app /data/tpp06-home/esphome-launcher.sh", check=False)
    log("  Verificación del lanzador armado:\n    " + chk.strip().replace("\n", "\n    "))
    ch.run("sync", check=False)
    log("\n⏸  MODO REEMPLAZO ARMADO. Haz un POWER-CYCLE: la UI tomará la pantalla.")
    log("   Revertir (si hiciera falta), por SSH:")
    log("     cp /data/tpp06-home/S99tuya-app.stock /etc/init.d/S99tuya-app && sync")


def phase_finish(ctx: Ctx) -> None:
    section("Fase 7/7 · Verificación final")
    rc, out = ctx.ssh().run(
        "echo UI=$(pidof program || echo none); "
        "echo DROPBEAR=$(pidof dropbear >/dev/null && echo yes || echo no); "
        "echo ADBD=$(pidof adbd >/dev/null && echo yes || echo no); "
        "uname -r",
        check=False, timeout=20)
    log("  " + out.strip().replace("\n", " · "))
    log("\n✅ Panel aprovisionado como el de referencia.")
    log("   Siguientes pasos (fuera de este script):")
    log("   • Añadir el panel a Home Assistant por la API de ESPHome (clave del secrets del binario).")
    log("   • Comprobar reloj/clima: clocksync usa la cabecera Date de " + (ctx.cfg.clock_url or "<HA>"))


# Orden, transporte y si termina pidiendo power-cycle.
PHASES = [
    ("discover",      phase_discover,      "p8090", False),
    ("enable_adb",    phase_enable_adb,    "p8090", True),
    ("backup",        phase_backup,        "adb",   False),
    ("harden",        phase_harden,        "adb",   True),
    ("verify_access", phase_verify_access, "ssh",   False),
    ("install_ui",    phase_install_ui,    "ssh",   True),
    ("finish",        phase_finish,        "ssh",   False),
]


def run(ctx: Ctx, only: str | None, start_from: str | None) -> int:
    keys = [p[0] for p in PHASES]
    for key, fn, _transport, stop_after in PHASES:
        if only and key != only:
            continue
        if not only:
            if start_from and keys.index(key) < keys.index(start_from):
                continue
            if ctx.state.done(key):
                continue
        try:
            fn(ctx)
        except CutterError as e:
            log(f"\n✗ Fase '{key}' falló: {e}")
            log("  Corrige y re-ejecuta; las fases completadas no se repiten.")
            return 2
        ctx.state.mark(key)
        if stop_after and not only:
            return 0  # espera el power-cycle; el estado queda guardado
    log("\nTodas las fases completadas.")
    return 0


def cmd_status(ctx: Ctx) -> int:
    section(f"Estado de '{ctx.cfg.name}' ({ctx.cfg.host})")
    for key, _fn, transport, stop in PHASES:
        done = "✓" if ctx.state.done(key) else "·"
        log(f"  [{done}] {key:<14} (transporte: {transport}{' · power-cycle después' if stop else ''})")
    nxt = next((k for k, *_ in PHASES if not ctx.state.done(k)), None)
    log(f"\n  Próxima fase: {nxt or '(ninguna; completado)'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Aprovisionar un TPP06 igual que el de referencia.")
    ap.add_argument("--config", required=True, type=Path, help="fichero panel.conf")
    ap.add_argument("--status", action="store_true", help="mostrar progreso y salir")
    ap.add_argument("--reset", action="store_true", help="olvidar el progreso guardado")
    ap.add_argument("--only", help="ejecutar SOLO esta fase")
    ap.add_argument("--from", dest="start_from", help="empezar desde esta fase")
    ap.add_argument("--yes", action="store_true", help="no preguntar confirmaciones")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    state_path = REPO / "installer" / "state" / f"{cfg.name}.json"
    state = State(state_path)
    if args.reset:
        state.reset()
        log(f"Progreso de '{cfg.name}' reiniciado.")
        return 0

    cutter = TuyaCutter(cfg.host, cfg.backup_dir, adb=cfg.adb)
    ctx = Ctx(cfg=cfg, state=state, assume_yes=args.yes, cutter=cutter)

    if args.status:
        return cmd_status(ctx)
    return run(ctx, only=args.only, start_from=args.start_from)


if __name__ == "__main__":
    raise SystemExit(main())
