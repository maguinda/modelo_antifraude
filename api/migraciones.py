"""Migraciones versionadas de la base: cada cambio de esquema es un paso NUMERADO que se aplica una sola vez.

Qué garantiza (y qué no):
  - Cada migración corre en UNA transacción: si falla a la mitad, no queda nada a medias, y no se registra como aplicada.
  - Dos procesos arrancando a la vez (uvicorn --workers) no aplican la misma migración dos veces: se toma el bloqueo de escritura y el estado
    se RELEE dentro de él.
  - Antes de aplicar el primer cambio pendiente a una base que ya tiene datos, se guarda una COPIA con la API de respaldo de SQLite. Si la copia
    no se puede hacer, NO se migra (fallar cerrado es mejor que migrar sin red de seguridad). Las copias no se borran solas.
  - Una base con migraciones que este código no conoce (de una versión más nueva) se rechaza: abrirla con código viejo podría dañarla.
  - NO deshace migraciones: protege de bajar de versión, no revierte. Para volver atrás se restaura la copia o el respaldo cifrado.

Para agregar un cambio de esquema: una función nueva `(conexion) -> None` y un `Migracion` con el siguiente número al final de la lista de
`Almacen._migraciones`. Nunca se edita ni se renumera una migración ya publicada.
"""
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Sequence

TABLA = "CREATE TABLE IF NOT EXISTS migraciones (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, aplicada TEXT NOT NULL)"
SEPARADOR_COPIA = ".antes-de-migrar-"


class BaseMasNueva(RuntimeError):
    """La base tiene migraciones que este código no conoce: la creó una versión más nueva. No se abre para no dañarla."""


class CopiaPreviaFallo(RuntimeError):
    """No se pudo guardar la copia previa a migrar, así que NO se migró nada."""


@dataclass(frozen=True)
class Migracion:
    id: int
    nombre: str
    aplicar: Callable[[sqlite3.Connection], None]


def sentencias(sql: str) -> List[str]:
    """Parte un texto SQL en sentencias sueltas (respeta los triggers, que llevan «;» por dentro), para ejecutarlas una a una DENTRO de una transacción.

    `executescript` no sirve aquí: confirma la transacción en curso antes de empezar y rompería la atomicidad."""
    salida, actual = [], ""
    for linea in sql.splitlines(keepends=True):
        actual += linea
        if sqlite3.complete_statement(actual):
            if actual.strip():
                salida.append(actual.strip())
            actual = ""
    if actual.strip():
        raise ValueError("SQL incompleto al final del texto: " + actual.strip()[:60])
    return salida


def validar(migraciones: Sequence[Migracion]) -> None:
    """Un error de programación, no de datos: los números deben ser 1, 2, 3… sin saltos ni repeticiones, y cada una con nombre."""
    ids = [m.id for m in migraciones]
    if ids != list(range(1, len(ids) + 1)):
        raise ValueError(f"Las migraciones deben numerarse 1, 2, 3… sin saltos ni repetidas (hay {ids}).")
    if any(not m.nombre.strip() for m in migraciones):
        raise ValueError("Cada migración necesita un nombre.")


def _tiene_tabla(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='migraciones'").fetchone() is not None


def estado(con: sqlite3.Connection, migraciones: Sequence[Migracion]) -> List[Dict]:
    """Qué hay aplicado y qué falta, SIN tocar la base. Las que la base tiene y este código no conoce salen marcadas como `desconocida`."""
    aplicadas = {r[0]: (r[1], r[2]) for r in con.execute("SELECT id, nombre, aplicada FROM migraciones")} if _tiene_tabla(con) else {}
    filas = [{"id": m.id, "nombre": m.nombre, "aplicada": aplicadas[m.id][1] if m.id in aplicadas else None, "desconocida": False} for m in migraciones]
    conocidas = {m.id for m in migraciones}
    filas += [{"id": i, "nombre": n, "aplicada": a, "desconocida": True} for i, (n, a) in sorted(aplicadas.items()) if i not in conocidas]
    return filas


def listar_copias(ruta: Path) -> List[Path]:
    return sorted(ruta.parent.glob(ruta.name + SEPARADOR_COPIA + "*.sqlite3"))     # solo las copias, no sus archivos auxiliares


def copia_previa(ruta: Path, primera: int, ahora: Callable[[], datetime] = datetime.now) -> Path:
    """Guarda una copia consistente de la base (aunque haya alguien escribiendo) junto a ella, con permisos 600, y comprueba que está sana."""
    marca = ahora().strftime("%Y%m%d-%H%M%S")
    destino = ruta.with_name(f"{ruta.name}{SEPARADOR_COPIA}v{primera}-{marca}.sqlite3")
    n = 1
    while destino.exists():
        n += 1
        destino = ruta.with_name(f"{ruta.name}{SEPARADOR_COPIA}v{primera}-{marca}-{n}.sqlite3")
    origen = dst = None
    try:
        origen = sqlite3.connect(str(ruta), timeout=30)
        dst = sqlite3.connect(str(destino))
        origen.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")                 # la copia hereda el modo WAL de la base; así queda como UN solo archivo, sin -wal ni -shm
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise CopiaPreviaFallo("La copia previa salió dañada.")
    except CopiaPreviaFallo:
        _quitar(destino)
        raise
    except Exception as e:
        _quitar(destino)
        raise CopiaPreviaFallo(f"No se pudo guardar la copia previa a migrar ({type(e).__name__}). No se migró nada: libera espacio o permisos y reintenta.") from e
    finally:
        for c in (dst, origen):
            if c is not None:
                c.close()
    os.chmod(destino, 0o600)               # tiene los mismos datos que la base: mismos permisos
    return destino


def _quitar(archivo: Path) -> None:
    try:
        archivo.unlink()
    except OSError:
        pass


def aplicar(ruta: Path, abrir: Callable[[], sqlite3.Connection], migraciones: Sequence[Migracion], hay_datos: Callable[[sqlite3.Connection], bool],
            ahora_iso: Callable[[], str]) -> List[int]:
    """Aplica en orden las migraciones que falten. Devuelve los números de las que aplicó ESTA llamada (vacío si ya estaba todo)."""
    validar(migraciones)
    con = abrir()
    hechas: List[int] = []
    copia = None
    try:
        con.execute(TABLA)
        conocidas = {m.id for m in migraciones}
        desconocidas = sorted(r[0] for r in con.execute("SELECT id FROM migraciones") if r[0] not in conocidas)
        if desconocidas:
            raise BaseMasNueva(f"Esta base tiene migraciones que este programa no conoce ({desconocidas}): la creó una versión más nueva. "
                               "No se abre para no dañarla; actualiza el programa o restaura una copia anterior.")
        for m in migraciones:
            con.execute("BEGIN IMMEDIATE")                        # una sola escritura a la vez, también entre procesos
            try:
                ya = {r[0] for r in con.execute("SELECT id FROM migraciones")}          # se relee DENTRO del bloqueo: otro proceso pudo aplicarla
                if m.id in ya:
                    con.execute("COMMIT")
                    continue
                if copia is None and hay_datos(con):
                    copia = copia_previa(ruta, m.id)
                m.aplicar(con)
                con.execute("INSERT INTO migraciones (id, nombre, aplicada) VALUES (?,?,?)", (m.id, m.nombre, ahora_iso()))
                con.execute("COMMIT")
                hechas.append(m.id)
            except BaseException:
                try:
                    con.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
    finally:
        con.close()
    return hechas
