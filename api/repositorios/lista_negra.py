"""La lista negra de cada comercio: clientes que se rechazan siempre. Cada método recibe la conexión de quien llama (ver evaluaciones.py)."""
import sqlite3
from typing import List, Optional


class ListaNegraRepositorio:
    def ids_de_cliente(self, con: sqlite3.Connection, comercio_id: int, id_cliente: str) -> List[str]:
        """[id_cliente] si ese cliente está en la lista negra del comercio; [] si no."""
        return [r["id_cliente"] for r in con.execute("SELECT id_cliente FROM lista_negra WHERE comercio_id=? AND id_cliente=?", (comercio_id, id_cliente))]

    def agregar(self, con: sqlite3.Connection, comercio_id: int, id_cliente: str, motivo: Optional[str], creada: str) -> None:
        con.execute("INSERT OR REPLACE INTO lista_negra (comercio_id, id_cliente, motivo, creada) VALUES (?,?,?,?)", (comercio_id, id_cliente, motivo, creada))

    def quitar(self, con: sqlite3.Connection, comercio_id: int, id_cliente: str) -> int:
        """Devuelve cuántas filas borró: 0 si ese cliente no estaba."""
        return con.execute("DELETE FROM lista_negra WHERE comercio_id=? AND id_cliente=?", (comercio_id, id_cliente)).rowcount

    def listar(self, con: sqlite3.Connection, comercio_id: int) -> List[sqlite3.Row]:
        return list(con.execute("SELECT * FROM lista_negra WHERE comercio_id=? ORDER BY creada", (comercio_id,)))
