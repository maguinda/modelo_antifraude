"""Pasa los datos de una base de SQLite a una base NUEVA de PostgreSQL, con verificación (cambio 7c).

Qué hace, en este orden:
  1. Abre el archivo de SQLite SOLO PARA LEER (no lo modifica) y comprueba que está al día con las migraciones de este programa.
  2. Comprueba que el destino (PostgreSQL) esté VACÍO (solo puede tener el comercio PLATAFORMA que crean las migraciones) y que las tablas tengan las mismas columnas.
  3. Copia todas las tablas en UNA sola transacción, en el orden de sus claves foráneas, conservando los números (id) y dejando las secuencias después del último.
  4. Antes de confirmar, compara tabla por tabla el número de filas y una huella SHA-256 del contenido (en el mismo orden). Si algo no coincide, se deshace TODO.

Lo que NO hace: no cambia la base de SQLite, no mezcla con datos que ya haya en PostgreSQL y no sirve para volver de PostgreSQL a SQLite.
Importante: el secreto (MODELO_IA_PEPPER) del destino debe ser EL MISMO que usaba SQLite, o las claves guardadas dejarán de reconocerse.
"""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence, Tuple

# (tabla, columnas de su clave en el orden en que se compara, ¿tiene columna id de identidad?) en el ORDEN de sus claves foráneas
TABLAS: Tuple[Tuple[str, Tuple[str, ...], bool], ...] = (
    ("comercios", ("id",), True),
    ("acuerdos", ("id",), True),
    ("claves", ("id",), True),
    ("evaluaciones", ("id",), False),
    ("evaluacion_reglas", ("id_evaluacion", "posicion"), False),
    ("revisiones", ("id_evaluacion",), False),
    ("resultados", ("id_evaluacion",), False),
    ("lista_negra", ("comercio_id", "id_cliente"), False),
    ("auditoria", ("id",), True),
    ("modelos_registro", ("id",), True),
)
LOTE = 2000
PLATAFORMA = 1


class MigracionDeDatosError(Exception):
    """Algo impide pasar los datos (origen o destino no sirven) o la verificación no coincidió. Los mensajes dicen qué hacer."""


def _filas(cur: Any, tabla: str, columnas: Sequence[str], orden: Sequence[str]) -> Iterator[Tuple[Any, ...]]:
    cur = cur.execute(f"SELECT {', '.join(columnas)} FROM {tabla} ORDER BY {', '.join(orden)}")
    while True:
        lote = cur.fetchmany(LOTE)
        if not lote:
            return
        for fila in lote:
            yield tuple(fila)


def _valor(v: Any) -> Any:
    return float(v) if isinstance(v, float) else v


def huella(filas: Iterator[Tuple[Any, ...]]) -> Tuple[int, str]:
    """(cuántas filas, SHA-256 de su contenido) con una representación que no depende del motor."""
    h, n = hashlib.sha256(), 0
    for f in filas:
        h.update(
            json.dumps([_valor(v) for v in f], ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        )
        h.update(b"\n")
        n += 1
    return n, h.hexdigest()


def _columnas_sqlite(con: sqlite3.Connection, tabla: str) -> List[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({tabla})")]


def _columnas_pg(con: Any, tabla: str) -> List[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ? ORDER BY ordinal_position",
            (tabla,),
        )
    ]


def comprobar_origen(origen: sqlite3.Connection, ids_destino: Sequence[int]) -> None:
    try:
        aplicadas = sorted(r[0] for r in origen.execute("SELECT id FROM migraciones"))
    except sqlite3.Error:
        raise MigracionDeDatosError(
            "El archivo de SQLite no parece una base de este programa (no tiene la tabla de migraciones)."
        ) from None
    if aplicadas != sorted(ids_destino):
        raise MigracionDeDatosError(
            f"La base de SQLite tiene las migraciones {aplicadas} y este programa espera {sorted(ids_destino)}. "
            "Ponla al día primero con «admin migrar» (con MODELO_IA_BD apuntando a ese archivo) y repite."
        )


def comprobar_destino(destino: Any) -> None:
    for tabla, _, _ in TABLAS:
        n = destino.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
        permitido = 1 if tabla == "comercios" else 0  # el comercio PLATAFORMA lo crean las migraciones
        if n > permitido:
            raise MigracionDeDatosError(
                f"El destino de PostgreSQL ya tiene datos (tabla {tabla}: {n} filas). Esta herramienta solo copia a una base NUEVA: no mezcla ni sobrescribe."
            )
    if (
        destino.execute("SELECT nombre FROM comercios WHERE id = ?", (PLATAFORMA,)).fetchone() is None
        and destino.execute("SELECT COUNT(*) FROM comercios").fetchone()[0]
    ):
        raise MigracionDeDatosError(
            "El destino tiene comercios pero no el comercio PLATAFORMA: no es una base nueva de este programa."
        )


def copiar(ruta_sqlite: Path, almacen_destino: Any) -> Dict[str, Dict[str, Any]]:
    """Copia `ruta_sqlite` (solo lectura) a `almacen_destino` (PostgreSQL, ya migrado y vacío) y verifica. Devuelve, por tabla, filas y huella."""
    if getattr(almacen_destino.base, "motor", "sqlite") != "postgresql":
        raise MigracionDeDatosError("El destino debe ser PostgreSQL (define MODELO_IA_BD_URL).")
    ruta = Path(ruta_sqlite)
    if not ruta.is_file():
        raise MigracionDeDatosError(f"No existe el archivo de SQLite: {ruta}")
    origen = sqlite3.connect(
        f"{ruta.resolve().as_uri()}?mode=ro", uri=True
    )  # solo lectura: es imposible modificarlo desde aquí
    try:
        almacen_destino.inicializar()
        ids_destino = [m["id"] for m in almacen_destino.estado_migraciones()]
        comprobar_origen(origen, ids_destino)
        resumen: Dict[str, Dict[str, Any]] = {}
        with almacen_destino.transaccion() as destino:
            comprobar_destino(destino)
            for tabla, clave, identidad in TABLAS:
                cols_s, cols_p = _columnas_sqlite(origen, tabla), _columnas_pg(destino, tabla)
                if set(cols_s) != set(cols_p):
                    raise MigracionDeDatosError(
                        f"La tabla {tabla} no tiene las mismas columnas en los dos motores (SQLite: {sorted(set(cols_s) - set(cols_p))}, "
                        f"PostgreSQL: {sorted(set(cols_p) - set(cols_s))}). ¿Las dos bases están en la misma versión?"
                    )
                columnas = cols_p  # el orden de PostgreSQL
                marcas = ", ".join("?" for _ in columnas)
                if tabla == "comercios":  # PLATAFORMA ya existe en el destino: se reemplaza por la del origen
                    actualizar = ", ".join(f"{c} = excluded.{c}" for c in columnas if c != "id")
                    sql = f"INSERT INTO {tabla} ({', '.join(columnas)}) VALUES ({marcas}) ON CONFLICT (id) DO UPDATE SET {actualizar}"
                else:
                    sql = f"INSERT INTO {tabla} ({', '.join(columnas)}) VALUES ({marcas})"
                lote: List[Tuple[Any, ...]] = []
                for fila in _filas(origen, tabla, columnas, clave):
                    lote.append(fila)
                    if len(lote) >= LOTE:
                        destino.executemany(sql, lote)
                        lote = []
                if lote:
                    destino.executemany(sql, lote)
                if (
                    identidad
                ):  # las secuencias siguen DESPUÉS del último id copiado, o el próximo registro nuevo chocaría
                    destino.execute(
                        f"SELECT setval(pg_get_serial_sequence('{tabla}', 'id'), (SELECT GREATEST(COALESCE(MAX(id), 1), 1) FROM {tabla}))"
                    )
                n_o, h_o = huella(_filas(origen, tabla, columnas, clave))
                n_d, h_d = huella(_filas(destino, tabla, columnas, clave))
                if (n_o, h_o) != (n_d, h_d):
                    raise MigracionDeDatosError(
                        f"La verificación de {tabla} NO coincide (origen {n_o} filas, destino {n_d}). No se guardó nada en PostgreSQL."
                    )
                resumen[tabla] = {"filas": n_d, "huella": h_d[:16]}
        return resumen
    finally:
        origen.close()
