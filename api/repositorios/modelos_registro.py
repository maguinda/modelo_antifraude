"""El registro de modelos: qué archivos de modelo se conocen, su huella SHA-256 y cuál está EN USO para cada rol.

Dos roles, porque el servicio usa dos modelos: `principal` (con el estado del dispositivo) y `sin_estado` (cuando el comercio no lo manda).
Estados: `candidato` (registrado, todavía no se usa) → `en_uso` (como mucho UNO por rol: lo garantiza un índice único parcial) → `archivado` (lo que estuvo en uso).
Promover y revertir son transacciones que cambian los dos modelos a la vez y dejan constancia en la auditoría. Aquí NO se leen ni se cargan modelos: solo se
guardan sus datos (archivo, huella, versión, referencia de puntajes); verificar la huella y cargar es de `registro_modelos.py`.
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional

from .auditoria import auditar
from .base import Base
from .comunes import ahora_iso

ROLES = ("principal", "sin_estado")
COLUMNAS = (
    "id, archivo, sha256, nombre, version, conjunto, rol, estado, registrado, promovido, archivado, referencia_json"
)


def _fila(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["referencia"] = json.loads(d.pop("referencia_json")) if d.get("referencia_json") else None
    return d


class ModelosRegistroRepositorio:
    def __init__(self, base: Base):
        self.base = base

    def registrar(
        self,
        archivo: str,
        sha256: str,
        nombre: str,
        version: str,
        conjunto: str,
        rol: str,
        referencia: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Anota un modelo como CANDIDATO. Un mismo archivo (huella) no se registra dos veces para el mismo rol."""
        if rol not in ROLES:
            raise ValueError(f"El rol debe ser uno de: {', '.join(ROLES)}.")
        with self.base.transaccion() as con:
            ya = con.execute("SELECT id FROM modelos_registro WHERE sha256=? AND rol=?", (sha256, rol)).fetchone()
            if ya is not None:
                raise ValueError(f"Ese modelo ya está registrado para el rol «{rol}» (número {ya['id']}).")
            cur = con.execute(
                "INSERT INTO modelos_registro (archivo, sha256, nombre, version, conjunto, rol, estado, referencia_json, registrado) VALUES (?,?,?,?,?,?, 'candidato', ?, ?) RETURNING id",
                (
                    archivo,
                    sha256,
                    nombre,
                    version,
                    conjunto,
                    rol,
                    json.dumps(referencia) if referencia else None,
                    ahora_iso(),
                ),
            )
            id_ = int(cur.fetchone()[0])
            auditar(
                con,
                None,
                None,
                "modelo_registrado",
                None,
                {"id": id_, "rol": rol, "version": version, "huella": sha256[:12]},
            )
            return id_

    def listar(self) -> List[Dict[str, Any]]:
        with self.base.lectura() as con:
            return [_fila(r) for r in con.execute(f"SELECT {COLUMNAS} FROM modelos_registro ORDER BY rol, id DESC")]

    def por_id(self, id_: int) -> Optional[Dict[str, Any]]:
        with self.base.lectura() as con:
            r = con.execute(f"SELECT {COLUMNAS} FROM modelos_registro WHERE id=?", (id_,)).fetchone()
        return _fila(r) if r else None

    def en_uso(self, rol: str) -> Optional[Dict[str, Any]]:
        with self.base.lectura() as con:
            r = con.execute(
                f"SELECT {COLUMNAS} FROM modelos_registro WHERE rol=? AND estado='en_uso'", (rol,)
            ).fetchone()
        return _fila(r) if r else None

    def promover(self, id_: int) -> Dict[str, Any]:
        """Pone ese modelo EN USO y archiva el que estaba en uso para su rol, en una sola transacción (nunca quedan dos, ni ninguno a medias)."""
        with self.base.transaccion() as con:
            r = con.execute(
                f"SELECT {COLUMNAS} FROM modelos_registro WHERE id=?", (id_,)
            ).fetchone()  # releído dentro del bloqueo
            if r is None:
                raise ValueError("No existe un modelo registrado con ese número.")
            if r["estado"] == "en_uso":
                raise ValueError("Ese modelo ya está en uso.")
            ahora = ahora_iso()
            anterior = con.execute(
                "SELECT id FROM modelos_registro WHERE rol=? AND estado='en_uso'", (r["rol"],)
            ).fetchone()
            con.execute(
                "UPDATE modelos_registro SET estado='archivado', archivado=? WHERE rol=? AND estado='en_uso'",
                (ahora, r["rol"]),
            )
            con.execute(
                "UPDATE modelos_registro SET estado='en_uso', promovido=?, archivado=NULL WHERE id=?", (ahora, id_)
            )
            auditar(
                con,
                None,
                None,
                "modelo_promovido",
                None,
                {
                    "id": id_,
                    "rol": r["rol"],
                    "version": r["version"],
                    "huella": r["sha256"][:12],
                    "reemplaza": anterior["id"] if anterior else None,
                },
            )
            return _fila(con.execute(f"SELECT {COLUMNAS} FROM modelos_registro WHERE id=?", (id_,)).fetchone())

    def revertir(self, rol: str) -> Dict[str, Any]:
        """Vuelve al modelo que estuvo en uso justo antes (el archivado más recientemente promovido). El actual queda archivado."""
        if rol not in ROLES:
            raise ValueError(f"El rol debe ser uno de: {', '.join(ROLES)}.")
        with self.base.transaccion() as con:
            actual = con.execute("SELECT id FROM modelos_registro WHERE rol=? AND estado='en_uso'", (rol,)).fetchone()
            previo = con.execute(
                "SELECT id, version, sha256 FROM modelos_registro WHERE rol=? AND estado='archivado' AND promovido IS NOT NULL ORDER BY promovido DESC, id DESC LIMIT 1",
                (rol,),
            ).fetchone()
            if previo is None:
                raise ValueError(f"No hay un modelo anterior al que volver para el rol «{rol}».")
            ahora = ahora_iso()
            con.execute(
                "UPDATE modelos_registro SET estado='archivado', archivado=? WHERE rol=? AND estado='en_uso'",
                (ahora, rol),
            )
            con.execute(
                "UPDATE modelos_registro SET estado='en_uso', promovido=?, archivado=NULL WHERE id=?",
                (ahora, previo["id"]),
            )
            auditar(
                con,
                None,
                None,
                "modelo_revertido",
                None,
                {
                    "rol": rol,
                    "vuelve_a": previo["id"],
                    "version": previo["version"],
                    "huella": previo["sha256"][:12],
                    "deja": actual["id"] if actual else None,
                },
            )
            return _fila(con.execute(f"SELECT {COLUMNAS} FROM modelos_registro WHERE id=?", (previo["id"],)).fetchone())

    def guardar_referencia(self, id_: int, referencia: Dict[str, Any]) -> None:
        with self.base.transaccion() as con:
            if con.execute("SELECT 1 FROM modelos_registro WHERE id=?", (id_,)).fetchone() is None:
                raise ValueError("No existe un modelo registrado con ese número.")
            con.execute("UPDATE modelos_registro SET referencia_json=? WHERE id=?", (json.dumps(referencia), id_))
            auditar(con, None, None, "modelo_referencia_guardada", None, {"id": id_, "pagos": referencia.get("n")})
