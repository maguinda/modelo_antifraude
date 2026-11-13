"""Límite de peticiones por clave (ventana deslizante de un minuto)."""
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple


class LimitadorTasa:
    def __init__(self, maximo_por_minuto: int):
        self.maximo = maximo_por_minuto
        self._tiempos: Dict[int, Deque[float]] = defaultdict(deque)
        self._candado = threading.Lock()

    def permitir(self, clave_id: int, ahora: float = None) -> Tuple[bool, int]:
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
