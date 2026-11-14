"""Lecturas del panel de control (listado, detalle, resumen, vínculos, comercios): todo el SQL de `consultas.py` vive aquí.

Solo lectura. Cada método recibe la conexión de quien llama y un objeto de filtros YA VALIDADO (ver `filtros_sql.py`), y devuelve filas crudas:
dar forma a la respuesta (JSON, textos, qué ve cada rol) es trabajo de `consultas.ConsultasPanel`, que no toca SQL.

SQL portable (cambio 7a): sin `json_each`, `julianday`, `rowid`, `SUM(condición)` ni alias en `HAVING`; los nulos se ordenan con `NULLS LAST` explícito
(SQLite y PostgreSQL los ordenan al revés por defecto) y las cubetas de puntaje se calculan con `CASE`, no con `CAST` (que trunca en uno y redondea en el otro).
"""

import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .filtros_sql import donde_de

ORDENES = {
    "fecha_desc": "e.fecha DESC, e.id",
    "fecha_asc": "e.fecha ASC, e.id",
    "monto_desc": "e.monto_cop DESC NULLS LAST, e.id",
    "riesgo_desc": "e.probabilidad DESC NULLS LAST, e.id",
}
COLUMNAS_DE_CODIGO = (
    "ip_codigo",
    "disp_codigo",
)  # las únicas columnas que `pagos_con_codigo` acepta (van dentro del SQL, así que no pueden venir de fuera)


def _y(donde: str) -> str:
    """Lo que hay que escribir antes de una condición extra: `AND` si ya hay WHERE, `WHERE` si no."""
    return donde + (" AND " if donde else "WHERE ")


class ConsultasRepositorio:
    ORDENES = ORDENES

    # ------------------------------------------------------------------ listado
    def listar(
        self, con: sqlite3.Connection, f: Any, orden: str, limite: int, desplazamiento: int
    ) -> Tuple[int, List[sqlite3.Row]]:
        donde, par = donde_de(f)
        total = con.execute(f"SELECT COUNT(*) FROM evaluaciones e {donde}", par).fetchone()[0]
        filas = con.execute(
            "SELECT e.*, c.nombre AS comercio_nombre, r.estado AS rev_estado, r.decision_analista AS rev_decision "
            f"FROM evaluaciones e JOIN comercios c ON c.id = e.comercio_id LEFT JOIN revisiones r ON r.id_evaluacion = e.id {donde} "
            f"ORDER BY {self.ORDENES[orden]} LIMIT ? OFFSET ?",
            par + (limite, desplazamiento),
        ).fetchall()
        return total, filas

    # ------------------------------------------------------------------ detalle de un pago
    def revision_de(self, con: sqlite3.Connection, id_evaluacion: str) -> Optional[sqlite3.Row]:
        return con.execute(
            "SELECT r.*, COALESCE(NULLIF(k.etiqueta, ''), 'clave #' || k.id) AS por FROM revisiones r LEFT JOIN claves k ON k.id = r.clave_analista "
            "WHERE r.id_evaluacion=?",
            (id_evaluacion,),
        ).fetchone()

    def resultado_de(self, con: sqlite3.Connection, id_evaluacion: str) -> Optional[sqlite3.Row]:
        return con.execute("SELECT * FROM resultados WHERE id_evaluacion=?", (id_evaluacion,)).fetchone()

    def nombre_comercio(self, con: sqlite3.Connection, comercio_id: int) -> str:
        return con.execute("SELECT nombre FROM comercios WHERE id=?", (comercio_id,)).fetchone()[0]

    def auditoria_de(self, con: sqlite3.Connection, id_evaluacion: str) -> List[sqlite3.Row]:
        return con.execute(
            "SELECT a.momento, a.accion, a.detalle, k.rol AS actor_rol, COALESCE(NULLIF(k.etiqueta, ''), 'clave #' || k.id) AS actor_nombre "
            "FROM auditoria a LEFT JOIN claves k ON k.id = a.clave_id WHERE a.id_evaluacion=? ORDER BY a.id",
            (id_evaluacion,),
        ).fetchall()

    def pagos_con_codigo(
        self, con: sqlite3.Connection, columna: str, valor: str, id_evaluacion: str, alcance_comercio: Optional[int]
    ) -> Tuple[int, List[sqlite3.Row]]:
        """Cuántos OTROS pagos comparten un código (de IP o de dispositivo) y los 5 más recientes. `alcance_comercio`: si es un comercio, solo cuentan los suyos."""
        if columna not in COLUMNAS_DE_CODIGO:
            raise ValueError(f"columna no permitida: {columna}")
        alcance, par = ("", ()) if alcance_comercio is None else (" AND comercio_id = ?", (alcance_comercio,))
        n = con.execute(
            f"SELECT COUNT(*) FROM evaluaciones WHERE {columna} = ? AND id != ?{alcance}", (valor, id_evaluacion) + par
        ).fetchone()[0]
        ultimos = con.execute(
            f"SELECT id_seguimiento, id_cliente, decision, fecha FROM evaluaciones WHERE {columna} = ? AND id != ?{alcance} "
            "ORDER BY fecha DESC LIMIT 5",
            (valor, id_evaluacion) + par,
        ).fetchall()
        return n, ultimos

    def otros_pagos_del_cliente(
        self, con: sqlite3.Connection, comercio_id: int, id_cliente: str, id_evaluacion: str
    ) -> int:
        return con.execute(
            "SELECT COUNT(*) FROM evaluaciones WHERE comercio_id = ? AND id_cliente = ? AND id != ?",
            (comercio_id, id_cliente, id_evaluacion),
        ).fetchone()[0]

    # ------------------------------------------------------------------ resumen
    def resumen(self, con: sqlite3.Connection, f: Any, con_analistas: bool) -> Dict[str, Any]:
        donde, par = donde_de(f)
        t = con.execute(
            "SELECT COUNT(*) pagos, SUM(CASE WHEN decision = 'APROBADA' THEN 1 ELSE 0 END) aprobadas, SUM(CASE WHEN decision = 'EN_REVISION' THEN 1 ELSE 0 END) en_revision, SUM(CASE WHEN decision = 'RECHAZADA' THEN 1 ELSE 0 END) rechazadas, "
            "COALESCE(SUM(monto_cop),0) monto, COALESCE(SUM(CASE WHEN decision='RECHAZADA' THEN monto_cop END),0) monto_rechazado, "
            "COALESCE(SUM(CASE WHEN decision='EN_REVISION' THEN monto_cop END),0) monto_en_revision, "
            "SUM(CASE WHEN modo = 'degradado' THEN 1 ELSE 0 END) degradados, SUM(CASE WHEN estado_origen = 'no_enviado' THEN 1 ELSE 0 END) sin_estado, AVG(latencia_ms) latencia_media "
            f"FROM evaluaciones e {donde}",
            par,
        ).fetchone()
        n = t["pagos"] or 0
        p95 = None
        if n:
            p95 = con.execute(
                f"SELECT latencia_ms FROM evaluaciones e {donde} ORDER BY latencia_ms LIMIT 1 OFFSET ?",
                par + (int(0.95 * (n - 1)),),
            ).fetchone()[0]
        por_dia = con.execute(
            "SELECT substr(fecha,1,10) dia, SUM(CASE WHEN decision = 'APROBADA' THEN 1 ELSE 0 END) aprobadas, SUM(CASE WHEN decision = 'EN_REVISION' THEN 1 ELSE 0 END) en_revision, SUM(CASE WHEN decision = 'RECHAZADA' THEN 1 ELSE 0 END) rechazadas "
            f"FROM evaluaciones e {donde} GROUP BY dia ORDER BY dia",
            par,
        ).fetchall()
        por_comercio = con.execute(
            "SELECT e.comercio_id, c.nombre, COUNT(*) pagos, SUM(CASE WHEN decision = 'APROBADA' THEN 1 ELSE 0 END) aprobadas, SUM(CASE WHEN decision = 'EN_REVISION' THEN 1 ELSE 0 END) en_revision, "
            "SUM(CASE WHEN decision = 'RECHAZADA' THEN 1 ELSE 0 END) rechazadas, COALESCE(SUM(CASE WHEN decision='RECHAZADA' THEN monto_cop END),0) monto_rechazado "
            f"FROM evaluaciones e JOIN comercios c ON c.id = e.comercio_id {donde} GROUP BY e.comercio_id, c.nombre ORDER BY pagos DESC LIMIT 100",
            par,
        ).fetchall()
        reglas = con.execute(
            "SELECT er.regla regla, COUNT(*) veces, SUM(CASE WHEN e.decision = 'RECHAZADA' THEN 1 ELSE 0 END) rechazadas "
            f"FROM evaluaciones e JOIN evaluacion_reglas er ON er.id_evaluacion = e.id {donde} GROUP BY er.regla ORDER BY veces DESC LIMIT 20",
            par,
        ).fetchall()
        hist = con.execute(
            "SELECT CASE WHEN probabilidad * 10 >= 9 THEN 9 WHEN probabilidad * 10 >= 8 THEN 8 WHEN probabilidad * 10 >= 7 THEN 7 WHEN probabilidad * 10 >= 6 THEN 6 WHEN probabilidad * 10 >= 5 THEN 5 WHEN probabilidad * 10 >= 4 THEN 4 WHEN probabilidad * 10 >= 3 THEN 3 WHEN probabilidad * 10 >= 2 THEN 2 WHEN probabilidad * 10 >= 1 THEN 1 ELSE 0 END cubeta, COUNT(*) pagos "
            f"FROM evaluaciones e {_y(donde)} probabilidad IS NOT NULL GROUP BY CASE WHEN probabilidad * 10 >= 9 THEN 9 WHEN probabilidad * 10 >= 8 THEN 8 WHEN probabilidad * 10 >= 7 THEN 7 WHEN probabilidad * 10 >= 6 THEN 6 WHEN probabilidad * 10 >= 5 THEN 5 WHEN probabilidad * 10 >= 4 THEN 4 WHEN probabilidad * 10 >= 3 THEN 3 WHEN probabilidad * 10 >= 2 THEN 2 WHEN probabilidad * 10 >= 1 THEN 1 ELSE 0 END",
            par,
        ).fetchall()
        por_analista: List[sqlite3.Row] = []
        if con_analistas:
            por_analista = con.execute(
                "SELECT COALESCE(NULLIF(k.etiqueta, ''), 'clave #' || k.id) AS analista, COUNT(*) AS resueltas, SUM(CASE WHEN r.decision_analista = 'APROBADA' THEN 1 ELSE 0 END) AS aprobadas, "
                "SUM(CASE WHEN r.decision_analista = 'RECHAZADA' THEN 1 ELSE 0 END) AS rechazadas, AVG(r.minutos_resolucion) AS minutos_promedio "
                f"FROM revisiones r JOIN evaluaciones e ON e.id = r.id_evaluacion LEFT JOIN claves k ON k.id = r.clave_analista {_y(donde)} r.estado = 'resuelta' "
                "GROUP BY r.clave_analista, k.etiqueta, k.id ORDER BY resueltas DESC LIMIT 50",
                par,
            ).fetchall()
        alcance, par2 = ("WHERE e.comercio_id = ?", (f.comercio_id,)) if f.comercio_id is not None else ("", ())
        pendientes = con.execute(
            f"SELECT COUNT(*) FROM revisiones r JOIN evaluaciones e ON e.id = r.id_evaluacion {_y(alcance)} r.estado = 'pendiente'",
            par2,
        ).fetchone()[0]
        return {
            "totales": t,
            "p95": p95,
            "por_dia": por_dia,
            "por_comercio": por_comercio,
            "reglas": reglas,
            "histograma": hist,
            "por_analista": por_analista,
            "pendientes": pendientes,
        }

    # ------------------------------------------------------------------ vínculos: patrones entre pagos
    def vinculos(self, con: sqlite3.Connection, f: Any, minimo: int) -> Dict[str, List[sqlite3.Row]]:
        donde, par = donde_de(f)
        base = _y(donde)
        ips = con.execute(
            "SELECT e.ip_codigo codigo, COUNT(*) pagos, COUNT(DISTINCT e.comercio_id || '|' || e.id_cliente) clientes, COUNT(DISTINCT e.comercio_id) comercios, "
            "SUM(CASE WHEN decision = 'RECHAZADA' THEN 1 ELSE 0 END) rechazadas, SUM(CASE WHEN decision = 'EN_REVISION' THEN 1 ELSE 0 END) en_revision, MIN(fecha) primera, MAX(fecha) ultima "
            f"FROM evaluaciones e {base} e.ip_codigo IS NOT NULL GROUP BY e.ip_codigo HAVING COUNT(DISTINCT e.comercio_id || '|' || e.id_cliente) >= ? ORDER BY clientes DESC, pagos DESC LIMIT 50",
            par + (minimo,),
        ).fetchall()
        disp = con.execute(
            "SELECT e.disp_codigo codigo, COUNT(*) pagos, COUNT(DISTINCT e.comercio_id || '|' || e.id_cliente) clientes, COUNT(DISTINCT e.comercio_id) comercios, "
            "SUM(CASE WHEN decision = 'RECHAZADA' THEN 1 ELSE 0 END) rechazadas, SUM(CASE WHEN decision = 'EN_REVISION' THEN 1 ELSE 0 END) en_revision, MIN(fecha) primera, MAX(fecha) ultima "
            f"FROM evaluaciones e {base} e.disp_codigo IS NOT NULL GROUP BY e.disp_codigo HAVING COUNT(DISTINCT e.comercio_id || '|' || e.id_cliente) >= ? ORDER BY clientes DESC, pagos DESC LIMIT 50",
            par + (minimo,),
        ).fetchall()
        clientes = con.execute(
            "SELECT e.comercio_id, c.nombre comercio, e.id_cliente, COUNT(*) pagos, COUNT(DISTINCT e.ip_codigo) ips, "
            "SUM(CASE WHEN decision = 'RECHAZADA' THEN 1 ELSE 0 END) rechazadas, MIN(fecha) primera, MAX(fecha) ultima "
            f"FROM evaluaciones e JOIN comercios c ON c.id = e.comercio_id {base} e.ip_codigo IS NOT NULL "
            "GROUP BY e.comercio_id, e.id_cliente HAVING COUNT(DISTINCT e.ip_codigo) >= ? ORDER BY ips DESC, pagos DESC LIMIT 50",
            par + (minimo,),
        ).fetchall()
        candidatas = con.execute(
            f"SELECT e.ip_codigo, e.fecha FROM evaluaciones e {base} e.ip_codigo IN ("
            f"SELECT ip_codigo FROM evaluaciones e {_y(donde)} e.ip_codigo IS NOT NULL GROUP BY ip_codigo HAVING COUNT(*) >= ? LIMIT 200) "
            "ORDER BY e.ip_codigo, e.fecha",
            par + par + (minimo,),
        ).fetchall()
        return {"ips": ips, "dispositivos": disp, "clientes": clientes, "candidatas": candidatas}

    # ------------------------------------------------------------------ deriva del modelo
    def distribucion_puntaje(
        self, con: sqlite3.Connection, nombre: str, version: str, conjunto: str, desde_iso: str
    ) -> List[int]:
        """Cuántos pagos evaluados por ESE modelo (desde `desde_iso`, creación de la evaluación) caen en cada una de las 10 cubetas de puntaje.
        Se cuentan solo los que tienen puntaje (los del modo degradado no) y se excluyen los de la variante sin estado del dispositivo. Mismas cubetas que `deriva.cubeta_de`."""
        cuentas = [0] * 10
        for r in con.execute(
            "SELECT CASE WHEN probabilidad * 10 >= 9 THEN 9 WHEN probabilidad * 10 >= 8 THEN 8 WHEN probabilidad * 10 >= 7 THEN 7 WHEN probabilidad * 10 >= 6 THEN 6 WHEN probabilidad * 10 >= 5 THEN 5 WHEN probabilidad * 10 >= 4 THEN 4 WHEN probabilidad * 10 >= 3 THEN 3 WHEN probabilidad * 10 >= 2 THEN 2 WHEN probabilidad * 10 >= 1 THEN 1 ELSE 0 END cubeta, COUNT(*) pagos FROM evaluaciones "
            "WHERE modelo_nombre = ? AND modelo_version = ? AND modelo_conjunto = ? AND estado_origen != 'no_enviado' AND probabilidad IS NOT NULL AND creada >= ? GROUP BY CASE WHEN probabilidad * 10 >= 9 THEN 9 WHEN probabilidad * 10 >= 8 THEN 8 WHEN probabilidad * 10 >= 7 THEN 7 WHEN probabilidad * 10 >= 6 THEN 6 WHEN probabilidad * 10 >= 5 THEN 5 WHEN probabilidad * 10 >= 4 THEN 4 WHEN probabilidad * 10 >= 3 THEN 3 WHEN probabilidad * 10 >= 2 THEN 2 WHEN probabilidad * 10 >= 1 THEN 1 ELSE 0 END",
            (nombre, version, conjunto, desde_iso),
        ):
            cuentas[max(0, min(int(r["cubeta"]), 9))] += int(r["pagos"])
        return cuentas

    # ------------------------------------------------------------------ comercios (para el filtro)
    def comercios_con_pagos(self, con: sqlite3.Connection) -> List[sqlite3.Row]:
        return con.execute(
            "SELECT c.id, c.nombre, (c.activo = 0) AS suspendido, COUNT(e.id) pagos FROM comercios c LEFT JOIN evaluaciones e ON e.comercio_id = c.id "
            "WHERE c.id != 1 GROUP BY c.id ORDER BY pagos DESC, c.nombre"
        ).fetchall()
