"""Resultados reales de los pagos (fraude confirmado, contracargo o legítimo): lo que el comercio informa después, para que el modelo mejore. Recibe la conexión de quien llama."""
import sqlite3
from typing import Optional


class ResultadosRepositorio:
    def por_evaluacion(self, con: sqlite3.Connection, id_evaluacion: str) -> Optional[sqlite3.Row]:
        return con.execute("SELECT resultado FROM resultados WHERE id_evaluacion=?", (id_evaluacion,)).fetchone()

    def registrar(self, con: sqlite3.Connection, id_evaluacion: str, comercio_id: int, resultado: str, fecha_confirmacion: str, nota: Optional[str], recibido: str) -> None:
        con.execute("INSERT INTO resultados (id_evaluacion, comercio_id, resultado, fecha_confirmacion, nota, recibido) VALUES (?,?,?,?,?,?)",
                    (id_evaluacion, comercio_id, resultado, fecha_confirmacion, nota, recibido))
