"""Límites de peticiones: por clave (válida) y por IP para las claves inválidas. Ventana deslizante de un minuto, en memoria de cada proceso."""

import threading
import time
from collections import OrderedDict, defaultdict, deque
from typing import Deque, Dict, Optional, Tuple


class LimitadorTasa:
    def __init__(self, maximo_por_minuto: int):
        self.maximo = maximo_por_minuto
        self._tiempos: Dict[int, Deque[float]] = defaultdict(deque)
        self._candado = threading.Lock()

    def permitir(self, clave_id: int, ahora: Optional[float] = None) -> Tuple[bool, int]:
        """(permitido, segundos_para_reintentar)."""
        ahora = time.monotonic() if ahora is None else ahora
        with self._candado:
            cola = self._tiempos[clave_id]
            while cola and ahora - cola[0] >= 60:
                cola.popleft()
            if len(cola) >= self.maximo:
                return False, max(1, int(60 - (ahora - cola[0])) + 1)
            cola.append(ahora)
            return True, 0


class LimitadorClavesInvalidas:
    """Cuenta los intentos con una clave que NO existe, por IP. Pasado el máximo por minuto, los siguientes intentos reciben 429.

    No bloquea a nadie: solo frena el ritmo de los intentos inválidos (las claves válidas ni siquiera pasan por aquí). La IP «desconocida» no se limita.
    La tabla tiene tope: si alguien inventa millones de IP no crece la memoria; se olvidan las más antiguas."""

    def __init__(self, maximo_por_minuto: int, max_ips: int = 10_000):
        self.maximo, self.max_ips = maximo_por_minuto, max_ips
        self._por_ip: "OrderedDict[str, Deque[float]]" = OrderedDict()
        self._candado = threading.Lock()

    def registrar(self, ip: str, ahora: Optional[float] = None) -> Tuple[bool, int]:
        """(permitido, segundos_para_reintentar). Un intento permitido queda contado; uno rechazado no extiende el castigo."""
        if ip == "desconocida":
            return True, 0
        ahora = time.monotonic() if ahora is None else ahora
        with self._candado:
            cola = self._por_ip.get(ip)
            if cola is None:
                cola = self._por_ip[ip] = deque()
                while len(self._por_ip) > self.max_ips:
                    self._por_ip.popitem(last=False)
            else:
                self._por_ip.move_to_end(ip)
            while cola and ahora - cola[0] >= 60:
                cola.popleft()
            if len(cola) >= self.maximo:
                return False, max(1, int(60 - (ahora - cola[0])) + 1)
            cola.append(ahora)
            return True, 0

    def ips_en_memoria(self) -> int:
        with self._candado:
            return len(self._por_ip)
