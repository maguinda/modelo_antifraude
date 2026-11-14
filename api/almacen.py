"""Almacén SQLite: claves, comercios, evaluaciones, revisiones, resultados, lista negra y auditoría.

SQLite sirve para desarrollo y para empezar. Para producción se cambia por PostgreSQL manteniendo esta misma interfaz.
"""
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from .migraciones import Migracion, aplicar, estado as estado_de_migraciones, listar_copias, sentencias

PLATAFORMA = 1          # id del «comercio» al que pertenecen las claves de analistas y administradores


class ComercioNoAutorizado(Exception):
    """Un comercio no puede operar ahora: está suspendido (`comercio_suspendido`) o no tiene acuerdo de datos vigente (`sin_acuerdo`)."""
    def __init__(self, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.codigo, self.mensaje = codigo, mensaje


class ComercioInexistente(Exception):
    """El id de comercio no existe (o es el de la plataforma, que no se administra como un comercio)."""


class OperacionNoPermitida(Exception):
    """Algo que no se debe hacer nunca, ni siendo administrador (p. ej. suspender la plataforma y dejar sin acceso a todo el equipo)."""

DDL = """
CREATE TABLE IF NOT EXISTS comercios (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, creado TEXT NOT NULL, activo INTEGER NOT NULL DEFAULT 1,
    razon_social TEXT, nit TEXT, suspendido_en TEXT, motivo_suspension TEXT);
CREATE TABLE IF NOT EXISTS acuerdos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, comercio_id INTEGER NOT NULL REFERENCES comercios(id), version TEXT NOT NULL, firmado_en TEXT NOT NULL,
    vigente_hasta TEXT, referencia TEXT NOT NULL, registrado TEXT NOT NULL, registrado_por INTEGER);
CREATE TRIGGER IF NOT EXISTS acuerdos_no_se_edita BEFORE UPDATE ON acuerdos BEGIN SELECT RAISE(ABORT, 'los acuerdos no se editan: se registra uno nuevo'); END;
CREATE TRIGGER IF NOT EXISTS acuerdos_no_se_borra BEFORE DELETE ON acuerdos BEGIN SELECT RAISE(ABORT, 'los acuerdos no se borran'); END;
CREATE TABLE IF NOT EXISTS claves (
    id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT NOT NULL UNIQUE, comercio_id INTEGER NOT NULL REFERENCES comercios(id),
    rol TEXT NOT NULL CHECK (rol IN ('comercio','analista','admin')), etiqueta TEXT, activa INTEGER NOT NULL DEFAULT 1, creada TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evaluaciones (
    id TEXT PRIMARY KEY, comercio_id INTEGER NOT NULL REFERENCES comercios(id), id_transaccion TEXT NOT NULL,
    id_cliente TEXT NOT NULL, fecha TEXT NOT NULL, huella TEXT, fila_json TEXT NOT NULL, solicitud_hash TEXT NOT NULL,
    decision TEXT NOT NULL, decision_modelo TEXT, probabilidad REAL, motivos_json TEXT NOT NULL, reglas_json TEXT NOT NULL,
    modelo_nombre TEXT NOT NULL, modelo_version TEXT NOT NULL, modelo_conjunto TEXT NOT NULL, modo TEXT NOT NULL,
    latencia_ms REAL NOT NULL, creada TEXT NOT NULL, red_origen TEXT NOT NULL DEFAULT 'comercio', estado_origen TEXT NOT NULL DEFAULT 'comercio',
    id_seguimiento TEXT, ip_codigo TEXT, disp_codigo TEXT, monto_cop REAL, no_enviados_json TEXT, senales_json TEXT,
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


def _texto(valor: Any, minimo: int, maximo: int, nombre: str) -> str:
    """Texto libre limpio: sin espacios de más, sin caracteres de control ni < >, y dentro del largo permitido."""
    t = re.sub(r"\s+", " ", str(valor or "")).strip()
    if not minimo <= len(t) <= maximo or re.search(r"[\x00-\x1f\x7f<>]", t):
        raise ValueError(f"{nombre} debe tener entre {minimo} y {maximo} caracteres y no llevar signos < o >.")
    return t


DIAS_AVISO_VENCIMIENTO = 30


def estado_acuerdo(ultimo: Optional[Any], hoy: date) -> Dict[str, Any]:
    """El estado del acuerdo de datos de un comercio a partir de su último registro: sin_acuerdo, vigente, vence_pronto (en 30 días o menos) o vencido."""
    if ultimo is None:
        return {"estado": "sin_acuerdo", "version": None, "firmado_en": None, "vigente_hasta": None, "dias_para_vencer": None}
    hasta = date.fromisoformat(ultimo["vigente_hasta"]) if ultimo["vigente_hasta"] else None
    dias = (hasta - hoy).days if hasta else None
    estado = "vigente" if dias is None or dias > DIAS_AVISO_VENCIMIENTO else "vence_pronto" if dias >= 0 else "vencido"
    return {"estado": estado, "version": ultimo["version"], "firmado_en": ultimo["firmado_en"], "vigente_hasta": ultimo["vigente_hasta"], "dias_para_vencer": dias}


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
    usuario: str = ""           # para analistas y administradores: el nombre de la persona dueña de la clave


NOMBRE_MIN, NOMBRE_MAX = 3, 60
USO_CADA_SEGUNDOS = 3600        # el «último uso» de una clave se anota como mucho una vez por hora (para no escribir en cada petición)


def normalizar_nombre(nombre: str) -> str:
    n = re.sub(r"\s+", " ", str(nombre or "")).strip()
    if not NOMBRE_MIN <= len(n) <= NOMBRE_MAX or re.search(r"[\x00-\x1f\x7f<>]", n):
        raise ValueError(f"El nombre debe tener entre {NOMBRE_MIN} y {NOMBRE_MAX} caracteres y no llevar signos < o >.")
    return n


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
        for r in pendientes:
            fila = json.loads(r["fila_json"])
            nuevo = id_seguimiento_libre(usados.__contains__)
            usados.add(nuevo)
            huella = fila.get("huella_dispositivo")
            con.execute("UPDATE evaluaciones SET id_seguimiento=?, ip_codigo=?, disp_codigo=?, monto_cop=? WHERE id=?",
                        (nuevo, self.codigo_ip(fila.get("ip_huella")), None if not huella or str(huella).startswith("SIN-") else self.codigo_dispositivo(huella),
                         fila.get("monto_cop"), r["id"]))

    def _migraciones(self) -> List[Migracion]:
        """Los cambios de esquema, en orden. Para agregar uno: una función nueva y un número más al final. Nunca se edita ni se renumera uno ya publicado."""
        return [Migracion(1, "Esquema inicial y adopción de bases anteriores", self._m1_esquema_inicial)]

    @staticmethod
    def _hay_datos(con: sqlite3.Connection) -> bool:
        """¿La base ya tiene algo que valga la pena copiar antes de migrar? Una base recién creada (solo la plataforma) no."""
        tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        total = 0
        for tabla, donde in (("claves", ""), ("evaluaciones", ""), ("comercios", f" WHERE id != {PLATAFORMA}")):
            if tabla in tablas:
                total += con.execute(f"SELECT COUNT(*) FROM {tabla}{donde}").fetchone()[0]
        return total > 0

    def _m1_esquema_inicial(self, con: sqlite3.Connection) -> None:
        """Migración 1: deja la base con el esquema completo. Sirve igual para una base nueva y para una creada por versiones anteriores
        (cada paso comprueba lo que ya existe), que desde aquí quedan registradas y avanzan solo por migraciones numeradas."""
        for sentencia in sentencias(DDL):
            con.execute(sentencia)
        columnas = {r["name"] for r in con.execute("PRAGMA table_info(evaluaciones)")}
        for nueva in ("red_origen", "estado_origen"):          # bases creadas antes de existir estas columnas
            if nueva not in columnas:
                con.execute(f"ALTER TABLE evaluaciones ADD COLUMN {nueva} TEXT NOT NULL DEFAULT 'comercio'")
        for nueva, tipo in (("id_seguimiento", "TEXT"), ("ip_codigo", "TEXT"), ("disp_codigo", "TEXT"), ("monto_cop", "REAL"), ("no_enviados_json", "TEXT"), ("senales_json", "TEXT")):
            if nueva not in columnas:
                con.execute(f"ALTER TABLE evaluaciones ADD COLUMN {nueva} {tipo}")
        self._rellenar_columnas_nuevas(con)
        claves = {r["name"] for r in con.execute("PRAGMA table_info(claves)")}
        for nueva in ("ultimo_uso", "creada_por"):                      # bases creadas antes de existir estas columnas
            if nueva not in claves:
                con.execute(f"ALTER TABLE claves ADD COLUMN {nueva} " + ("TEXT" if nueva == "ultimo_uso" else "INTEGER"))
        comercios = {r["name"] for r in con.execute("PRAGMA table_info(comercios)")}
        for nueva in ("razon_social", "nit", "suspendido_en", "motivo_suspension"):          # bases creadas antes de existir estas columnas
            if nueva not in comercios:
                con.execute(f"ALTER TABLE comercios ADD COLUMN {nueva} TEXT")
        for sentencia in sentencias(INDICES_PANEL):
            con.execute(sentencia)
        if con.execute("SELECT 1 FROM comercios WHERE id=?", (PLATAFORMA,)).fetchone() is None:
            con.execute("INSERT INTO comercios (id, nombre, creado) VALUES (?, 'PLATAFORMA', ?)", (PLATAFORMA, ahora_iso()))

    def inicializar(self, migraciones: Optional[Sequence[Migracion]] = None) -> List[int]:
        """Pone la base al día aplicando las migraciones que falten (ver migraciones.py). Devuelve los números de las que aplicó esta vez.

        Si la base tiene datos, antes de migrar se guarda una copia junto a ella (`<base>.antes-de-migrar-vN-FECHA.sqlite3`); si no se puede, no se migra."""
        con = self.abrir()
        try:
            con.execute("PRAGMA journal_mode=WAL")
        finally:
            con.close()
        return aplicar(self.ruta, self.abrir, migraciones if migraciones is not None else self._migraciones(), self._hay_datos, ahora_iso)

    def estado_migraciones(self) -> List[Dict[str, Any]]:
        """Qué migraciones están aplicadas y cuáles faltan, sin tocar la base."""
        with self.lectura() as con:
            return estado_de_migraciones(con, self._migraciones())

    def copias_previas(self) -> List[Path]:
        """Las copias que se guardaron antes de migrar. No se borran solas: tienen los mismos datos que la base, así que cuídalas igual."""
        return listar_copias(self.ruta)

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

    def crear_comercio(self, nombre: str, razon_social: Optional[str] = None, nit: Optional[str] = None, por: Optional[int] = None) -> int:
        razon_social = _texto(razon_social, 2, 120, "La razón social") if razon_social else None
        if nit is not None and not re.fullmatch(r"[0-9][0-9.\-]{3,18}[0-9]", str(nit)):
            raise ValueError("El NIT debe llevar solo números, puntos o guion (de 5 a 20 caracteres).")
        with self.transaccion() as con:
            cur = con.execute("INSERT INTO comercios (nombre, creado, razon_social, nit) VALUES (?, ?, ?, ?)", (nombre, ahora_iso(), razon_social, nit))
            self.auditar(con, None, por, "comercio_creado", None, {"nombre": nombre, "id": cur.lastrowid, "con_nit": nit is not None})   # el NIT nunca va a la auditoría
            return int(cur.lastrowid)

    # ------------------------------------------------------------ suspensión y acuerdo de datos
    def verificar_comercio(self, con: sqlite3.Connection, comercio_id: int, exigir_acuerdo: bool = False, hoy: Optional[date] = None) -> None:
        """Se llama DENTRO del bloqueo de cada escritura de un comercio: así, ningún pago se guarda después de que se suspendió (se relee el estado
        aquí, no antes). La plataforma (analistas y administradores) nunca se bloquea."""
        if comercio_id == PLATAFORMA:
            return
        c = con.execute("SELECT activo FROM comercios WHERE id=?", (comercio_id,)).fetchone()
        if c is None or not c["activo"]:
            raise ComercioNoAutorizado("comercio_suspendido", "Este comercio está suspendido. Contacta al administrador del servicio.")
        if exigir_acuerdo:
            ult = con.execute("SELECT * FROM acuerdos WHERE comercio_id=? ORDER BY id DESC LIMIT 1", (comercio_id,)).fetchone()
            if estado_acuerdo(ult, hoy or date.today())["estado"] not in ("vigente", "vence_pronto"):
                raise ComercioNoAutorizado("sin_acuerdo", "Este comercio no tiene un acuerdo de datos vigente. Contacta al administrador del servicio.")

    def comercio_suspendido_por_clave(self, clave: str) -> bool:
        """¿Esta clave (válida y activa) es de un comercio suspendido? Solo se pregunta cuando la autenticación falló."""
        with self.lectura() as con:
            return con.execute("SELECT 1 FROM claves k JOIN comercios c ON c.id = k.comercio_id WHERE k.hash=? AND k.activa=1 AND c.activo=0",
                               (self._hash(clave),)).fetchone() is not None

    def suspender_comercio(self, comercio_id: int, motivo: str, por: Optional[int] = None) -> bool:
        """Devuelve True si se suspendió ahora, False si ya estaba suspendido (repetirlo no hace nada ni ensucia la auditoría)."""
        motivo = _texto(motivo, 3, 200, "El motivo")
        if comercio_id == PLATAFORMA:
            raise OperacionNoPermitida("El comercio interno de la plataforma no se puede suspender: dejaría sin acceso a todo el equipo.")
        with self.transaccion() as con:
            c = con.execute("SELECT activo FROM comercios WHERE id=?", (comercio_id,)).fetchone()
            if c is None:
                raise ComercioInexistente("No existe ese comercio.")
            if not c["activo"]:
                return False
            con.execute("UPDATE comercios SET activo=0, suspendido_en=?, motivo_suspension=? WHERE id=?", (ahora_iso(), motivo, comercio_id))
            self.auditar(con, comercio_id, por, "comercio_suspendido", None, {"motivo": motivo})
            return True

    def reactivar_comercio(self, comercio_id: int, por: Optional[int] = None) -> bool:
        with self.transaccion() as con:
            c = con.execute("SELECT activo FROM comercios WHERE id=?", (comercio_id,)).fetchone()
            if c is None:
                raise ComercioInexistente("No existe ese comercio.")
            if c["activo"]:
                return False
            con.execute("UPDATE comercios SET activo=1, suspendido_en=NULL, motivo_suspension=NULL WHERE id=?", (comercio_id,))
            self.auditar(con, comercio_id, por, "comercio_reactivado", None, {})
            return True

    def registrar_acuerdo(self, comercio_id: int, version: str, firmado_en: date, vigente_hasta: Optional[date], referencia: str,
                          por: Optional[int] = None, hoy: Optional[date] = None) -> int:
        """Guarda los DATOS de un acuerdo de datos (no el documento). El historial no se edita: uno nuevo reemplaza al anterior como vigente."""
        hoy = hoy or date.today()
        if not re.fullmatch(r"[\w .\-]{1,40}", str(version or "")):
            raise ValueError("La versión del acuerdo debe tener de 1 a 40 caracteres (letras, números, espacio, punto o guion).")
        if not re.fullmatch(r"[\w .,:#()\-]{1,120}", str(referencia or "")):
            raise ValueError("La referencia del archivo firmado debe tener de 1 a 120 caracteres, sin rutas ni signos raros (por ejemplo su nombre o su huella SHA-256).")
        if not isinstance(firmado_en, date) or isinstance(firmado_en, datetime) or (vigente_hasta is not None and (not isinstance(vigente_hasta, date) or isinstance(vigente_hasta, datetime))):
            raise ValueError("Las fechas del acuerdo deben ser fechas (AAAA-MM-DD).")
        if (firmado_en - hoy).days > 1:
            raise ValueError("La fecha de firma no puede ser futura.")
        if vigente_hasta is not None and vigente_hasta < firmado_en:
            raise ValueError("El acuerdo no puede vencer antes de su firma.")
        if comercio_id == PLATAFORMA:
            raise OperacionNoPermitida("La plataforma no es un comercio: no lleva acuerdo de datos.")
        with self.transaccion() as con:
            if con.execute("SELECT 1 FROM comercios WHERE id=?", (comercio_id,)).fetchone() is None:
                raise ComercioInexistente("No existe ese comercio.")
            cur = con.execute("INSERT INTO acuerdos (comercio_id, version, firmado_en, vigente_hasta, referencia, registrado, registrado_por) VALUES (?,?,?,?,?,?,?)",
                              (comercio_id, version, firmado_en.isoformat(), vigente_hasta.isoformat() if vigente_hasta else None, referencia, ahora_iso(), por))
            self.auditar(con, comercio_id, por, "acuerdo_registrado", None, {"version": version, "firmado_en": firmado_en.isoformat(),
                                                                                "vigente_hasta": vigente_hasta.isoformat() if vigente_hasta else None, "referencia": referencia})
            return int(cur.lastrowid)

    def historial_acuerdos(self, comercio_id: int, hoy: Optional[date] = None) -> Optional[List[Dict[str, Any]]]:
        """None si el comercio no existe. Del más reciente al más viejo; el primero es el que vale."""
        hoy = hoy or date.today()
        with self.lectura() as con:
            if comercio_id == PLATAFORMA or con.execute("SELECT 1 FROM comercios WHERE id=?", (comercio_id,)).fetchone() is None:
                return None
            filas = con.execute("SELECT * FROM acuerdos WHERE comercio_id=? ORDER BY id DESC", (comercio_id,)).fetchall()
        return [dict(estado_acuerdo(f, hoy) if i == 0 else {"estado": "reemplazado", "version": f["version"], "firmado_en": f["firmado_en"], "vigente_hasta": f["vigente_hasta"],
                                                          "dias_para_vencer": None}, referencia=f["referencia"], registrado=f["registrado"]) for i, f in enumerate(filas)]

    def listar_comercios(self, hoy: Optional[date] = None, con_nit: bool = False) -> List[Dict[str, Any]]:
        """Para el personal. El NIT (que en una persona natural es su cédula) solo sale si `con_nit`."""
        hoy = hoy or date.today()
        with self.lectura(instantanea=True) as con:
            filas = con.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM claves k WHERE k.comercio_id=c.id AND k.activa=1) AS claves_activas, "
                "(SELECT MAX(k.ultimo_uso) FROM claves k WHERE k.comercio_id=c.id) AS ultimo_uso, "
                "(SELECT COUNT(*) FROM evaluaciones e WHERE e.comercio_id=c.id) AS pagos FROM comercios c WHERE c.id != ? ORDER BY c.activo DESC, c.nombre", (PLATAFORMA,)).fetchall()
            ultimos = {a["comercio_id"]: a for a in con.execute("SELECT * FROM acuerdos a WHERE a.id = (SELECT MAX(id) FROM acuerdos WHERE comercio_id = a.comercio_id)")}
        return [{"id": f["id"], "nombre": f["nombre"], "razon_social": f["razon_social"], "nit": f["nit"] if con_nit else None,
                 "estado": "activo" if f["activo"] else "suspendido", "suspendido_en": f["suspendido_en"], "motivo_suspension": f["motivo_suspension"],
                 "creado": f["creado"], "claves_activas": f["claves_activas"], "ultimo_uso": f["ultimo_uso"], "pagos": f["pagos"],
                 "acuerdo": estado_acuerdo(ultimos.get(f["id"]), hoy)} for f in filas]

    def crear_clave(self, comercio_id: int, rol: str, etiqueta: str = "", creada_por: Optional[int] = None) -> str:
        """Devuelve la clave en claro UNA vez; en la base solo queda su hash."""
        return self._crear_clave(comercio_id, rol, etiqueta, creada_por)[1]

    def _crear_clave(self, comercio_id: int, rol: str, etiqueta: str, creada_por: Optional[int], con: Optional[sqlite3.Connection] = None) -> Tuple[int, str]:
        clave = f"mia_{rol}_{secrets.token_urlsafe(32)}"

        def insertar(c: sqlite3.Connection) -> int:
            cur = c.execute("INSERT INTO claves (hash, comercio_id, rol, etiqueta, creada, creada_por) VALUES (?,?,?,?,?,?)",
                            (self._hash(clave), comercio_id, rol, etiqueta, ahora_iso(), creada_por))
            self.auditar(c, comercio_id, int(cur.lastrowid), "clave_creada", None, {"rol": rol, "nombre": etiqueta or None, "creada_por": creada_por})
            return int(cur.lastrowid)
        if con is not None:
            return insertar(con), clave
        with self.transaccion() as c:
            return insertar(c), clave

    def crear_usuario(self, rol: str, nombre: str, creada_por: Optional[int] = None) -> Tuple[int, str]:
        """Una clave PARA UNA PERSONA (analista o administrador): lleva su nombre y no se puede repetir entre las activas.
        Devuelve (id de la clave, clave en claro). La clave se muestra una sola vez."""
        if rol not in ("analista", "admin"):
            raise ValueError("Solo los analistas y administradores son personas con nombre.")
        nombre = normalizar_nombre(nombre)
        with self.transaccion() as con:
            activos = [r[0] or "" for r in con.execute("SELECT etiqueta FROM claves WHERE comercio_id=? AND activa=1 AND rol IN ('analista','admin')", (PLATAFORMA,))]
            if any(re.sub(r"\s+", " ", a).strip().casefold() == nombre.casefold() for a in activos):      # en Python: el lower() de SQLite no reconoce las mayúsculas con tilde
                raise ValueError("Ya hay una clave activa con ese nombre. Si es la misma persona, usa «rotar-clave»; si es otra, usa un nombre distinto.")
            return self._crear_clave(PLATAFORMA, rol, nombre, creada_por, con)

    def rotar_clave(self, clave_id: int, creada_por: Optional[int] = None) -> Tuple[int, str]:
        """Cambia la clave de una persona: crea una nueva con el mismo rol y nombre y apaga la anterior, en un solo paso."""
        with self.transaccion() as con:
            v = con.execute("SELECT * FROM claves WHERE id=? AND activa=1", (clave_id,)).fetchone()
            if v is None:
                raise ValueError("No existe una clave activa con ese número.")
            con.execute("UPDATE claves SET activa=0 WHERE id=?", (clave_id,))
            self.auditar(con, v["comercio_id"], clave_id, "clave_desactivada", None, {"motivo": "rotación"})
            return self._crear_clave(v["comercio_id"], v["rol"], v["etiqueta"] or "", creada_por, con)

    def listar_claves(self) -> List[Dict[str, Any]]:
        """Para administrar: nunca incluye la clave ni su hash."""
        with self.lectura() as con:
            return [dict(r) for r in con.execute(
                "SELECT k.id, k.rol, k.comercio_id, c.nombre AS comercio, k.etiqueta, k.activa, k.creada, k.ultimo_uso, k.creada_por "
                "FROM claves k JOIN comercios c ON c.id = k.comercio_id ORDER BY k.activa DESC, k.rol, k.id")]

    def autenticar(self, clave: str) -> Optional[Principal]:
        with self.lectura() as con:
            fila = con.execute(
                "SELECT k.id, k.comercio_id, k.rol, c.nombre, k.etiqueta, k.ultimo_uso FROM claves k JOIN comercios c ON c.id = k.comercio_id "
                "WHERE k.hash = ? AND k.activa = 1 AND c.activo = 1", (self._hash(clave),)).fetchone()
        if fila is None:
            return None
        self._anotar_uso(fila["id"], fila["ultimo_uso"])
        return Principal(fila["id"], fila["comercio_id"], fila["rol"], fila["nombre"], (fila["etiqueta"] or "") if fila["rol"] in ("analista", "admin") else "")

    def _anotar_uso(self, clave_id: int, ultimo: Optional[str]) -> None:
        """Anota cuándo se usó la clave por última vez, como mucho una vez por hora; si no se puede escribir en ese momento, no pasa nada."""
        try:
            if ultimo and (datetime.now() - datetime.fromisoformat(ultimo)).total_seconds() < USO_CADA_SEGUNDOS:
                return
            with self.transaccion() as con:
                con.execute("UPDATE claves SET ultimo_uso=? WHERE id=?", (ahora_iso(), clave_id))
        except (sqlite3.Error, ValueError):
            pass

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
