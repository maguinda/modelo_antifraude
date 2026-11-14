"""Segundo factor por TOTP (RFC 6238): el código de 6 dígitos que genera una app de autenticación y cambia cada 30 segundos.

Se implementa con la biblioteca estándar (HMAC-SHA1, RFC 4226) para no sumar una dependencia; una prueba lo comprueba contra los vectores oficiales de la RFC.

Qué garantiza `verificar`:
  - el código debe tener exactamente 6 dígitos ASCII (cualquier otra cosa se rechaza sin calcular nada);
  - se aceptan el paso actual y uno anterior y uno posterior (desfase de reloj de hasta ~30 s), comparando en tiempo constante;
  - un código NO se puede usar dos veces ni uno más viejo que el último aceptado: solo se aceptan pasos MAYORES que `ultimo_paso`.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from typing import Optional
from urllib.parse import quote

PASO_SEGUNDOS = 30
DIGITOS = 6
VENTANA = 1  # pasos de tolerancia a cada lado del actual


def nuevo_secreto() -> str:
    """160 bits al azar (lo que recomienda la RFC 4226) en base32, que es lo que aceptan las apps de autenticación."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def _hotp(clave: bytes, contador: int, digitos: int = DIGITOS) -> str:
    resumen = hmac.new(clave, struct.pack(">Q", contador), hashlib.sha1).digest()
    desplazamiento = resumen[-1] & 0x0F
    numero = struct.unpack(">I", resumen[desplazamiento : desplazamiento + 4])[0] & 0x7FFFFFFF
    return str(numero % (10**digitos)).zfill(digitos)


def paso_de(epoch_segundos: float) -> int:
    return int(epoch_segundos // PASO_SEGUNDOS)


def _clave(secreto_b32: str) -> bytes:
    return base64.b32decode(secreto_b32.strip().replace(" ", "").upper(), casefold=True)


def codigo(secreto_b32: str, epoch_segundos: float, digitos: int = DIGITOS) -> str:
    """El código que corresponde a ese instante (lo usan las pruebas y quien quiera generar uno a mano)."""
    return _hotp(_clave(secreto_b32), paso_de(epoch_segundos), digitos)


def verificar(
    secreto_b32: str, ingresado: object, epoch_segundos: float, ultimo_paso: int = 0, ventana: int = VENTANA
) -> Optional[int]:
    """El paso (contador) del código si es válido y NO repetido; None si no. No lanza excepciones por entradas raras."""
    if not isinstance(ingresado, str) or len(ingresado) != DIGITOS or not (ingresado.isascii() and ingresado.isdigit()):
        return None
    clave = _clave(secreto_b32)
    actual = paso_de(epoch_segundos)
    aceptado: Optional[int] = None
    for paso in range(actual - ventana, actual + ventana + 1):  # se recorren TODOS: no se corta al primer acierto
        if hmac.compare_digest(_hotp(clave, paso).encode("ascii"), ingresado.encode("ascii")):
            aceptado = paso
    if aceptado is None or aceptado <= ultimo_paso:
        return None
    return aceptado


def uri(secreto_b32: str, cuenta: str, emisor: str = "Antifraude pymes") -> str:
    """El enlace `otpauth://` que leen las apps de autenticación (también sirve para generar un QR)."""
    etiqueta = quote(f"{emisor}:{cuenta}", safe="")
    return (
        f"otpauth://totp/{etiqueta}?secret={secreto_b32}&issuer={quote(emisor, safe='')}"
        f"&algorithm=SHA1&digits={DIGITOS}&period={PASO_SEGUNDOS}"
    )
