"""Piezas pequeñas que comparten todos los repositorios."""
import re
import secrets
from datetime import datetime
from typing import Any, Callable

PLATAFORMA = 1          # id del «comercio» al que pertenecen las claves de analistas y administradores


def texto_limpio(valor: Any, minimo: int, maximo: int, nombre: str) -> str:
    """Texto libre limpio: sin espacios de más, sin caracteres de control ni < >, y dentro del largo permitido."""
    t = re.sub(r"\s+", " ", str(valor or "")).strip()
    if not minimo <= len(t) <= maximo or re.search(r"[\x00-\x1f\x7f<>]", t):
        raise ValueError(f"{nombre} debe tener entre {minimo} y {maximo} caracteres y no llevar signos < o >.")
    return t


def ahora_iso() -> str:
    return datetime.now().isoformat(timespec="microseconds")


ALFABETO_SEGUIMIENTO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"        # sin I, L, O, U: no se confunden al leerlos por teléfono


def id_seguimiento_libre(ya_existe: Callable[[str], bool]) -> str:
    """Un identificador de seguimiento que `ya_existe` no reconoce. Con tope de intentos: un generador roto da un error claro y no un ciclo infinito."""
    for _ in range(20):
        candidato = nuevo_id_seguimiento()
        if not ya_existe(candidato):
            return candidato
    raise RuntimeError("No se pudo generar un identificador de seguimiento único tras 20 intentos.")


def nuevo_id_seguimiento() -> str:
    """Identificador propio de cada pago, legible: AF-7K3Q-9M2X (40 bits al azar; quien lo guarda verifica que no exista)."""
    c = "".join(secrets.choice(ALFABETO_SEGUIMIENTO) for _ in range(8))
    return f"AF-{c[:4]}-{c[4:]}"
