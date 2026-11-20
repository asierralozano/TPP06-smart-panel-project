#!/usr/bin/env python3
"""TPP06 1.2.3: prueba aislada de autorizacion USB, sin shell ni escrituras.

Usa solo CNXN y AUTH de ADB. No modifica ~/.android, el panel, ni su firmware.
La clave publica se lee de la imagen OTA local conocida, nunca de credenciales
del usuario. El adbd analizado compara AUTH tipo 3 con /etc/adbkey.pub.
Esto es una hipotesis comprobada estaticamente; la prueba confirma si aplica
al panel conectado. Solo para dispositivos propios o autorizados.

Antes de probar: adb kill-server (libera USB; no reinicia el panel).
Ejemplo: /usr/bin/python3 tpp06_usb_probe.py --serial 70f1c865d1587b56
--self-test valida el protocolo sin acceder a USB.
La autorizacion, si funciona, pertenece a esta conexion y termina al cerrar.
"""
from __future__ import annotations

import argparse
import base64
import ctypes as C
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import sys

OTA_SHA256 = '3ef8663dc2fcbe238dd77fe8df00da855d48058e19134c074697c586670d06cd'
KEY_SHA256 = 'e37c3c3583b6009dd392ae9918908d56ac39ce1659b07f1fad21089fd9a46249'
U8, U16, U32 = C.c_uint8, C.c_uint16, C.c_uint32


class ProbeError(Exception):
    pass


class DeviceDescriptor(C.Structure):
    _fields_ = [('length', U8), ('type', U8), ('usb', U16), ('cls', U8),
                ('subclass', U8), ('protocol', U8), ('max_packet', U8),
                ('vendor', U16), ('product', U16), ('release', U16),
                ('manufacturer', U8), ('product_name', U8), ('serial', U8),
                ('configurations', U8)]


class Endpoint(C.Structure):
    _fields_ = [('length', U8), ('type', U8), ('address', U8),
                ('attributes', U8), ('max_packet', U16), ('interval', U8),
                ('refresh', U8), ('sync', U8), ('extra', C.c_void_p),
                ('extra_length', C.c_int)]


class InterfaceDescriptor(C.Structure):
    _fields_ = [('length', U8), ('type', U8), ('number', U8), ('alternate', U8),
                ('endpoints_count', U8), ('cls', U8), ('subclass', U8),
                ('protocol', U8), ('string', U8), ('endpoints', C.POINTER(Endpoint)),
                ('extra', C.c_void_p), ('extra_length', C.c_int)]


class Interface(C.Structure):
    _fields_ = [('alternates', C.POINTER(InterfaceDescriptor)), ('count', C.c_int)]


class Configuration(C.Structure):
    _fields_ = [('length', U8), ('type', U8), ('total_length', U16),
                ('interfaces_count', U8), ('value', U8), ('string', U8),
                ('attributes', U8), ('max_power', U8),
                ('interfaces', C.POINTER(Interface)), ('extra', C.c_void_p),
                ('extra_length', C.c_int)]


def load_library():
    candidates = ['/opt/homebrew/opt/libusb/lib/libusb-1.0.dylib',
                  '/usr/local/opt/libusb/lib/libusb-1.0.dylib']
    path = next((p for p in candidates if Path(p).is_file()), None)
    if not path:
        raise ProbeError('No se encuentra libusb de Homebrew; no se ha instalado nada.')
    lib = C.CDLL(path)
    void, integer = C.c_void_p, C.c_int
    specs = {
        'init': ([C.POINTER(void)], integer),
        'exit': ([void], None),
        'get_device_list': ([void, C.POINTER(C.POINTER(void))], C.c_ssize_t),
        'free_device_list': ([C.POINTER(void), integer], None),
        'get_device_descriptor': ([void, C.POINTER(DeviceDescriptor)], integer),
        'open': ([void, C.POINTER(void)], integer),
        'close': ([void], None),
        'get_string_descriptor_ascii': ([void, U8, C.POINTER(U8), integer], integer),
        'get_active_config_descriptor': ([void, C.POINTER(C.POINTER(Configuration))], integer),
        'free_config_descriptor': ([C.POINTER(Configuration)], None),
        'claim_interface': ([void, integer], integer),
        'release_interface': ([void, integer], integer),
        'bulk_transfer': ([void, U8, C.POINTER(U8), integer, C.POINTER(integer), C.c_uint], integer),
        'error_name': ([integer], C.c_char_p),
    }
    for name, (args, result) in specs.items():
        fn = getattr(lib, 'libusb_' + name)
        fn.argtypes, fn.restype = args, result
    return lib


class USB:
    def __init__(self, serial):
        self.lib = load_library()
        self.context, self.handle = C.c_void_p(), C.c_void_p()
        self.initialized, self.claimed = False, False
        self.interface = None
        try:
            self.check(self.lib.libusb_init(C.byref(self.context)), 'Inicializar USB')
            self.initialized = True
            self.open_target(serial)
        except BaseException:
            self.close()
            raise

    def check(self, code, operation):
        if code < 0:
            reason = self.lib.libusb_error_name(code).decode()
            hint = ' Ejecuta adb kill-server y vuelve a probar.' if code == -6 else ''
            raise ProbeError(f'{operation}: {reason}.{hint}')
        return code

    def open_target(self, serial):
        devices = C.POINTER(C.c_void_p)()
        count = self.check(self.lib.libusb_get_device_list(self.context, C.byref(devices)), 'Enumerar USB')
        try:
            for index in range(count):
                device = devices[index]
                desc = DeviceDescriptor()
                if self.lib.libusb_get_device_descriptor(device, C.byref(desc)) < 0:
                    continue
                if (desc.vendor, desc.product) != (0x2207, 0x0006):
                    continue
                handle = C.c_void_p()
                self.check(self.lib.libusb_open(device, C.byref(handle)), 'Abrir USB Tuya')
                self.handle = handle
                buf = (U8 * 256)()
                size = self.lib.libusb_get_string_descriptor_ascii(handle, desc.serial, buf, len(buf))
                if size < 0 or bytes(buf[:size]).decode('ascii', 'replace') != serial:
                    self.lib.libusb_close(handle)
                    self.handle = C.c_void_p()
                    continue
                config = C.POINTER(Configuration)()
                self.check(self.lib.libusb_get_active_config_descriptor(device, C.byref(config)), 'Leer interfaces USB')
                try:
                    for i in range(config.contents.interfaces_count):
                        interface = config.contents.interfaces[i]
                        for j in range(interface.count):
                            alt = interface.alternates[j]
                            if (alt.cls, alt.subclass, alt.protocol, alt.alternate) != (255, 66, 1, 0):
                                continue
                            endpoints = [alt.endpoints[k].address for k in range(alt.endpoints_count)
                                         if alt.endpoints[k].attributes & 3 == 2]
                            incoming = [ep for ep in endpoints if ep & 128]
                            outgoing = [ep for ep in endpoints if not ep & 128]
                            if len(incoming) == len(outgoing) == 1:
                                self.interface = alt.number
                                self.ep_in, self.ep_out = incoming[0], outgoing[0]
                                break
                        if self.interface is not None:
                            break
                finally:
                    self.lib.libusb_free_config_descriptor(config)
                if self.interface is None:
                    raise ProbeError('No se encontro una interfaz ADB USB compatible.')
                self.check(self.lib.libusb_claim_interface(handle, self.interface), 'Reservar interfaz ADB')
                self.claimed = True
                return
            raise ProbeError('No aparece el panel con ese numero de serie y VID/PID Tuya.')
        finally:
            self.lib.libusb_free_device_list(devices, 1)

    def transfer(self, endpoint, data_or_length):
        if isinstance(data_or_length, int):
            buffer = (U8 * data_or_length)()
        else:
            buffer = (U8 * len(data_or_length)).from_buffer_copy(data_or_length)
        transferred = C.c_int()
        code = self.lib.libusb_bulk_transfer(self.handle, endpoint, buffer, len(buffer),
                                            C.byref(transferred), 5000)
        self.check(code, 'Transferencia USB')
        if not endpoint & 128 and transferred.value != len(buffer):
            raise ProbeError('Escritura USB incompleta; se cierra la prueba.')
        return bytes(buffer[:transferred.value])

    def write(self, data):
        self.transfer(self.ep_out, data)

    def read_exact(self, length):
        output = bytearray()
        while len(output) < length:
            chunk = self.transfer(self.ep_in, length - len(output))
            if not chunk:
                raise ProbeError('Lectura USB vacia; se cierra la prueba.')
            output.extend(chunk)
        return bytes(output)

    def close(self):
        if self.handle:
            if self.claimed:
                self.lib.libusb_release_interface(self.handle, self.interface)
                self.claimed = False
            self.lib.libusb_close(self.handle)
            self.handle = C.c_void_p()
        if self.initialized:
            self.lib.libusb_exit(self.context)
            self.initialized = False


def encode(command, arg0=0, arg1=0, payload=b''):
    value = int.from_bytes(command, 'little')
    header = struct.pack('<6I', value, arg0, arg1, len(payload), sum(payload) & 0xffffffff,
                         value ^ 0xffffffff)
    return header, payload


def send(transport, command, arg0=0, arg1=0, payload=b''):
    if command not in (b'CNXN', b'AUTH'):
        raise ProbeError('La prueba solo permite CNXN y AUTH; no permite shell ni comandos.')
    header, body = encode(command, arg0, arg1, payload)
    transport.write(header)
    if body:
        transport.write(body)


def receive(transport):
    command, arg0, arg1, length, checksum, magic = struct.unpack('<6I', transport.read_exact(24))
    if magic != command ^ 0xffffffff or length > 4096:
        raise ProbeError('Cabecera ADB invalida o demasiado grande.')
    payload = transport.read_exact(length) if length else b''
    if sum(payload) & 0xffffffff != checksum:
        raise ProbeError('Checksum ADB incorrecto.')
    return command.to_bytes(4, 'little'), arg0, arg1, payload


def probe(transport, key):
    send(transport, b'CNXN', 0x01000000, 4096, b'host::\0')
    reply = receive(transport)
    if reply[0] == b'CNXN':
        return 'ACEPTADA: el panel acepto CNXN sin pedir clave en esta conexion.'
    if reply[0] != b'AUTH' or reply[1] != 1 or len(reply[3]) != 20:
        raise ProbeError('Respuesta distinta del desafio AUTH esperado; prueba detenida.')
    print('Desafio ADB recibido. Presentando la clave PUBLICA de la imagen OTA.', flush=True)
    send(transport, b'AUTH', 3, 0, key + b'\0')
    reply = receive(transport)
    if reply[0] == b'CNXN':
        return 'ACEPTADA: el panel autorizo esta conexion con la clave publica del firmware.'
    if reply[0] == b'AUTH':
        raise ProbeError('El panel sigue solicitando autorizacion. La hipotesis no queda validada.')
    raise ProbeError('No se recibio CNXN tras presentar la clave; no se confirma autorizacion.')


def firmware_key(path):
    if not path.is_file():
        raise ProbeError(f'No existe la imagen OTA: {path}')
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != OTA_SHA256:
        raise ProbeError('La imagen no coincide con la OTA 1.2.3 analizada. No se accede a USB.')
    program = shutil.which('unsquashfs')
    if not program:
        raise ProbeError('Falta unsquashfs. No se ha instalado nada ni accedido a USB.')
    result = subprocess.run([program, '-cat', '-o', '1877352', str(path), 'etc/adbkey.pub'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    key = result.stdout
    if result.returncode or hashlib.sha256(key).hexdigest() != KEY_SHA256:
        raise ProbeError('No se obtuvo exactamente la clave publica esperada de la OTA.')
    # La imagen incluye NUL tras los 700 bytes Base64, antes del comentario.
    # fgets lo lee, pero strcmp solo compara el prefijo hasta ese NUL.
    key = key.split(b'\0', 1)[0]
    if len(base64.b64decode(key, validate=True)) != 524:
        raise ProbeError('Formato de clave publica inesperado.')
    return key


def self_test():
    class Fake:
        def __init__(self, frames):
            self.data = b''.join(b''.join(encode(*f)) for f in frames)
            self.sent = []
        def write(self, b):
            self.sent.append(b)
        def read_exact(self, n):
            b, self.data = self.data[:n], self.data[n:]
            if len(b) != n:
                raise ProbeError('Respuesta truncada')
            return b
    fake = Fake([(b'AUTH', 1, 0, b'x' * 20), (b'CNXN', 0x01000000, 4096, b'device::\0')])
    assert probe(fake, b'public-test-key').startswith('ACEPTADA:')
    assert len(fake.sent) == 4
    command, kind, _, length, _, _ = struct.unpack('<6I', fake.sent[2])
    assert command == int.from_bytes(b'AUTH', 'little') and kind == 3
    assert length == len(fake.sent[3]) and fake.sent[3] == b'public-test-key\0'
    assert probe(Fake([(b'CNXN', 0x01000000, 4096, b'device::\0')]), b'key').startswith('ACEPTADA:')
    for frames in [[(b'AUTH', 1, 0, b'x' * 20), (b'AUTH', 1, 0, b'y' * 20)],
                   [(b'CLSE', 0, 0, b'')], [(b'AUTH', 1, 0, b'short')]]:
        try:
            probe(Fake(frames), b'key')
        except ProbeError:
            pass
        else:
            raise AssertionError('Se acepto una respuesta que debia rechazarse')
    malformed = Fake([(b'CNXN', 0x01000000, 4096, b'x')])
    malformed.data = malformed.data[:-1] + b'y'
    try:
        receive(malformed)
    except ProbeError:
        pass
    else:
        raise AssertionError('Checksum invalido aceptado')
    try:
        send(Fake([]), b'OPEN', payload=b'shell:\0')
    except ProbeError:
        pass
    else:
        raise AssertionError('La prueba permitio abrir una shell')
    print('Pruebas offline correctas: aceptacion, rechazo, formato y ausencia de comandos de shell.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--serial', help='Numero de serie exacto del panel propio')
    parser.add_argument('--firmware', type=Path,
                        default=Path.home() / 'Downloads/tyznzk_TPP06_AK_OTA_1.2.3.img')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.serial:
        parser.error('--serial es obligatorio para seleccionar un unico panel')
    transport = None
    try:
        key = firmware_key(args.firmware)
        print('Imagen OTA y clave publica verificadas. No se usan claves privadas.', flush=True)
        transport = USB(args.serial)
        print('Interfaz USB ADB reservada. Probando la autorizacion...', flush=True)
        print(probe(transport, key))
        print('Prueba terminada: sin shell, sin login y sin escrituras persistentes.')
        return 0
    except (ProbeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'NO CONFIRMADO: {exc}', file=sys.stderr)
        return 1
    finally:
        if transport is not None:
            transport.close()


if __name__ == '__main__':
    sys.exit(main())
