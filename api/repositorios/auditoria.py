"""La auditoría: un registro de solo agregar (los triggers de la base impiden editarlo o borrarlo)."""
import json
import sqlite3
from typing import Any, Dict, Optional

from .comunes import ahora_iso


def auditar(con: sqlite3.Connection, comercio_id: Optional[int], clave_id: Optional[int], accion: str,
            id_evaluacion: Optional[str], detalle: Dict[str, Any]) -> None:
    con.execute("INSERT INTO auditoria (momento, comercio_id, clave_id, accion, id_evaluacion, detalle) VALUES (?,?,?,?,?,?)",
                (ahora_iso(), comercio_id, clave_id, accion, id_evaluacion, json.dumps(detalle, ensure_ascii=False, default=str)))
