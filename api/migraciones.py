"""Migraciones versionadas de la base: cada cambio de esquema es un paso NUMERADO que se aplica una sola vez.

Qué garantiza (y qué no):
  - Cada migración corre en UNA transacción: si falla a la mitad, no queda nada a medias, y no se registra como aplicada.
  - Dos procesos arrancando a la vez (uvicorn --workers) no aplican la misma migración dos veces: se toma el bloqueo de escritura y el estado
    se RELEE dentro de él. Además, un candado de archivo cubre TODA la actualización: el segundo proceso espera a que el primero termine y no repite
    ni la copia previa (antes, uno que llegaba entre dos migraciones del primero hacía una copia de más).
  - Antes de aplicar el primer cambio pendiente a una base que ya tiene datos, se guarda una COPIA (UNA por actualización, aunque se apliquen varios
    cambios o arranquen varios procesos a la vez) con la API de respaldo de SQLite. Si la copia
    no se puede hacer, NO se migra (fallar cerrado es mejor que migrar sin red de seguridad). Las copias no se borran solas.
  - Una base con migraciones que este código no conoce (de una versión más nueva) se rechaza: abrirla con código viejo podría dañarla.
  - NO deshace migraciones: protege de bajar de versión, no revierte. Para volver atrás se restaura la copia o el respaldo cifrado.

Para agregar un cambio de esquema: una función nueva `(conexion) -> None` y un `Migracion` con el siguiente número al final de la lista de
`Almacen._migraciones`. Nunca se edita ni se renumera una migración ya publicada.
"""

import contextlib
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Sequence

try:
    import fcntl  # candado de archivo entre procesos (Linux y macOS)
except ImportError:  # Windows: no hay; cada migración sigue siendo atómica y exclusiva por el bloqueo de SQLite
    fcntl = None  # type: ignore[assignment]

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


def _tiene_tabla(con: Any) -> bool:
    if getattr(con, "motor", "sqlite") == "postgresql":
        return (
            con.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = 'migraciones'"
            ).fetchone()
            is not None
        )
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='migraciones'").fetchone() is not None


def estado(con: sqlite3.Connection, migraciones: Sequence[Migracion]) -> List[Dict]:
    """Qué hay aplicado y qué falta, SIN tocar la base. Las que la base tiene y este código no conoce salen marcadas como `desconocida`."""
    aplicadas = (
        {r[0]: (r[1], r[2]) for r in con.execute("SELECT id, nombre, aplicada FROM migraciones")}
        if _tiene_tabla(con)
        else {}
    )
    filas = [
        {
            "id": m.id,
            "nombre": m.nombre,
            "aplicada": aplicadas[m.id][1] if m.id in aplicadas else None,
            "desconocida": False,
        }
        for m in migraciones
    ]
    conocidas = {m.id for m in migraciones}
    filas += [
        {"id": i, "nombre": n, "aplicada": a, "desconocida": True}
        for i, (n, a) in sorted(aplicadas.items())
        if i not in conocidas
    ]
    return filas


def listar_copias(ruta: Path) -> List[Path]:
    return sorted(
        ruta.parent.glob(ruta.name + SEPARADOR_COPIA + "*.sqlite3")
    )  # solo las copias, no sus archivos auxiliares


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
        dst.execute(
            "PRAGMA journal_mode=DELETE"
        )  # la copia hereda el modo WAL de la base; así queda como UN solo archivo, sin -wal ni -shm
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise CopiaPreviaFallo("La copia previa salió dañada.")
    except CopiaPreviaFallo:
        _quitar(destino)
        raise
    except Exception as e:
        _quitar(destino)
        raise CopiaPreviaFallo(
            f"No se pudo guardar la copia previa a migrar ({type(e).__name__}). No se migró nada: libera espacio o permisos y reintenta."
        ) from e
    finally:
        for c in (dst, origen):
            if c is not None:
                c.close()
    os.chmod(destino, 0o600)  # tiene los mismos datos que la base: mismos permisos
    return destino


def _quitar(archivo: Path) -> None:
    try:
        archivo.unlink()
    except OSError:
        pass


ESPERA_MAXIMA_CANDADO = 600.0  # segundos: más que eso es que otro proceso se colgó con el candado, y es mejor fallar con un mensaje claro que esperar para siempre
log = logging.getLogger("modelo_ia.migraciones")


@contextlib.contextmanager
def candado_de_actualizacion(ruta: Path, espera_maxima: float = ESPERA_MAXIMA_CANDADO) -> Iterator[None]:
    """Un candado de ARCHIVO (`<base>.migrar.lock`) que se toma durante TODA la actualización, no solo durante cada migración.

    Sin él, un segundo proceso podía entrar entre dos migraciones del primero y verlas como «las primeras pendientes»: hacía su propia copia previa (una
    de más) y se repartían las migraciones. Con él, el segundo espera a que el primero termine y entonces ve que no queda nada. El sistema operativo lo
    suelta solo si el proceso muere. Si otro proceso lo tiene más de `espera_maxima` segundos (se colgó), se falla con un mensaje claro en vez de esperar
    para siempre. Si el archivo no se puede crear o el sistema de archivos no admite candados, se sigue SIN él y se deja un aviso en el registro: es una
    protección extra, no la base (cada migración sigue siendo atómica y exclusiva por el bloqueo de SQLite)."""
    archivo = None
    if fcntl is not None:
        try:
            archivo = open(f"{ruta}.migrar.lock", "a")
        except OSError as e:
            log.warning("No se pudo crear el candado de actualización (%s): se sigue sin él.", type(e).__name__)
    try:
        if archivo is not None:
            inicio = time.monotonic()
            while True:
                try:
                    fcntl.flock(archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:  # lo tiene otro proceso: se espera
                    if time.monotonic() - inicio >= espera_maxima:
                        raise RuntimeError(
                            f"Otro proceso lleva más de {int(espera_maxima)} s actualizando la base y no termina. Revisa que no esté colgado "
                            f"(el candado es el archivo {Path(ruta).name}.migrar.lock) y vuelve a intentar."
                        )
                    time.sleep(0.05)
                except OSError as e:  # el sistema de archivos no admite candados (p. ej. ENOLCK): se sigue sin él
                    log.warning(
                        "El sistema de archivos no admite candados (%s): se actualiza sin candado de archivo.",
                        type(e).__name__,
                    )
                    break
        yield
    finally:
        if archivo is not None:
            archivo.close()  # cerrar el archivo suelta el candado (también si el proceso muere)


def aplicar(
    ruta: Path,
    abrir: Callable[[], sqlite3.Connection],
    migraciones: Sequence[Migracion],
    hay_datos: Callable[[sqlite3.Connection], bool],
    ahora_iso: Callable[[], str],
) -> List[int]:
    """Aplica en orden las migraciones que falten, con el candado de toda la actualización (ver `candado_de_actualizacion`)."""
    validar(migraciones)
    with candado_de_actualizacion(Path(ruta)):
        return _aplicar(ruta, abrir, migraciones, hay_datos, ahora_iso)


def _aplicar(
    ruta: Path,
    abrir: Callable[[], sqlite3.Connection],
    migraciones: Sequence[Migracion],
    hay_datos: Callable[[sqlite3.Connection], bool],
    ahora_iso: Callable[[], str],
) -> List[int]:
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
            raise BaseMasNueva(
                f"Esta base tiene migraciones que este programa no conoce ({desconocidas}): la creó una versión más nueva. "
                "No se abre para no dañarla; actualiza el programa o restaura una copia anterior."
            )
        aplicadas_al_inicio = {r[0] for r in con.execute("SELECT id FROM migraciones")}
        # La copia se hace ANTES DE LA PRIMERA que estaba pendiente al empezar esta llamada, no antes de cada una: si otro proceso aplicó la 1 y este llega
        # para la 2, la copia de la actualización ya la hizo ese otro (una copia por actualización, aunque arranquen varios procesos a la vez).
        primera_pendiente = next((m.id for m in migraciones if m.id not in aplicadas_al_inicio), None)
        for m in migraciones:
            con.execute("BEGIN IMMEDIATE")  # una sola escritura a la vez, también entre procesos
            try:
                ya = {
                    r[0] for r in con.execute("SELECT id FROM migraciones")
                }  # se relee DENTRO del bloqueo: otro proceso pudo aplicarla
                if m.id in ya:
                    con.execute("COMMIT")
                    continue
                if copia is None and m.id == primera_pendiente and hay_datos(con):
                    copia = copia_previa(ruta, m.id)
                m.aplicar(con)
                con.execute(
                    "INSERT INTO migraciones (id, nombre, aplicada) VALUES (?,?,?)", (m.id, m.nombre, ahora_iso())
                )
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


def aplicar_pg(base: Any, migraciones: Sequence[Migracion], ahora_iso: Callable[[], str]) -> List[int]:
    """Lo mismo que `aplicar`, para PostgreSQL: el candado es de PostgreSQL (vale entre procesos y entre servidores), cada migración es UNA transacción
    (el DDL de PostgreSQL también se deshace) y se relee lo aplicado DENTRO del bloqueo. No se guarda copia previa: antes de actualizar una base con datos,
    haz una con `pg_dump` (ver el README)."""
    from .repositorios.motor_pg import CLAVE_ESCRITURA, CLAVE_MIGRACIONES

    validar(migraciones)
    con = base.abrir()
    hechas: List[int] = []
    try:
        con.execute("SET lock_timeout = '600s'")  # la espera máxima del candado, como en SQLite
        con.execute("SELECT pg_advisory_lock(?)", (CLAVE_MIGRACIONES,))
        try:
            con.execute(TABLA)
            conocidas = {m.id for m in migraciones}
            desconocidas = sorted(r[0] for r in con.execute("SELECT id FROM migraciones") if r[0] not in conocidas)
            if desconocidas:
                raise BaseMasNueva(
                    f"Esta base tiene migraciones que este programa no conoce ({desconocidas}): la creó una versión más nueva. "
                    "No se abre para no dañarla; actualiza el programa o restaura una copia anterior."
                )
            for m in migraciones:
                con.execute("BEGIN")
                try:
                    con.execute("SELECT pg_advisory_xact_lock(?)", (CLAVE_ESCRITURA,))
                    if m.id in {r[0] for r in con.execute("SELECT id FROM migraciones")}:  # releído DENTRO del bloqueo
                        con.execute("COMMIT")
                        continue
                    m.aplicar(con)
                    con.execute(
                        "INSERT INTO migraciones (id, nombre, aplicada) VALUES (?,?,?)", (m.id, m.nombre, ahora_iso())
                    )
                    con.execute("COMMIT")
                    hechas.append(m.id)
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
        finally:
            con.execute("SELECT pg_advisory_unlock(?)", (CLAVE_MIGRACIONES,))
    finally:
        con.close()
    return hechas
