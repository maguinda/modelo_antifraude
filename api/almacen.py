"""Almacén SQLite: claves, comercios, evaluaciones, revisiones, resultados, lista negra y auditoría.

SQLite sirve para desarrollo y para empezar. Para producción se cambia por PostgreSQL manteniendo esta misma interfaz.
"""
import hashlib
import hmac
import ipaddress
import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

PLATAFORMA = 1          # id del «comercio» al que pertenecen las claves de analistas y administradores

DDL = """
CREATE TABLE IF NOT EXISTS comercios (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, creado TEXT NOT NULL, activo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS claves (
    id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT NOT NULL UNIQUE, comercio_id INTEGER NOT NULL REFERENCES comercios(id),
    rol TEXT NOT NULL CHECK (rol IN ('comercio','analista','admin')), etiqueta TEXT, activa INTEGER NOT NULL DEFAULT 1, creada TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evaluaciones (
    id TEXT PRIMARY KEY, comercio_id INTEGER NOT NULL REFERENCES comercios(id), id_transaccion TEXT NOT NULL,
    id_cliente TEXT NOT NULL, fecha TEXT NOT NULL, huella TEXT, fila_json TEXT NOT NULL, solicitud_hash TEXT NOT NULL,
    decision TEXT NOT NULL, decision_modelo TEXT, probabilidad REAL, motivos_json TEXT NOT NULL, reglas_json TEXT NOT NULL,
    modelo_nombre TEXT NOT NULL, modelo_version TEXT NOT NULL, modelo_conjunto TEXT NOT NULL, modo TEXT NOT NULL,
    latencia_ms REAL NOT NULL, creada TEXT NOT NULL, red_origen TEXT NOT NULL DEFAULT 'comercio', estado_origen TEXT NOT NULL DEFAULT 'comercio',
    id_seguimiento TEXT, ip_codigo TEXT, disp_codigo TEXT, monto_cop REAL, no_enviados_json TEXT,
    UNIQUE (comercio_id, id_transaccion));
CREATE INDEX IF NOT EXISTS ix_eval_cliente ON evaluaciones (comercio_id, id_cliente, fecha);
CREATE INDEX IF NOT EXISTS ix_eval_huella ON evaluaciones (comercio_id, huella, fecha);
CREATE TABLE IF NOT EXISTS revisiones (
    id_evaluacion TEXT PRIMARY KEY REFERENCES evaluaciones(id), comercio_id INTEGER NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('pendiente','resuelta')), decision_analista TEXT, nota TEXT,
    clave_analista INTEGER, creada TEXT NOT NULL, resuelta TEXT);
CREATE INDEX IF NOT EXISTS ix_rev_estado ON revisiones (estado, creada);
CREATE TABLE IF NOT EXISTS resultados (
    id_evaluacion TEXT PRIMARY KEY REFERENCES evaluaciones(id), comercio_id INTEGER NOT NULL, resultado TEXT NOT NULL,
    fecha_confirmacion TEXT NOT NULL, nota TEXT, recibido TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lista_negra (
    comercio_id INTEGER NOT NULL, id_cliente TEXT NOT NULL, motivo TEXT, creada TEXT NOT NULL,
    PRIMARY KEY (comercio_id, id_cliente));
CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT, momento TEXT NOT NULL, comercio_id INTEGER, clave_id INTEGER, accion TEXT NOT NULL,
    id_evaluacion TEXT, detalle TEXT);
CREATE TRIGGER IF NOT EXISTS auditoria_no_se_edita BEFORE UPDATE ON auditoria BEGIN SELECT RAISE(ABORT, 'la auditoria no se edita'); END;
CREATE TRIGGER IF NOT EXISTS auditoria_no_se_borra BEFORE DELETE ON auditoria BEGIN SELECT RAISE(ABORT, 'la auditoria no se borra'); END;
"""


INDICES_PANEL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_eval_seguimiento ON evaluaciones (id_seguimiento);
CREATE INDEX IF NOT EXISTS ix_eval_fecha ON evaluaciones (fecha);
CREATE INDEX IF NOT EXISTS ix_eval_comercio_fecha ON evaluaciones (comercio_id, fecha);
CREATE INDEX IF NOT EXISTS ix_eval_decision ON evaluaciones (decision, fecha);
CREATE INDEX IF NOT EXISTS ix_eval_ip ON evaluaciones (ip_codigo, fecha);
CREATE INDEX IF NOT EXISTS ix_eval_disp ON evaluaciones (disp_codigo, fecha);
"""


def ahora_iso() -> str:
    return datetime.now().isoformat(timespec="microseconds")


ALFABETO_SEGUIMIENTO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"        # sin I, L, O, U: no se confunden al leerlos por teléfono


def id_seguimiento_libre(ya_existe: Callable[[str], bool]) -> str:
    """Un identificador de seguimiento que `ya_existe` no reconoce. Con tope de intentos: un generador roto da un error claro y no un ciclo infinito."""
    for _ in range(20):
        candidato = nuevo_id_seguimiento()
        if not ya_existe(candidato):
            return candidato
    raise RuntimeError("No se pudo generar un identificador de seguimiento único tras 20 intentos.")


def nuevo_id_seguimiento() -> str:
    """Identificador propio de cada pago, legible: AF-7K3Q-9M2X (40 bits al azar; quien lo guarda verifica que no exista)."""
    c = "".join(secrets.choice(ALFABETO_SEGUIMIENTO) for _ in range(8))
    return f"AF-{c[:4]}-{c[4:]}"


@dataclass(frozen=True)
class Principal:
    clave_id: int
    comercio_id: int
    rol: str
    nombre: str


class Almacen:
    def __init__(self, ruta: Path, pepper: str, durabilidad: str = "NORMAL"):
        """durabilidad: NORMAL (rápida; si se va la luz puede perderse lo último) o FULL (cada guardado se fuerza a disco)."""
        if durabilidad not in ("NORMAL", "FULL"):
            raise ValueError("durabilidad debe ser NORMAL o FULL")
        self.ruta, self.pepper, self.durabilidad = Path(ruta), pepper.encode("utf-8"), durabilidad
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._turno = threading.Lock()          # un solo guardado a la vez DENTRO de este proceso (en fila, sin pelear por la base)

    def abrir(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.ruta), timeout=30, isolation_level=None)   # manejamos las transacciones a mano
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        con.execute(f"PRAGMA synchronous={self.durabilidad}")
        return con

    def _rellenar_columnas_nuevas(self, con: sqlite3.Connection) -> None:
        """Pagos guardados antes de existir las columnas del panel: se les calculan desde lo que ya había (sin tocar la decisión)."""
        pendientes = con.execute("SELECT id, fila_json FROM evaluaciones WHERE id_seguimiento IS NULL").fetchall()
        if not pendientes:
            return
        usados = {r[0] for r in con.execute("SELECT id_seguimiento FROM evaluaciones WHERE id_seguimiento IS NOT NULL")}
        con.execute("BEGIN IMMEDIATE")
        for r in pendientes:
            fila = json.loads(r["fila_json"])
            nuevo = id_seguimiento_libre(usados.__contains__)
            usados.add(nuevo)
            huella = fila.get("huella_dispositivo")
            con.execute("UPDATE evaluaciones SET id_seguimiento=?, ip_codigo=?, disp_codigo=?, monto_cop=? WHERE id=?",
                        (nuevo, self.codigo_ip(fila.get("ip_huella")), None if not huella or str(huella).startswith("SIN-") else self.codigo_dispositivo(huella),
                         fila.get("monto_cop"), r["id"]))
        con.execute("COMMIT")

    def inicializar(self) -> None:
        con = self.abrir()
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(DDL)
            columnas = {r["name"] for r in con.execute("PRAGMA table_info(evaluaciones)")}
            for nueva in ("red_origen", "estado_origen"):          # bases creadas antes de existir estas columnas
                if nueva not in columnas:
                    con.execute(f"ALTER TABLE evaluaciones ADD COLUMN {nueva} TEXT NOT NULL DEFAULT 'comercio'")
            for nueva, tipo in (("id_seguimiento", "TEXT"), ("ip_codigo", "TEXT"), ("disp_codigo", "TEXT"), ("monto_cop", "REAL"), ("no_enviados_json", "TEXT")):
                if nueva not in columnas:
                    con.execute(f"ALTER TABLE evaluaciones ADD COLUMN {nueva} {tipo}")
            self._rellenar_columnas_nuevas(con)
            con.executescript(INDICES_PANEL)
            if con.execute("SELECT 1 FROM comercios WHERE id=?", (PLATAFORMA,)).fetchone() is None:
                con.execute("INSERT INTO comercios (id, nombre, creado) VALUES (?, 'PLATAFORMA', ?)", (PLATAFORMA, ahora_iso()))
        finally:
            con.close()

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

    # ------------------------------------------------------------ claves
    def _hash(self, clave: str) -> str:
        return hmac.new(self.pepper, clave.encode("utf-8"), hashlib.sha256).hexdigest()

    def huella_ip(self, ip: str) -> str:
        """Huella IRREVERSIBLE de una IP (HMAC con el secreto del servicio): sirve para reconocer repeticiones sin guardar la IP."""
        return hmac.new(self.pepper, ("ip|" + str(ipaddress.ip_address(ip))).encode("utf-8"), hashlib.sha256).hexdigest()[:16]

    def codigo_ip(self, ip_huella: Optional[str]) -> Optional[str]:
        """Código corto (8 caracteres) de la huella de una IP, para agrupar y mostrar sin revelar la IP."""
        return str(ip_huella)[:8].upper() if ip_huella else None

    def codigo_dispositivo(self, huella: str) -> str:
        """Código corto e irreversible de la huella del dispositivo (la huella real no se muestra)."""
        return hmac.new(self.pepper, ("disp|" + str(huella)).encode("utf-8"), hashlib.sha256).hexdigest()[:8].upper()

    def crear_comercio(self, nombre: str) -> int:
        with self.transaccion() as con:
            cur = con.execute("INSERT INTO comercios (nombre, creado) VALUES (?, ?)", (nombre, ahora_iso()))
            self.auditar(con, None, None, "comercio_creado", None, {"nombre": nombre, "id": cur.lastrowid})
            return int(cur.lastrowid)

    def crear_clave(self, comercio_id: int, rol: str, etiqueta: str = "") -> str:
        """Devuelve la clave en claro UNA vez; en la base solo queda su hash."""
        clave = f"mia_{rol}_{secrets.token_urlsafe(32)}"
        with self.transaccion() as con:
            cur = con.execute("INSERT INTO claves (hash, comercio_id, rol, etiqueta, creada) VALUES (?,?,?,?,?)",
                              (self._hash(clave), comercio_id, rol, etiqueta, ahora_iso()))
            self.auditar(con, comercio_id, int(cur.lastrowid), "clave_creada", None, {"rol": rol})
        return clave

    def autenticar(self, clave: str) -> Optional[Principal]:
        with self.lectura() as con:
            fila = con.execute(
                "SELECT k.id, k.comercio_id, k.rol, c.nombre FROM claves k JOIN comercios c ON c.id = k.comercio_id "
                "WHERE k.hash = ? AND k.activa = 1 AND c.activo = 1", (self._hash(clave),)).fetchone()
        return Principal(fila["id"], fila["comercio_id"], fila["rol"], fila["nombre"]) if fila else None

    def desactivar_clave(self, clave_id: int) -> None:
        with self.transaccion() as con:
            con.execute("UPDATE claves SET activa = 0 WHERE id = ?", (clave_id,))
            self.auditar(con, None, clave_id, "clave_desactivada", None, {})

    # ------------------------------------------------------------ auditoría
    @staticmethod
    def auditar(con: sqlite3.Connection, comercio_id: Optional[int], clave_id: Optional[int], accion: str,
                id_evaluacion: Optional[str], detalle: Dict[str, Any]) -> None:
        con.execute("INSERT INTO auditoria (momento, comercio_id, clave_id, accion, id_evaluacion, detalle) VALUES (?,?,?,?,?,?)",
                    (ahora_iso(), comercio_id, clave_id, accion, id_evaluacion, json.dumps(detalle, ensure_ascii=False, default=str)))
