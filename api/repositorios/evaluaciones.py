"""Evaluaciones: buscarlas, guardarlas y leer el historial de un cliente o de un dispositivo.

Cada método recibe la CONEXIÓN de quien llama: las transacciones (y con ellas el orden de los bloqueos) las controla el servicio, no el repositorio.
Aquí solo hay persistencia: las decisiones de negocio (quién puede ver qué, si algo se repite, qué se recalcula) siguen en el servicio.
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple


class EvaluacionesRepositorio:
    def por_transaccion(self, con: sqlite3.Connection, comercio_id: int, id_transaccion: str) -> Optional[sqlite3.Row]:
        """La evaluación que este comercio ya hizo con ese `id_transaccion` (None si no hay)."""
        return con.execute(
            "SELECT * FROM evaluaciones WHERE comercio_id=? AND id_transaccion=?", (comercio_id, id_transaccion)
        ).fetchone()

    def por_id(self, con: sqlite3.Connection, id_evaluacion: str) -> Optional[sqlite3.Row]:
        return con.execute("SELECT * FROM evaluaciones WHERE id=?", (id_evaluacion,)).fetchone()

    def por_identificador(self, con: sqlite3.Connection, identificador: str) -> Optional[sqlite3.Row]:
        """Busca por el identificador de seguimiento (AF-XXXX-XXXX) o por el interno. NO decide quién puede verla: eso es del servicio."""
        columna = "id_seguimiento" if identificador.upper().startswith("AF-") else "id"
        return con.execute(
            f"SELECT * FROM evaluaciones WHERE {columna}=?",
            (identificador.upper() if columna == "id_seguimiento" else identificador,),
        ).fetchone()

    def seguimiento_existe(self, con: sqlite3.Connection, candidato: str) -> bool:
        return con.execute("SELECT 1 FROM evaluaciones WHERE id_seguimiento=?", (candidato,)).fetchone() is not None

    def marcas(
        self, con: sqlite3.Connection, comercio_id: int, id_cliente: str, huella: Optional[str]
    ) -> Tuple[int, int]:
        """Último pago guardado del cliente y de la huella: si cambian entre calcular y guardar, el historial cambió."""
        cliente = con.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM evaluaciones WHERE comercio_id=? AND id_cliente=?",
            (comercio_id, id_cliente),
        ).fetchone()[0]
        ultimo = (
            con.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM evaluaciones WHERE comercio_id=? AND huella=?",
                (comercio_id, huella),
            ).fetchone()[0]
            if huella
            else 0
        )
        return cliente, ultimo

    def previos_cliente(
        self, con: sqlite3.Connection, comercio_id: int, id_cliente: str, fecha: str, limite: int
    ) -> List[Dict]:
        """Las compras anteriores de un cliente, de la más vieja a la más nueva."""
        return self._previos(
            con,
            "SELECT fila_json FROM evaluaciones WHERE comercio_id=? AND id_cliente=? AND fecha<=? "
            "ORDER BY fecha DESC, seq DESC LIMIT ?",
            (comercio_id, id_cliente, fecha, limite),
        )

    def previos_huella(
        self, con: sqlite3.Connection, comercio_id: int, huella: str, id_cliente: str, fecha: str, limite: int
    ) -> List[Dict]:
        """Las compras anteriores de OTROS clientes desde el mismo dispositivo, de la más vieja a la más nueva."""
        return self._previos(
            con,
            "SELECT fila_json FROM evaluaciones WHERE comercio_id=? AND huella=? AND id_cliente<>? AND fecha<=? "
            "ORDER BY fecha DESC, seq DESC LIMIT ?",
            (comercio_id, huella, id_cliente, fecha, limite),
        )

    @staticmethod
    def _previos(con: sqlite3.Connection, consulta: str, parametros: tuple) -> List[Dict]:
        filas = [json.loads(r["fila_json"]) for r in con.execute(consulta, parametros)]
        return list(reversed(filas))  # de la más vieja a la más nueva

    def insertar(
        self,
        con: sqlite3.Connection,
        *,
        id: str,
        comercio_id: int,
        id_transaccion: str,
        id_cliente: str,
        fecha: str,
        huella: Optional[str],
        fila_json: str,
        solicitud_hash: str,
        decision: str,
        decision_modelo: Optional[str],
        probabilidad: Optional[float],
        motivos_json: str,
        reglas_json: str,
        modelo_nombre: str,
        modelo_version: str,
        modelo_conjunto: str,
        modo: str,
        latencia_ms: float,
        creada: str,
        red_origen: str,
        estado_origen: str,
        id_seguimiento: str,
        ip_codigo: Optional[str],
        disp_codigo: Optional[str],
        monto_cop: Any,
        no_enviados_json: str,
        senales_json: Optional[str],
    ) -> None:
        con.execute(
            "INSERT INTO evaluaciones (id, comercio_id, id_transaccion, id_cliente, fecha, huella, fila_json, solicitud_hash, "
            "decision, decision_modelo, probabilidad, motivos_json, reglas_json, modelo_nombre, modelo_version, "
            "modelo_conjunto, modo, latencia_ms, creada, red_origen, estado_origen, id_seguimiento, ip_codigo, disp_codigo, monto_cop, no_enviados_json, senales_json, seq) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM evaluaciones))",
            (
                id,
                comercio_id,
                id_transaccion,
                id_cliente,
                fecha,
                huella,
                fila_json,
                solicitud_hash,
                decision,
                decision_modelo,
                probabilidad,
                motivos_json,
                reglas_json,
                modelo_nombre,
                modelo_version,
                modelo_conjunto,
                modo,
                latencia_ms,
                creada,
                red_origen,
                estado_origen,
                id_seguimiento,
                ip_codigo,
                disp_codigo,
                monto_cop,
                no_enviados_json,
                senales_json,
            ),
        )
        for posicion, regla in enumerate(
            json.loads(reglas_json or "[]")
        ):  # una fila por regla activada (consultas y filtros por regla, sin leer el JSON)
            con.execute(
                "INSERT INTO evaluacion_reglas (id_evaluacion, posicion, regla) VALUES (?,?,?)",
                (id, posicion, str(regla.get("regla", "?")) if isinstance(regla, dict) else "?"),
            )
