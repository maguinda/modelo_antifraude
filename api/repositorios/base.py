"""La conexión a SQLite y las transacciones. Es la dueña del ÚNICO candado de escritura del proceso: todos los repositorios la comparten."""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


VERSION_MINIMA_SQLITE = (3, 35, 0)  # primera versión con `RETURNING`


class Base:
    def __init__(self, ruta: Path, durabilidad: str = "NORMAL"):
        """durabilidad: NORMAL (rápida; si se va la luz puede perderse lo último) o FULL (cada guardado se fuerza a disco)."""
        if durabilidad not in ("NORMAL", "FULL"):
            raise ValueError("durabilidad debe ser NORMAL o FULL")
        if sqlite3.sqlite_version_info < VERSION_MINIMA_SQLITE:  # `INSERT ... RETURNING` (cambio 7a) lo pide
            raise RuntimeError(
                f"Este programa necesita SQLite {'.'.join(map(str, VERSION_MINIMA_SQLITE))} o más reciente y esta computadora trae {sqlite3.sqlite_version}. "
                "Actualiza Python o SQLite."
            )
        self.ruta, self.durabilidad = Path(ruta), durabilidad
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._turno = (
            threading.Lock()
        )  # un solo guardado a la vez DENTRO de este proceso (en fila, sin pelear por la base)

    def abrir(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.ruta), timeout=30, isolation_level=None)  # manejamos las transacciones a mano
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        con.execute(f"PRAGMA synchronous={self.durabilidad}")
        return con

    @contextmanager
    def transaccion(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE: una sola escritura a la vez; lo leído dentro no cambia hasta el COMMIT.

        Dentro del proceso las escrituras hacen fila con un candado normal (así no compiten decenas de conexiones por el
        bloqueo de la base); entre procesos distintos manda el bloqueo de SQLite."""
        with self._turno:
            con = self.abrir()
            try:
                con.execute("BEGIN IMMEDIATE")
                try:
                    yield con
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
            finally:
                con.close()

    @contextmanager
    def lectura(self, instantanea: bool = False) -> Iterator[sqlite3.Connection]:
        """Lectura que no bloquea a nadie. Con instantanea=True, todo lo leído es de un mismo momento (una foto de la base)."""
        con = self.abrir()
        try:
            if instantanea:
                con.execute("BEGIN")
            yield con
        finally:
            con.close()
