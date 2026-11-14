"""Claves de API y las personas dueñas de ellas: crear, rotar, apagar y autenticar."""
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .auditoria import auditar
from .base import Base
from .comunes import PLATAFORMA, ahora_iso
from .seudonimos import Seudonimizador


@dataclass(frozen=True)
class Principal:
    clave_id: int
    comercio_id: int
    rol: str
    nombre: str
    usuario: str = ""           # para analistas y administradores: el nombre de la persona dueña de la clave


NOMBRE_MIN, NOMBRE_MAX = 3, 60
USO_CADA_SEGUNDOS = 3600        # el «último uso» de una clave se anota como mucho una vez por hora (para no escribir en cada petición)


def normalizar_nombre(nombre: str) -> str:
    n = re.sub(r"\s+", " ", str(nombre or "")).strip()
    if not NOMBRE_MIN <= len(n) <= NOMBRE_MAX or re.search(r"[\x00-\x1f\x7f<>]", n):
        raise ValueError(f"El nombre debe tener entre {NOMBRE_MIN} y {NOMBRE_MAX} caracteres y no llevar signos < o >.")
    return n


class ClavesRepositorio:
    def __init__(self, base: Base, seudonimos: Seudonimizador):
        self.base, self.seudonimos = base, seudonimos

    def comercio_suspendido_por_clave(self, clave: str) -> bool:
        """¿Esta clave (válida y activa) es de un comercio suspendido? Solo se pregunta cuando la autenticación falló."""
        with self.base.lectura() as con:
            return con.execute("SELECT 1 FROM claves k JOIN comercios c ON c.id = k.comercio_id WHERE k.hash=? AND k.activa=1 AND c.activo=0",
                               (self.seudonimos.hash_clave(clave),)).fetchone() is not None

    def crear_clave(self, comercio_id: int, rol: str, etiqueta: str = "", creada_por: Optional[int] = None) -> str:
        """Devuelve la clave en claro UNA vez; en la base solo queda su hash."""
        return self._crear_clave(comercio_id, rol, etiqueta, creada_por)[1]

    def _crear_clave(self, comercio_id: int, rol: str, etiqueta: str, creada_por: Optional[int], con: Optional[sqlite3.Connection] = None) -> Tuple[int, str]:
        clave = f"mia_{rol}_{secrets.token_urlsafe(32)}"

        def insertar(c: sqlite3.Connection) -> int:
            cur = c.execute("INSERT INTO claves (hash, comercio_id, rol, etiqueta, creada, creada_por) VALUES (?,?,?,?,?,?)",
                            (self.seudonimos.hash_clave(clave), comercio_id, rol, etiqueta, ahora_iso(), creada_por))
            auditar(c, comercio_id, int(cur.lastrowid), "clave_creada", None, {"rol": rol, "nombre": etiqueta or None, "creada_por": creada_por})
            return int(cur.lastrowid)
        if con is not None:
            return insertar(con), clave
        with self.base.transaccion() as c:
            return insertar(c), clave

    def crear_usuario(self, rol: str, nombre: str, creada_por: Optional[int] = None) -> Tuple[int, str]:
        """Una clave PARA UNA PERSONA (analista o administrador): lleva su nombre y no se puede repetir entre las activas.
        Devuelve (id de la clave, clave en claro). La clave se muestra una sola vez."""
        if rol not in ("analista", "admin"):
            raise ValueError("Solo los analistas y administradores son personas con nombre.")
        nombre = normalizar_nombre(nombre)
        with self.base.transaccion() as con:
            activos = [r[0] or "" for r in con.execute("SELECT etiqueta FROM claves WHERE comercio_id=? AND activa=1 AND rol IN ('analista','admin')", (PLATAFORMA,))]
            if any(re.sub(r"\s+", " ", a).strip().casefold() == nombre.casefold() for a in activos):      # en Python: el lower() de SQLite no reconoce las mayúsculas con tilde
                raise ValueError("Ya hay una clave activa con ese nombre. Si es la misma persona, usa «rotar-clave»; si es otra, usa un nombre distinto.")
            return self._crear_clave(PLATAFORMA, rol, nombre, creada_por, con)

    def rotar_clave(self, clave_id: int, creada_por: Optional[int] = None) -> Tuple[int, str]:
        """Cambia la clave de una persona: crea una nueva con el mismo rol y nombre y apaga la anterior, en un solo paso."""
        with self.base.transaccion() as con:
            v = con.execute("SELECT * FROM claves WHERE id=? AND activa=1", (clave_id,)).fetchone()
            if v is None:
                raise ValueError("No existe una clave activa con ese número.")
            con.execute("UPDATE claves SET activa=0 WHERE id=?", (clave_id,))
            auditar(con, v["comercio_id"], clave_id, "clave_desactivada", None, {"motivo": "rotación"})
            return self._crear_clave(v["comercio_id"], v["rol"], v["etiqueta"] or "", creada_por, con)

    def listar_claves(self) -> List[Dict[str, Any]]:
        """Para administrar: nunca incluye la clave ni su hash."""
        with self.base.lectura() as con:
            return [dict(r) for r in con.execute(
                "SELECT k.id, k.rol, k.comercio_id, c.nombre AS comercio, k.etiqueta, k.activa, k.creada, k.ultimo_uso, k.creada_por "
                "FROM claves k JOIN comercios c ON c.id = k.comercio_id ORDER BY k.activa DESC, k.rol, k.id")]

    def autenticar(self, clave: str) -> Optional[Principal]:
        with self.base.lectura() as con:
            fila = con.execute(
                "SELECT k.id, k.comercio_id, k.rol, c.nombre, k.etiqueta, k.ultimo_uso FROM claves k JOIN comercios c ON c.id = k.comercio_id "
                "WHERE k.hash = ? AND k.activa = 1 AND c.activo = 1", (self.seudonimos.hash_clave(clave),)).fetchone()
        if fila is None:
            return None
        self._anotar_uso(fila["id"], fila["ultimo_uso"])
        return Principal(fila["id"], fila["comercio_id"], fila["rol"], fila["nombre"], (fila["etiqueta"] or "") if fila["rol"] in ("analista", "admin") else "")

    def _anotar_uso(self, clave_id: int, ultimo: Optional[str]) -> None:
        """Anota cuándo se usó la clave por última vez, como mucho una vez por hora; si no se puede escribir en ese momento, no pasa nada."""
        try:
            if ultimo and (datetime.now() - datetime.fromisoformat(ultimo)).total_seconds() < USO_CADA_SEGUNDOS:
                return
            with self.base.transaccion() as con:
                con.execute("UPDATE claves SET ultimo_uso=? WHERE id=?", (ahora_iso(), clave_id))
        except (sqlite3.Error, ValueError):
            pass

    def desactivar_clave(self, clave_id: int) -> None:
        with self.base.transaccion() as con:
            con.execute("UPDATE claves SET activa = 0 WHERE id = ?", (clave_id,))
            auditar(con, None, clave_id, "clave_desactivada", None, {})
