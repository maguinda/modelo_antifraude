"""Revisiones manuales: la cola de pagos que espera a un analista y su resolución. Cada método recibe la conexión de quien llama (ver evaluaciones.py)."""

import sqlite3
from typing import Any, List, Optional, Tuple

from .filtros_sql import condiciones_de


class RevisionesRepositorio:
    ORDEN = {
        "antiguos": "r.creada, e.id",
        "recientes": "r.creada DESC, e.id",
        "riesgo_desc": "e.probabilidad DESC, r.creada",
        "monto_desc": "e.monto_cop DESC, r.creada",
    }

    def por_evaluacion(self, con: sqlite3.Connection, id_evaluacion: str) -> Optional[sqlite3.Row]:
        return con.execute("SELECT * FROM revisiones WHERE id_evaluacion=?", (id_evaluacion,)).fetchone()

    def existe(self, con: sqlite3.Connection, id_evaluacion: str) -> bool:
        return con.execute("SELECT 1 FROM revisiones WHERE id_evaluacion=?", (id_evaluacion,)).fetchone() is not None

    def crear_pendiente(self, con: sqlite3.Connection, id_evaluacion: str, comercio_id: int, creada: str) -> None:
        con.execute(
            "INSERT INTO revisiones (id_evaluacion, comercio_id, estado, creada) VALUES (?,?,'pendiente',?)",
            (id_evaluacion, comercio_id, creada),
        )

    def resolver(
        self,
        con: sqlite3.Connection,
        id_evaluacion: str,
        decision: str,
        nota: Optional[str],
        clave_analista: int,
        resuelta: str,
    ) -> int:
        """Resuelve una revisión que siga pendiente. Devuelve cuántas filas cambió: 0 si ya la había resuelto alguien (así se detecta la doble resolución)."""
        cur = con.execute(
            "UPDATE revisiones SET estado='resuelta', decision_analista=?, nota=?, clave_analista=?, resuelta=? "
            "WHERE id_evaluacion=? AND estado='pendiente'",
            (decision, nota, clave_analista, resuelta, id_evaluacion),
        )
        return cur.rowcount

    def listar(
        self, con: sqlite3.Connection, estado: str, filtros: Optional[Any], orden: str, limite: int, desplazamiento: int
    ) -> Tuple[sqlite3.Row, List[sqlite3.Row]]:
        """La cola de revisión. `filtros` es el objeto ya validado de consultas.Filtros (o None); el orden sale de una lista fija (`ORDEN`)."""
        condiciones, parametros = condiciones_de(filtros) if filtros is not None else ([], ())
        donde = " AND ".join(["r.estado = ?"] + condiciones)
        tot = con.execute(
            f"SELECT COUNT(*), COALESCE(SUM(e.monto_cop), 0) FROM revisiones r JOIN evaluaciones e ON e.id = r.id_evaluacion WHERE {donde}",
            (estado,) + tuple(parametros),
        ).fetchone()
        filas = con.execute(
            "SELECT e.*, c.nombre AS comercio_nombre, r.estado AS rev_estado, r.creada AS rev_creada, r.resuelta AS rev_resuelta, "
            "COALESCE(NULLIF(k.etiqueta, ''), 'clave #' || k.id) AS rev_por FROM revisiones r JOIN evaluaciones e ON e.id=r.id_evaluacion "
            f"JOIN comercios c ON c.id = e.comercio_id LEFT JOIN claves k ON k.id = r.clave_analista WHERE {donde} ORDER BY {self.ORDEN[orden]} LIMIT ? OFFSET ?",
            (estado,) + tuple(parametros) + (limite, desplazamiento),
        ).fetchall()
        return tot, filas
