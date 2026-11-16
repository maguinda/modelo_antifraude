"""El esquema de la base y sus migraciones. La migración 1 está CONGELADA (ver migraciones.py y tests/test_migraciones.py): los cambios nuevos van como migración 2, 3…"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..migraciones import (
    Migracion,
    aplicar,
    aplicar_pg,
    estado as estado_de_migraciones,
    listar_copias,
    sentencias,
)
from . import esquema_pg
from .base import BaseDeDatos
from .comunes import PLATAFORMA, ahora_iso, id_seguimiento_libre
from .seudonimos import Seudonimizador

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

# Migración 4: el SQL portable (tampoco se mezcla con DDL). `seq` se agrega en la migración, con ALTER TABLE.
SQL_PORTABLE = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_eval_seq ON evaluaciones (seq);
CREATE TABLE IF NOT EXISTS evaluacion_reglas (
    id_evaluacion TEXT NOT NULL REFERENCES evaluaciones(id), posicion INTEGER NOT NULL, regla TEXT NOT NULL, PRIMARY KEY (id_evaluacion, posicion));
CREATE INDEX IF NOT EXISTS ix_eval_reglas_regla ON evaluacion_reglas (regla);
"""

# Migración 5: el índice de la consulta de deriva. Lleva TODAS las columnas que esa consulta lee (modelo, versión, conjunto, momento, origen y puntaje), así se
# responde solo con el índice, sin ir a cada fila: con 600 000 pagos, de 78 a 5 ms en la ventana de 7 días (medido en SQLite).
INDICE_DERIVA = """
CREATE INDEX IF NOT EXISTS ix_eval_modelo_deriva ON evaluaciones (modelo_nombre, modelo_version, modelo_conjunto, creada, estado_origen, probabilidad);
"""

# Migración 6: búsquedas por cliente o por transacción SIN saber el comercio (la caja de búsqueda del panel de analistas). Los índices que ya existen empiezan por
# comercio_id y no sirven en PostgreSQL para eso (SQLite a veces los salta con estadísticas, pero no con muchos comercios).
INDICES_BUSQUEDA = """
CREATE INDEX IF NOT EXISTS ix_eval_cliente_global ON evaluaciones (id_cliente);
CREATE INDEX IF NOT EXISTS ix_eval_transaccion_global ON evaluaciones (id_transaccion);
"""

# Migración 3 (no se mezcla con DDL: la migración 1 está congelada y se compara por huella)
REGISTRO_MODELOS = """
CREATE TABLE IF NOT EXISTS modelos_registro (
    id INTEGER PRIMARY KEY AUTOINCREMENT, archivo TEXT NOT NULL, sha256 TEXT NOT NULL, nombre TEXT NOT NULL, version TEXT NOT NULL, conjunto TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('principal','sin_estado')), estado TEXT NOT NULL DEFAULT 'candidato' CHECK (estado IN ('candidato','en_uso','archivado')),
    referencia_json TEXT, registrado TEXT NOT NULL, promovido TEXT, archivado TEXT, UNIQUE (sha256, rol));
CREATE UNIQUE INDEX IF NOT EXISTS un_modelo_en_uso_por_rol ON modelos_registro(rol) WHERE estado = 'en_uso';
"""


class Esquema:
    def __init__(self, base: BaseDeDatos, seudonimos: Seudonimizador):
        self.base, self.seudonimos = base, seudonimos

    def rellenar_columnas_nuevas(self, con: sqlite3.Connection) -> None:
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
            con.execute(
                "UPDATE evaluaciones SET id_seguimiento=?, ip_codigo=?, disp_codigo=?, monto_cop=? WHERE id=?",
                (
                    nuevo,
                    self.seudonimos.codigo_ip(fila.get("ip_huella")),
                    None
                    if not huella or str(huella).startswith("SIN-")
                    else self.seudonimos.codigo_dispositivo(huella),
                    fila.get("monto_cop"),
                    r["id"],
                ),
            )

    def migraciones(self) -> List[Migracion]:
        """Los cambios de esquema, en orden. Para agregar uno: una función nueva y un número más al final. Nunca se edita ni se renumera uno ya publicado."""
        if getattr(self.base, "motor", "sqlite") == "postgresql":
            return self.migraciones_pg()
        return [
            Migracion(1, "Esquema inicial y adopción de bases anteriores", self.m1_esquema_inicial),
            Migracion(2, "Segundo factor (TOTP) de las claves de administrador", self.m2_mfa_admin),
            Migracion(3, "Registro de modelos con huella SHA-256", self.m3_registro_modelos),
            Migracion(
                4,
                "SQL portable: secuencia de evaluaciones, reglas por evaluación y minutos de resolución",
                self.m4_sql_portable,
            ),
            Migracion(5, "Índice de la consulta de deriva", self.m5_indice_deriva),
            Migracion(6, "Índices de búsqueda por cliente y por transacción", self.m6_indices_busqueda),
        ]

    def migraciones_pg(self) -> List[Migracion]:
        """Las mismas cuatro migraciones (mismos números y nombres) para PostgreSQL: solo DDL, ver `esquema_pg.py`."""
        return [
            Migracion(
                1,
                "Esquema inicial y adopción de bases anteriores",
                lambda con: esquema_pg.m1(con, DDL, INDICES_PANEL, PLATAFORMA, ahora_iso()),
            ),
            Migracion(2, "Segundo factor (TOTP) de las claves de administrador", esquema_pg.m2),
            Migracion(3, "Registro de modelos con huella SHA-256", lambda con: esquema_pg.m3(con, REGISTRO_MODELOS)),
            Migracion(
                4,
                "SQL portable: secuencia de evaluaciones, reglas por evaluación y minutos de resolución",
                lambda con: esquema_pg.m4(con, SQL_PORTABLE),
            ),
            Migracion(5, "Índice de la consulta de deriva", lambda con: esquema_pg.m5(con, INDICE_DERIVA)),
            Migracion(
                6, "Índices de búsqueda por cliente y por transacción", lambda con: esquema_pg.m6(con, INDICES_BUSQUEDA)
            ),
        ]

    @staticmethod
    def hay_datos(con: sqlite3.Connection) -> bool:
        """¿La base ya tiene algo que valga la pena copiar antes de migrar? Una base recién creada (solo la plataforma) no."""
        tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        total = 0
        for tabla, donde in (("claves", ""), ("evaluaciones", ""), ("comercios", f" WHERE id != {PLATAFORMA}")):
            if tabla in tablas:
                total += con.execute(f"SELECT COUNT(*) FROM {tabla}{donde}").fetchone()[0]
        return total > 0

    def m1_esquema_inicial(self, con: sqlite3.Connection) -> None:
        """Migración 1: deja la base con el esquema completo. Sirve igual para una base nueva y para una creada por versiones anteriores
        (cada paso comprueba lo que ya existe), que desde aquí quedan registradas y avanzan solo por migraciones numeradas."""
        for sentencia in sentencias(DDL):
            con.execute(sentencia)
        columnas = {r["name"] for r in con.execute("PRAGMA table_info(evaluaciones)")}
        for nueva in ("red_origen", "estado_origen"):  # bases creadas antes de existir estas columnas
            if nueva not in columnas:
                con.execute(f"ALTER TABLE evaluaciones ADD COLUMN {nueva} TEXT NOT NULL DEFAULT 'comercio'")
        for nueva, tipo in (
            ("id_seguimiento", "TEXT"),
            ("ip_codigo", "TEXT"),
            ("disp_codigo", "TEXT"),
            ("monto_cop", "REAL"),
            ("no_enviados_json", "TEXT"),
            ("senales_json", "TEXT"),
        ):
            if nueva not in columnas:
                con.execute(f"ALTER TABLE evaluaciones ADD COLUMN {nueva} {tipo}")
        self.rellenar_columnas_nuevas(con)
        claves = {r["name"] for r in con.execute("PRAGMA table_info(claves)")}
        for nueva in ("ultimo_uso", "creada_por"):  # bases creadas antes de existir estas columnas
            if nueva not in claves:
                con.execute(
                    f"ALTER TABLE claves ADD COLUMN {nueva} " + ("TEXT" if nueva == "ultimo_uso" else "INTEGER")
                )
        comercios = {r["name"] for r in con.execute("PRAGMA table_info(comercios)")}
        for nueva in (
            "razon_social",
            "nit",
            "suspendido_en",
            "motivo_suspension",
        ):  # bases creadas antes de existir estas columnas
            if nueva not in comercios:
                con.execute(f"ALTER TABLE comercios ADD COLUMN {nueva} TEXT")
        for sentencia in sentencias(INDICES_PANEL):
            con.execute(sentencia)
        if con.execute("SELECT 1 FROM comercios WHERE id=?", (PLATAFORMA,)).fetchone() is None:
            con.execute(
                "INSERT INTO comercios (id, nombre, creado) VALUES (?, 'PLATAFORMA', ?)", (PLATAFORMA, ahora_iso())
            )

    def m2_mfa_admin(self, con: sqlite3.Connection) -> None:
        """Migración 2: las columnas del segundo factor en `claves`. Una clave existente queda SIN segundo factor (nada cambia para ella hasta que se active)."""
        columnas = {r["name"] for r in con.execute("PRAGMA table_info(claves)")}
        for nueva, tipo in (
            ("mfa_secreto", "TEXT"),  # el secreto TOTP en base32; NULL = sin segundo factor
            ("mfa_confirmada", "INTEGER NOT NULL DEFAULT 0"),  # 0 = secreto creado pero aún sin confirmar con un código
            ("mfa_ultimo_paso", "INTEGER NOT NULL DEFAULT 0"),  # último paso de 30 s aceptado: un código no se repite
            ("mfa_fallos", "INTEGER NOT NULL DEFAULT 0"),  # códigos malos seguidos
            ("mfa_bloqueo_hasta", "REAL"),  # instante (epoch) hasta el que la clave está bloqueada
        ):
            if nueva not in columnas:
                con.execute(f"ALTER TABLE claves ADD COLUMN {nueva} {tipo}")

    def m3_registro_modelos(self, con: sqlite3.Connection) -> None:
        """Migración 3: la tabla del registro de modelos (ver repositorios/modelos_registro.py). Un índice único parcial garantiza un solo modelo EN USO por rol."""
        for sentencia in sentencias(REGISTRO_MODELOS):
            con.execute(sentencia)

    @staticmethod
    def _reglas_de(texto: Any) -> List[Any]:
        """Las reglas guardadas en un `reglas_json` viejo. Si el texto está dañado o no es una lista, esa evaluación queda sin filas de reglas
        (antes bloqueaba el arranque): el JSON original sigue intacto en `evaluaciones`."""
        try:
            reglas = json.loads(texto or "[]")
        except (TypeError, ValueError):
            return []
        return reglas if isinstance(reglas, list) else []

    def m4_sql_portable(self, con: sqlite3.Connection) -> None:
        """Migración 4: lo que antes dependía de funciones propias de SQLite pasa a columnas y tablas normales, que valen igual en cualquier motor:
          - `evaluaciones.seq`: el orden de llegada (antes `rowid`, que solo existe en SQLite). Se rellena con `rowid`, así el orden no cambia.
          - `evaluacion_reglas`: una fila por regla activada en cada evaluación (antes se leía el JSON con `json_each`). Se rellena desde `reglas_json`.
          - `revisiones.minutos_resolucion`: lo que tardó el analista (antes `julianday`). Se rellena con la misma cuenta de antes.
        Es repetible: comprueba lo que ya existe."""
        evaluaciones = {r["name"] for r in con.execute("PRAGMA table_info(evaluaciones)")}
        if "seq" not in evaluaciones:
            con.execute("ALTER TABLE evaluaciones ADD COLUMN seq INTEGER")
            con.execute("UPDATE evaluaciones SET seq = rowid")
        for sentencia in sentencias(SQL_PORTABLE):
            con.execute(sentencia)
        if con.execute("SELECT 1 FROM evaluacion_reglas LIMIT 1").fetchone() is None:
            filas = []
            for r in con.execute("SELECT id, reglas_json FROM evaluaciones"):
                for posicion, regla in enumerate(self._reglas_de(r["reglas_json"])):
                    filas.append((r["id"], posicion, str(regla.get("regla", "?")) if isinstance(regla, dict) else "?"))
                if len(filas) >= 5000:
                    con.executemany(
                        "INSERT INTO evaluacion_reglas (id_evaluacion, posicion, regla) VALUES (?,?,?)", filas
                    )
                    filas = []
            if filas:
                con.executemany("INSERT INTO evaluacion_reglas (id_evaluacion, posicion, regla) VALUES (?,?,?)", filas)
        revisiones = {r["name"] for r in con.execute("PRAGMA table_info(revisiones)")}
        if "minutos_resolucion" not in revisiones:
            con.execute("ALTER TABLE revisiones ADD COLUMN minutos_resolucion REAL")
            con.execute(
                "UPDATE revisiones SET minutos_resolucion = (julianday(resuelta) - julianday(creada)) * 1440 WHERE estado = 'resuelta'"
            )

    def m5_indice_deriva(self, con: sqlite3.Connection) -> None:
        """Migración 5: solo un índice (repetible: IF NOT EXISTS). No toca datos."""
        for sentencia in sentencias(INDICE_DERIVA):
            con.execute(sentencia)

    def m6_indices_busqueda(self, con: sqlite3.Connection) -> None:
        """Migración 6: dos índices (repetible). No toca datos."""
        for sentencia in sentencias(INDICES_BUSQUEDA):
            con.execute(sentencia)

    @staticmethod
    def analizar_si_hace_falta(con: sqlite3.Connection, minimo_filas: int = 10_000, forzar: bool = False) -> bool:
        """SQLite elige el plan de cada consulta según estadísticas (`ANALYZE`) que este programa nunca calculaba: con millones de pagos eso volvía una consulta de
        0,4 s en una de más de 40 s. Las calcula (1,9 s con 3 millones de pagos) si la tabla ya es grande y no hay estadísticas o la tabla creció o se achicó a más del doble."""
        try:
            filas = con.execute("SELECT COALESCE(MAX(seq), 0) FROM evaluaciones").fetchone()[0]
        except sqlite3.Error:
            return False  # base sin la tabla o sin la columna: nada que analizar todavía
        if filas < minimo_filas:
            return False
        if forzar:  # se acaban de crear índices nuevos: no tienen estadísticas aunque las de la tabla sean recientes
            con.execute("ANALYZE")
            return True
        try:
            previa = con.execute(
                "SELECT stat FROM sqlite_stat1 WHERE tbl = 'evaluaciones' ORDER BY idx IS NOT NULL, idx LIMIT 1"
            ).fetchone()
            conocidas = int(str(previa[0]).split()[0]) if previa else 0
        except (sqlite3.Error, ValueError, IndexError):
            conocidas = 0
        if conocidas and conocidas / 2 <= filas <= conocidas * 2:
            return False
        con.execute("ANALYZE")
        return True

    def inicializar(self, migraciones: Optional[Sequence[Migracion]] = None) -> List[int]:
        """Pone la base al día aplicando las migraciones que falten (ver migraciones.py). Devuelve los números de las que aplicó esta vez.

        Si la base tiene datos, antes de migrar se guarda una copia junto a ella (`<base>.antes-de-migrar-vN-FECHA.sqlite3`); si no se puede, no se migra."""
        if getattr(self.base, "motor", "sqlite") == "postgresql":
            return aplicar_pg(self.base, migraciones if migraciones is not None else self.migraciones(), ahora_iso)
        con = self.base.abrir()
        try:
            con.execute("PRAGMA journal_mode=WAL")
        finally:
            con.close()
        hechas = aplicar(
            self.base.ruta,
            self.base.abrir,
            migraciones if migraciones is not None else self.migraciones(),
            self.hay_datos,
            ahora_iso,
        )
        con = self.base.abrir()
        try:
            self.analizar_si_hace_falta(con, forzar=6 in hechas)  # PostgreSQL lo hace solo (autovacuum); SQLite no
        except sqlite3.Error:
            pass  # las estadísticas son una mejora, nunca un motivo para no arrancar
        finally:
            con.close()
        return hechas

    def estado_migraciones(self) -> List[Dict[str, Any]]:
        """Qué migraciones están aplicadas y cuáles faltan, sin tocar la base."""
        with self.base.lectura() as con:
            return estado_de_migraciones(con, self.migraciones())

    def copias_previas(self) -> List[Path]:
        """Las copias que se guardaron antes de migrar. No se borran solas: tienen los mismos datos que la base, así que cuídalas igual."""
        if getattr(self.base, "motor", "sqlite") == "postgresql":
            return []  # en PostgreSQL la copia previa es un pg_dump que hace quien opera
        return listar_copias(self.base.ruta)
