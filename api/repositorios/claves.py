"""Claves de API y las personas dueñas de ellas: crear, rotar, apagar y autenticar."""

import math
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .. import totp
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
    usuario: str = ""  # para analistas y administradores: el nombre de la persona dueña de la clave


NOMBRE_MIN, NOMBRE_MAX = 3, 60
MFA_FALLOS_MAXIMOS = 5  # códigos malos seguidos que bloquean la clave
MFA_BLOQUEO_SEGUNDOS = 15 * 60
USO_CADA_SEGUNDOS = (
    3600  # el «último uso» de una clave se anota como mucho una vez por hora (para no escribir en cada petición)
)


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
            return (
                con.execute(
                    "SELECT 1 FROM claves k JOIN comercios c ON c.id = k.comercio_id WHERE k.hash=? AND k.activa=1 AND c.activo=0",
                    (self.seudonimos.hash_clave(clave),),
                ).fetchone()
                is not None
            )

    def crear_clave(self, comercio_id: int, rol: str, etiqueta: str = "", creada_por: Optional[int] = None) -> str:
        """Devuelve la clave en claro UNA vez; en la base solo queda su hash."""
        return self._crear_clave(comercio_id, rol, etiqueta, creada_por)[1]

    def _crear_clave(
        self,
        comercio_id: int,
        rol: str,
        etiqueta: str,
        creada_por: Optional[int],
        con: Optional[sqlite3.Connection] = None,
    ) -> Tuple[int, str]:
        clave = f"mia_{rol}_{secrets.token_urlsafe(32)}"

        def insertar(c: sqlite3.Connection) -> int:
            cur = c.execute(
                "INSERT INTO claves (hash, comercio_id, rol, etiqueta, creada, creada_por) VALUES (?,?,?,?,?,?) RETURNING id",
                (self.seudonimos.hash_clave(clave), comercio_id, rol, etiqueta, ahora_iso(), creada_por),
            )
            nueva = int(cur.fetchone()[0])
            auditar(
                c,
                comercio_id,
                nueva,
                "clave_creada",
                None,
                {"rol": rol, "nombre": etiqueta or None, "creada_por": creada_por},
            )
            return nueva

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
            activos = [
                r[0] or ""
                for r in con.execute(
                    "SELECT etiqueta FROM claves WHERE comercio_id=? AND activa=1 AND rol IN ('analista','admin')",
                    (PLATAFORMA,),
                )
            ]
            if any(
                re.sub(r"\s+", " ", a).strip().casefold() == nombre.casefold() for a in activos
            ):  # en Python: el lower() de SQLite no reconoce las mayúsculas con tilde
                raise ValueError(
                    "Ya hay una clave activa con ese nombre. Si es la misma persona, usa «rotar-clave»; si es otra, usa un nombre distinto."
                )
            return self._crear_clave(PLATAFORMA, rol, nombre, creada_por, con)

    def rotar_clave(self, clave_id: int, creada_por: Optional[int] = None) -> Tuple[int, str]:
        """Cambia la clave de una persona: crea una nueva con el mismo rol y nombre y apaga la anterior, en un solo paso."""
        with self.base.transaccion() as con:
            v = con.execute("SELECT * FROM claves WHERE id=? AND activa=1", (clave_id,)).fetchone()
            if v is None:
                raise ValueError("No existe una clave activa con ese número.")
            con.execute(
                "UPDATE claves SET activa=0, mfa_secreto=NULL, mfa_confirmada=0, mfa_ultimo_paso=0, mfa_fallos=0, mfa_bloqueo_hasta=NULL WHERE id=?",
                (clave_id,),
            )  # su secreto TOTP no se queda huérfano
            auditar(con, v["comercio_id"], clave_id, "clave_desactivada", None, {"motivo": "rotación"})
            return self._crear_clave(v["comercio_id"], v["rol"], v["etiqueta"] or "", creada_por, con)

    def listar_claves(self) -> List[Dict[str, Any]]:
        """Para administrar: nunca incluye la clave ni su hash."""
        with self.base.lectura() as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT k.id, k.rol, k.comercio_id, c.nombre AS comercio, k.etiqueta, k.activa, k.creada, k.ultimo_uso, k.creada_por "
                    "FROM claves k JOIN comercios c ON c.id = k.comercio_id ORDER BY k.activa DESC, k.rol, k.id"
                )
            ]

    def autenticar(self, clave: str) -> Optional[Principal]:
        with self.base.lectura() as con:
            fila = con.execute(
                "SELECT k.id, k.comercio_id, k.rol, c.nombre, k.etiqueta, k.ultimo_uso FROM claves k JOIN comercios c ON c.id = k.comercio_id "
                "WHERE k.hash = ? AND k.activa = 1 AND c.activo = 1",
                (self.seudonimos.hash_clave(clave),),
            ).fetchone()
        if fila is None:
            return None
        self._anotar_uso(fila["id"], fila["ultimo_uso"])
        return Principal(
            fila["id"],
            fila["comercio_id"],
            fila["rol"],
            fila["nombre"],
            (fila["etiqueta"] or "") if fila["rol"] in ("analista", "admin") else "",
        )

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
            con.execute(
                "UPDATE claves SET activa = 0, mfa_secreto=NULL, mfa_confirmada=0, mfa_ultimo_paso=0, mfa_fallos=0, mfa_bloqueo_hasta=NULL WHERE id = ?",
                (clave_id,),
            )  # su secreto TOTP no se queda huérfano
            auditar(con, None, clave_id, "clave_desactivada", None, {})

    # ------------------------------------------------------------------ segundo factor (TOTP) del administrador
    def mfa_iniciar(self, clave_id: int) -> str:
        """Crea un secreto nuevo, PENDIENTE de confirmar con un código (así un error al copiarlo no deja a nadie fuera). Solo claves de administrador activas."""
        with self.base.transaccion() as con:
            fila = con.execute(
                "SELECT rol, activa, comercio_id, mfa_confirmada FROM claves WHERE id=?", (clave_id,)
            ).fetchone()
            if fila is None or not fila["activa"]:
                raise ValueError("No existe una clave activa con ese número.")
            if fila["rol"] != "admin":
                raise ValueError("El segundo factor es solo para claves de administrador.")
            if fila["mfa_confirmada"]:
                raise ValueError(
                    "Esa clave ya tiene el segundo factor activo. Desactívalo primero si quieres cambiarlo."
                )
            secreto = totp.nuevo_secreto()
            con.execute(
                "UPDATE claves SET mfa_secreto=?, mfa_confirmada=0, mfa_ultimo_paso=0, mfa_fallos=0, mfa_bloqueo_hasta=NULL WHERE id=?",
                (secreto, clave_id),
            )
            auditar(con, fila["comercio_id"], clave_id, "mfa_iniciado", None, {})
        return secreto

    def mfa_confirmar(self, clave_id: int, ingresado: str, epoch: float) -> bool:
        """Activa el segundo factor si el código es válido para el secreto pendiente. Devuelve si quedó activo."""
        with self.base.transaccion() as con:
            fila = con.execute(
                "SELECT mfa_secreto, mfa_confirmada, comercio_id FROM claves WHERE id=? AND activa=1", (clave_id,)
            ).fetchone()
            if fila is None or not fila["mfa_secreto"]:
                raise ValueError("Esa clave no tiene un segundo factor pendiente. Usa «activar-mfa» primero.")
            if fila["mfa_confirmada"]:
                raise ValueError("Esa clave ya tiene el segundo factor activo.")
            paso = totp.verificar(fila["mfa_secreto"], ingresado, epoch)
            if paso is None:
                auditar(con, fila["comercio_id"], clave_id, "mfa_confirmacion_fallida", None, {})
                return False
            con.execute("UPDATE claves SET mfa_confirmada=1, mfa_ultimo_paso=? WHERE id=?", (paso, clave_id))
            auditar(con, fila["comercio_id"], clave_id, "mfa_activado", None, {})
            return True

    def mfa_desactivar(self, clave_id: int) -> None:
        with self.base.transaccion() as con:
            fila = con.execute("SELECT comercio_id, mfa_secreto FROM claves WHERE id=?", (clave_id,)).fetchone()
            if fila is None:
                raise ValueError("No existe una clave con ese número.")
            if not fila["mfa_secreto"]:
                raise ValueError("Esa clave no tiene segundo factor.")
            con.execute(
                "UPDATE claves SET mfa_secreto=NULL, mfa_confirmada=0, mfa_ultimo_paso=0, mfa_fallos=0, mfa_bloqueo_hasta=NULL WHERE id=?",
                (clave_id,),
            )
            auditar(con, fila["comercio_id"], clave_id, "mfa_desactivado", None, {})

    def mfa_estado(self, clave_id: int) -> str:
        """'activa', 'pendiente' (falta confirmarla) o 'sin_mfa'."""
        with self.base.lectura() as con:
            fila = con.execute("SELECT mfa_secreto, mfa_confirmada FROM claves WHERE id=?", (clave_id,)).fetchone()
        if fila is None or not fila["mfa_secreto"]:
            return "sin_mfa"
        return "activa" if fila["mfa_confirmada"] else "pendiente"

    def mfa_estados(self) -> Dict[int, str]:
        """El estado del segundo factor de cada clave de administrador activa (para la consola)."""
        with self.base.lectura() as con:
            return {
                r["id"]: ("sin_mfa" if not r["mfa_secreto"] else ("activa" if r["mfa_confirmada"] else "pendiente"))
                for r in con.execute(
                    "SELECT id, mfa_secreto, mfa_confirmada FROM claves WHERE rol='admin' AND activa=1"
                )
            }

    def mfa_verificar(self, clave_id: int, ingresado: object, epoch: float) -> Tuple[str, int]:
        """Comprueba un código para una acción. Devuelve (resultado, segundos de espera):
        'ok' · 'no_configurado' · 'bloqueada' · 'codigo_invalido'.

        TODO se decide DENTRO del bloqueo de escritura, con el estado releído ahí: así dos peticiones con el mismo código a la vez no pasan las dos
        (la primera avanza `mfa_ultimo_paso` y la segunda ya lo ve), y los fallos se cuentan igual con 2 procesos."""
        with self.base.transaccion() as con:
            fila = con.execute(
                "SELECT mfa_secreto, mfa_confirmada, mfa_ultimo_paso, mfa_fallos, mfa_bloqueo_hasta, comercio_id FROM claves WHERE id=?",
                (clave_id,),
            ).fetchone()
            if fila is None or not fila["mfa_secreto"] or not fila["mfa_confirmada"]:
                return "no_configurado", 0
            bloqueo = fila["mfa_bloqueo_hasta"]
            if bloqueo is not None and epoch < bloqueo:
                return "bloqueada", math.ceil(bloqueo - epoch)  # hacia arriba: nunca se promete menos de lo que falta
            fallos = fila[
                "mfa_fallos"
            ]  # al bloquear ya se pusieron en cero, así que un bloqueo vencido empieza de cero solo
            paso = totp.verificar(fila["mfa_secreto"], ingresado, epoch, ultimo_paso=fila["mfa_ultimo_paso"])
            if paso is not None:
                con.execute(
                    "UPDATE claves SET mfa_ultimo_paso=?, mfa_fallos=0, mfa_bloqueo_hasta=NULL WHERE id=? AND mfa_ultimo_paso < ?",
                    (paso, clave_id, paso),
                )
                return "ok", 0
            fallos += 1
            if fallos >= MFA_FALLOS_MAXIMOS:
                con.execute(
                    "UPDATE claves SET mfa_fallos=0, mfa_bloqueo_hasta=? WHERE id=?",
                    (epoch + MFA_BLOQUEO_SEGUNDOS, clave_id),
                )
                auditar(
                    con, fila["comercio_id"], clave_id, "mfa_bloqueada", None, {"minutos": MFA_BLOQUEO_SEGUNDOS // 60}
                )
                return "bloqueada", MFA_BLOQUEO_SEGUNDOS
            con.execute("UPDATE claves SET mfa_fallos=?, mfa_bloqueo_hasta=NULL WHERE id=?", (fallos, clave_id))
            auditar(con, fila["comercio_id"], clave_id, "mfa_fallido", None, {"seguidos": fallos})
            return "codigo_invalido", 0
