#!/usr/bin/env python3
"""Ejecutor NO interactivo de un comando en el TPP06 por USB (ADB + login).

Reutiliza tpp06_usb_probe (transporte USB y AUTH con la clave publica del
firmware) y el stream de consola de tpp06_usb_shell. Abre 'shell:', automatiza
el login (root + contrasena), ejecuta UN comando y captura su salida y codigo
de salida con centinelas robustos frente al eco del terminal.

La contrasena se lee de la variable de entorno TPP06_PW (nunca por argv, para
que no aparezca en 'ps'). El comando SI va por argv (no es sensible).

Uso:
    TPP06_PW=xxxx python3 tpp06_exec.py --serial 70f1c865d1587b56 -- "cat /proc/mtd"

Opciones:
    --serial    numero de serie del panel (obligatorio)
    --firmware  ruta de la OTA para verificar/extraer la clave (por defecto ~/Downloads)
    --timeout   segundos maximos totales (por defecto 120, o TPP06_TIMEOUT)
    --raw       imprime la sesion cruda en stderr (depuracion)

Salida: la del comando por stdout; el codigo de salida del proceso = codigo del
comando remoto (o 124 si hay timeout, 1 si error de acceso).
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from pathlib import Path

from tpp06_usb_probe import ProbeError, USB, firmware_key, probe
from tpp06_usb_shell import Packets, ShellStream, poll_usb


class ExecError(Exception):
    pass


def run_command(usb, password, command, timeout, raw=False):
    session = bytearray()          # toda la salida cruda recibida
    captured = bytearray()
    stream = ShellStream(usb, lambda d: (session.extend(d),
                                         sys.stderr.buffer.write(d) if raw else None))
    packets = Packets()
    stream.open()

    nonce = secrets.token_hex(4)
    # Centinelas partidos: el texto TECLEADO lleva comillas intermedias, asi que
    # su eco NO coincide con el centinela real que imprime 'echo'.
    begin = f"RSTART{nonce}"
    end = f"REND{nonce}"
    typed_begin = f'"RST""ART{nonce}"'   # produce RSTART<nonce> en la salida
    typed_end = f'"RE""ND{nonce}"'       # produce REND<nonce> en la salida
    batch = (f'echo {typed_begin}; {command}; echo {typed_end}:$?; exit\n')

    state = "login"        # login -> password -> prompt -> run -> capture -> done
    sent_cmd = False
    deadline = time.monotonic() + timeout
    phase_mark = time.monotonic()

    def text():
        return bytes(session).decode("utf-8", "replace")

    while not stream.closed:
        if time.monotonic() > deadline:
            stream.close()
            raise ExecError("timeout")
        for packet in packets.feed(poll_usb(usb)):
            stream.handle(packet)
            if stream.closed:
                break
        # En 'capture' NO decodificamos el buffer (puede ser enorme: O(n^2)).
        # El comando termina con 'exit' -> CLSE cierra el stream y sale del bucle.
        if state == "capture":
            continue
        buf = text()

        if state == "login":
            if "login:" in buf.lower():
                stream.input(b"root\n")
                state = "password"
                phase_mark = time.monotonic()
                session.clear()
            elif ("]$" in buf or "]#" in buf) and time.monotonic() - phase_mark > 1.5:
                state = "prompt"   # ya habia shell sin login
        elif state == "password":
            low = buf.lower()
            if "password" in low or "assword" in low:
                stream.input(password.encode() + b"\n")
                state = "prompt"
                phase_mark = time.monotonic()
                session.clear()
            elif "incorrect" in low or "failure" in low:
                raise ExecError("login incorrecto")
        elif state == "prompt":
            low = buf.lower()
            if "incorrect" in low or "login incorrect" in low:
                raise ExecError("contrasena incorrecta")
            ready = ("]$" in buf or "]#" in buf or "welcome to file system" in low)
            if (ready or time.monotonic() - phase_mark > 5) and not sent_cmd:
                stream.input(batch.encode())
                sent_cmd = True
                state = "capture"
                session.clear()
        elif state == "capture":
            if begin in buf and end in buf:
                pass  # se cerrara con 'exit' (CLSE); extraemos al final

    # Extraer salida entre centinelas
    full = text()
    code = 0
    if begin in full:
        rest = full.split(begin, 1)[1]
        # quitar el primer salto de linea tras el centinela
        if rest.startswith("\r\n"):
            rest = rest[2:]
        elif rest.startswith("\n"):
            rest = rest[1:]
        if end in rest:
            body, tail = rest.split(end, 1)
            captured.extend(body.encode("utf-8", "replace"))
            # tail = ":<code>\r\n..."
            tail = tail.lstrip(":")
            num = ""
            for ch in tail:
                if ch.isdigit():
                    num += ch
                else:
                    break
            code = int(num) if num else 0
        else:
            captured.extend(rest.encode("utf-8", "replace"))
            code = 124
    else:
        raise ExecError("no se recibio el centinela de inicio (login fallido o shell distinta)")
    return bytes(captured), code


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--firmware", type=Path,
                        default=Path.home() / "Downloads/tyznzk_TPP06_AK_OTA_1.2.3.img")
    parser.add_argument("--timeout", type=float,
                        default=float(os.environ.get("TPP06_TIMEOUT", "120")))
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("command", nargs="+", help="comando remoto a ejecutar")
    args = parser.parse_args()

    password = os.environ.get("TPP06_PW")
    if not password:
        print("ERROR: define la contrasena en la variable TPP06_PW.", file=sys.stderr)
        return 1

    command = " ".join(args.command)
    transport = None
    try:
        key = firmware_key(args.firmware)
        transport = USB(args.serial)
        _real_stdout = sys.stdout
        sys.stdout = sys.stderr          # el ruido de probe() va a stderr
        try:
            result = probe(transport, key)
        finally:
            sys.stdout = _real_stdout
        if not result.startswith("ACEPTADA"):
            print(f"AUTH no aceptada: {result}", file=sys.stderr)
            return 1
        output, code = run_command(transport, password, command, args.timeout, args.raw)
        sys.stdout.buffer.write(output)
        sys.stdout.flush()
        return code
    except (ProbeError, ExecError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if transport is not None:
            transport.close()


if __name__ == "__main__":
    sys.exit(main())
