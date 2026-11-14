"""El esquema de la base y sus migraciones. La migración 1 está CONGELADA (ver migraciones.py y tests/test_migraciones.py): los cambios nuevos van como migración 2, 3…"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..migraciones import Migracion, aplicar, estado as estado_de_migraciones, listar_copias, sentencias
from .base import Base
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


class Esquema:
    def __init__(self, base: Base, seudonimos: Seudonimizador):
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
        return [Migracion(1, "Esquema inicial y adopción de bases anteriores", self.m1_esquema_inicial)]

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

    def inicializar(self, migraciones: Optional[Sequence[Migracion]] = None) -> List[int]:
        """Pone la base al día aplicando las migraciones que falten (ver migraciones.py). Devuelve los números de las que aplicó esta vez.

        Si la base tiene datos, antes de migrar se guarda una copia junto a ella (`<base>.antes-de-migrar-vN-FECHA.sqlite3`); si no se puede, no se migra."""
        con = self.base.abrir()
        try:
            con.execute("PRAGMA journal_mode=WAL")
        finally:
            con.close()
        return aplicar(
            self.base.ruta,
            self.base.abrir,
            migraciones if migraciones is not None else self.migraciones(),
            self.hay_datos,
            ahora_iso,
        )

    def estado_migraciones(self) -> List[Dict[str, Any]]:
        """Qué migraciones están aplicadas y cuáles faltan, sin tocar la base."""
        with self.base.lectura() as con:
            return estado_de_migraciones(con, self.migraciones())

    def copias_previas(self) -> List[Path]:
        """Las copias que se guardaron antes de migrar. No se borran solas: tienen los mismos datos que la base, así que cuídalas igual."""
        return listar_copias(self.base.ruta)
