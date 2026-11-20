#!/usr/bin/env python3
"""Consola USB para el TPP06 1.2.3 propio, usando la autorizacion comprobada.

Requiere tpp06_usb_probe.py en la misma carpeta, libusb y la imagen OTA local.
No instala nada en el panel, no cambia claves ni configura el arranque.
Abre shell: y transmite solo lo que escriba el usuario. Los comandos que este
ejecute en la consola SI pueden modificar el panel. No guarda sesiones ni claves.
Salir de esta conexion: Ctrl+] (tambien --detach-after para pruebas).
Antes de conectar: adb kill-server. No ejecutar adb mientras esta consola este abierta.
"""
from __future__ import annotations

import argparse
import ctypes as C
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import termios
import time
import tty

from tpp06_usb_probe import ProbeError, USB, U8, encode, firmware_key, probe


class Packets:
    """Conserva lecturas parciales entre timeouts USB, sin perder bytes."""
    def __init__(self):
        self.pending = bytearray()

    def feed(self, data):
        self.pending.extend(data)
        messages = []
        while len(self.pending) >= 24:
            cmd, a0, a1, length, checksum, magic = struct.unpack('<6I', self.pending[:24])
            if magic != cmd ^ 0xffffffff or length > 4096:
                raise ProbeError('Cabecera ADB invalida.')
            if len(self.pending) < 24 + length:
                break
            body = bytes(self.pending[24:24 + length])
            del self.pending[:24 + length]
            if sum(body) & 0xffffffff != checksum:
                raise ProbeError('Checksum ADB incorrecto.')
            messages.append((cmd.to_bytes(4, 'little'), a0, a1, body))
        return messages


def send_stream(usb, cmd, a0, a1, body=b''):
    if cmd not in (b'OPEN', b'OKAY', b'WRTE', b'CLSE'):
        raise ProbeError('Operacion de consola no permitida.')
    header, payload = encode(cmd, a0, a1, body)
    usb.write(header)
    if payload:
        usb.write(payload)


def poll_usb(usb):
    data = (U8 * 4096)()
    size = C.c_int()
    code = usb.lib.libusb_bulk_transfer(usb.handle, usb.ep_in, data, len(data),
                                      C.byref(size), 50)
    if code != -7:  # LIBUSB_ERROR_TIMEOUT puede incluir bytes parciales.
        usb.check(code, 'Leer consola USB')
    return bytes(data[:size.value])


class ShellStream:
    def __init__(self, usb, output):
        self.usb, self.output = usb, output
        self.local, self.remote = 1, None
        self.waiting_ack, self.closed = False, False
        self.queue = bytearray()

    def open(self):
        send_stream(self.usb, b'OPEN', self.local, 0, b'shell:\0')

    def handle(self, packet):
        cmd, a0, a1, body = packet
        if a1 != self.local:
            raise ProbeError('Respuesta destinada a otro canal ADB.')
        if cmd == b'CLSE':
            if self.remote is not None and a0 not in (0, self.remote):
                raise ProbeError('Cierre de otro canal ADB.')
            send_stream(self.usb, b'CLSE', self.local, a0)
            self.closed = True
            return
        if cmd not in (b'OKAY', b'WRTE') or a0 == 0:
            raise ProbeError('Respuesta de consola inesperada.')
        if self.remote is None:
            if cmd != b'OKAY':
                raise ProbeError('Llegaron datos antes de aceptar la apertura de consola.')
            self.remote = a0
        elif a0 != self.remote:
            raise ProbeError('Cambio inesperado de canal remoto.')
        if cmd == b'OKAY':
            self.waiting_ack = False
        else:
            self.output(body)
            send_stream(self.usb, b'OKAY', self.local, self.remote)
        self.flush()

    def input(self, data):
        if len(self.queue) + len(data) > 16384:
            raise ProbeError('Demasiada entrada pendiente de confirmacion; se cierra la consola.')
        self.queue.extend(data)
        self.flush()

    def flush(self):
        if not self.closed and self.remote and self.queue and not self.waiting_ack:
            body = bytes(self.queue[:4096])
            del self.queue[:len(body)]
            send_stream(self.usb, b'WRTE', self.local, self.remote, body)
            self.waiting_ack = True

    def close(self):
        if self.remote and not self.closed:
            try:
                send_stream(self.usb, b'CLSE', self.local, self.remote)
            except (ProbeError, OSError):
                pass
        self.closed = True
        self.queue.clear()


def console(usb, detach_after=None):
    fd = sys.stdin.fileno()
    if not os.isatty(fd) or not sys.stdout.isatty():
        raise ProbeError('Ejecuta esta consola en una terminal interactiva, sin redirecciones.')
    print('Abriendo consola. Escribe root cuando aparezca login y tu contrasena cuando se solicite.')
    print('No se graba la sesion. Para desconectar: Ctrl+] (Control + corchete derecho).', flush=True)
    stream = ShellStream(usb, lambda data: os.write(sys.stdout.fileno(), data))
    packets = Packets()
    previous = termios.tcgetattr(fd)
    started = time.monotonic()
    try:
        # Sin eco local: la contrasena no se muestra al escribirla. El eco de
        # usuario/comandos lo gestiona la terminal remota, como en adb shell.
        tty.setraw(fd)
        stream.open()
        while not stream.closed:
            for packet in packets.feed(poll_usb(usb)):
                stream.handle(packet)
                if stream.closed:
                    break
            if stream.closed:
                break
            if stream.remote is None and time.monotonic() - started > 10:
                raise ProbeError('El panel no ha confirmado la apertura de shell en 10 segundos.')
            if detach_after and time.monotonic() - started >= detach_after:
                break
            if select.select([fd], [], [], 0)[0]:
                data = os.read(fd, 1024)
                if not data or b'\x1d' in data:
                    break
                stream.input(data)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
        stream.close()
        print('\nConexion USB cerrada.', flush=True)


def self_test():
    class Fake:
        def __init__(self):
            self.sent = []
        def write(self, data):
            self.sent.append(data)

    frames = [(b'OKAY', 7, 1, b''), (b'WRTE', 7, 1, b'Tuya login: ')]
    wire = b''.join(b''.join(encode(*f)) for f in frames)
    packets = Packets()
    actual = []
    for byte in wire:
        actual.extend(packets.feed(bytes([byte])))
        assert packets.feed(b'') == []  # Timeouts entre lecturas parciales.
    assert actual == frames and not packets.pending
    assert Packets().feed(wire) == frames
    usb, output = Fake(), []
    stream = ShellStream(usb, output.append)
    stream.open()
    stream.input(b'root\r')
    assert len(usb.sent) == 2  # No enviar teclas antes de OPEN/OKAY.
    stream.handle(frames[0])
    assert usb.sent[-1] == b'root\r' and stream.waiting_ack
    stream.input(b'test-only\r')
    assert usb.sent[-1] == b'root\r'  # Esperar ACK antes de otro WRTE.
    stream.handle(frames[1])
    assert output == [b'Tuya login: '] and stream.waiting_ack
    stream.handle((b'OKAY', 7, 1, b''))
    assert usb.sent[-1] == b'test-only\r' and stream.waiting_ack
    stream.handle((b'CLSE', 7, 1, b''))
    assert stream.closed
    for bad in [(b'WRTE', 8, 1, b'x'), (b'OKAY', 7, 9, b'')]:
        fresh = ShellStream(Fake(), output.append)
        fresh.handle((b'OKAY', 7, 1, b''))
        try:
            fresh.handle(bad)
        except ProbeError:
            pass
        else:
            raise AssertionError('Se acepto otro canal')
    malformed = b''.join(encode(b'WRTE', 7, 1, b'x'))[:-1] + b'y'
    try:
        Packets().feed(malformed)
    except ProbeError:
        pass
    else:
        raise AssertionError('Checksum invalido aceptado')
    print('Pruebas offline correctas: fragmentacion, timeouts, ACK, canales y cierre.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--serial', help='Numero de serie exacto del panel propio')
    parser.add_argument('--firmware', type=Path,
                        default=Path.home() / 'Downloads/tyznzk_TPP06_AK_OTA_1.2.3.img')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--detach-after', type=float, help='Desconectar despues de N segundos (opcional)')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.serial:
        parser.error('--serial es obligatorio')
    if args.detach_after is not None and args.detach_after <= 0:
        parser.error('--detach-after debe ser mayor que cero')
    usb = None
    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ProbeError('Se requiere una terminal interactiva; no se accede a USB.')
        key = firmware_key(args.firmware)
        usb = USB(args.serial)
        print(probe(usb, key))
        console(usb, args.detach_after)
        return 0
    except (ProbeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nCancelado.')
        return 130
    finally:
        if usb is not None:
            usb.close()


if __name__ == '__main__':
    sys.exit(main())
