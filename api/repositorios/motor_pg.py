"""PostgreSQL detrás de la MISMA interfaz que la conexión de SQLite (cambio 7b).

Los repositorios escriben SQL con marcadores `?` y leen las filas por nombre o por posición, como con `sqlite3.Row`. Esta capa les da lo mismo sobre
PostgreSQL: traduce `?` a `%s`, devuelve filas con `fila["col"]`, `fila[0]`, `dict(fila)` y `keys()`, y convierte los booleanos de Python a 0/1 (en la base
son INTEGER). Lo que NO se traduce aquí (porque no es un texto cambiado sino una diferencia real) está en `esquema.py` (DDL, triggers, columnas) y en
`migraciones.py` (candado y migraciones).
"""

import logging
import re
import threading
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Sequence, Tuple

log = logging.getLogger("modelo_ia.bd")

CLAVE_ESCRITURA = (
    7403001  # candado de aviso de PostgreSQL: UNA escritura a la vez en toda la base (como BEGIN IMMEDIATE en SQLite)
)
CLAVE_MIGRACIONES = 7403002  # candado de aviso de la actualización del esquema


def es_direccion_pg(destino: Any) -> bool:
    """¿`destino` es una dirección de PostgreSQL (y no la ruta de un archivo de SQLite)?"""
    return isinstance(destino, str) and destino.startswith(("postgresql://", "postgres://"))


def ocultar_clave(url: str) -> str:
    """La dirección sin la contraseña: la que va en `usuario:clave@` (aunque la clave lleve «@») y la que va como `?password=`."""
    sin_query, separador, query = url.partition("?")
    esquema, marca, resto = sin_query.partition("://")
    if marca:
        datos, arroba, destino = resto.rpartition("@")  # la ÚLTIMA arroba separa los datos de acceso del servidor
        if arroba and ":" in datos:
            resto = datos.split(":", 1)[0] + ":***@" + destino
        sin_query = esquema + marca + resto
    query = re.sub(r"(?i)((?:^|&)(?:password|passfile)=)[^&]*", r"\1***", query)
    return sin_query + separador + query


def direccion_sin_cifrar(url: str) -> bool:
    """¿La conexión puede ir sin cifrar y sale de esta máquina? Verdadero si el servidor no es local y `sslmode` falta o es disable, allow o prefer
    (ninguno de esos tres exige el cifrado)."""
    from urllib.parse import parse_qs, urlsplit

    partes = urlsplit(url)
    consulta = parse_qs(partes.query)
    host = (partes.hostname or "").lower()
    if (
        (consulta.get("host") or [""])[0].startswith("/")
        or host in ("localhost", "::1")
        or re.fullmatch(r"127(\.\d{1,3}){3}", host)
    ):
        return False  # un socket de esta máquina o la propia máquina
    return (consulta.get("sslmode") or [""])[0].lower() not in ("require", "verify-ca", "verify-full")


class FilaPg(tuple):
    """Una fila que se lee por posición, por nombre y como diccionario (igual que `sqlite3.Row`)."""

    _nombres: Tuple[str, ...]

    def __new__(cls, valores: Sequence[Any], nombres: Sequence[str]):
        fila = super().__new__(cls, valores)
        fila._nombres = tuple(nombres)
        return fila

    def __getitem__(self, clave: Any) -> Any:
        if isinstance(clave, str):
            try:
                return super().__getitem__(self._nombres.index(clave))
            except ValueError:
                raise IndexError(f"No hay una columna llamada {clave!r}.") from None
        return super().__getitem__(clave)

    def keys(self) -> List[str]:
        return list(self._nombres)


def fabrica_de_filas(cursor: Any):
    nombres = [d.name for d in (cursor.description or [])]
    return lambda valores: FilaPg(valores, nombres)


def traducir(sql: str) -> str:
    """`?` -> `%s` (fuera de los textos entre comillas) y `%` -> `%%` (para que el controlador no lo tome por un marcador)."""
    salida: List[str] = []
    dentro = False
    for c in sql:
        if c == "'":
            dentro = not dentro
            salida.append(c)
        elif c == "%":
            salida.append("%%")
        elif c == "?" and not dentro:
            salida.append("%s")
        else:
            salida.append(c)
    return "".join(salida)


def _parametros(params: Optional[Sequence[Any]]) -> tuple:
    return tuple((int(p) if isinstance(p, bool) else p) for p in (params or ()))


class ConexionPg:
    """La conexión que ven los repositorios: `execute`, `executemany`, `close` y `motor`."""

    motor = "postgresql"

    def __init__(self, conexion: Any):
        self._c = conexion

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None):
        return self._c.execute(traducir(sql), _parametros(params))

    def executemany(self, sql: str, filas: Sequence[Sequence[Any]]) -> None:
        with self._c.cursor() as cur:
            cur.executemany(traducir(sql), [_parametros(f) for f in filas])

    def close(self) -> None:
        self._c.close()


class BasePg:
    """Lo mismo que `Base` (SQLite): abrir, `transaccion()` (una escritura a la vez) y `lectura()`. `url` es `postgresql://usuario:clave@host:5432/base`."""

    motor = "postgresql"
    durabilidad = "FULL"

    def __init__(self, url: str, esquema: Optional[str] = None):
        try:
            import psycopg  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Para usar PostgreSQL instala el controlador: pip install 'psycopg[binary]'.") from e
        if esquema is not None and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", esquema):
            raise ValueError("El nombre del esquema de PostgreSQL solo lleva minúsculas, números y guion bajo.")
        self.url, self.esquema = url, esquema
        self.ruta = (
            url  # lo que en SQLite es el archivo: aquí identifica la base (sin mostrar la clave, ver `describir`)
        )
        self._turno = (
            threading.Lock()
        )  # una escritura a la vez dentro de este proceso (entre procesos manda el candado de PostgreSQL)
        self._colacion_revisada = False

    def __repr__(self) -> str:
        return f"BasePg({self.describir()!r}, esquema={self.esquema!r})"  # nunca con la contraseña

    def describir(self) -> str:
        """La dirección SIN la contraseña, para mensajes y registros."""
        return ocultar_clave(self.url)

    def abrir(self) -> ConexionPg:
        import psycopg

        try:  # las transacciones las manejamos a mano (autocommit)
            c = psycopg.connect(self.url, autocommit=True, row_factory=fabrica_de_filas, connect_timeout=10)
        except (
            psycopg.ProgrammingError
        ):  # la dirección está mal escrita: el mensaje del controlador la repite ENTERA, con la contraseña
            raise ValueError(
                "MODELO_IA_BD_URL no es una dirección válida de PostgreSQL (postgresql://usuario:clave@host:5432/base)."
            ) from None
        try:
            if self.esquema:
                c.execute(f'SET search_path TO "{self.esquema}"')
            c.execute("SET lock_timeout = '30s'")
            if not self._colacion_revisada:
                self._colacion_revisada = True
                fila = c.execute("SELECT datcollate FROM pg_database WHERE datname = current_database()").fetchone()
                col = fila[0] if fila else "C"
                if col not in ("C", "POSIX"):
                    log.warning(
                        "La base de PostgreSQL usa la colación «%s» y el orden de los textos puede diferir del de SQLite. "
                        "Créala con LC_COLLATE 'C' (ver el README).",
                        col,
                    )
        except BaseException:
            c.close()
            raise
        return ConexionPg(c)

    @contextmanager
    def transaccion(self) -> Iterator[ConexionPg]:
        """Una sola escritura a la vez en toda la base (candado de aviso tomado al empezar). Cada sentencia lee lo ya confirmado (READ COMMITTED): como
        nadie más escribe mientras se tiene el candado, releer DENTRO de la transacción da lo mismo que en SQLite."""
        with self._turno:
            con = self.abrir()
            try:
                con.execute("BEGIN")
                con.execute("SELECT pg_advisory_xact_lock(?)", (CLAVE_ESCRITURA,))
                try:
                    yield con
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
            finally:
                con.close()

    @contextmanager
    def lectura(self, instantanea: bool = False) -> Iterator[ConexionPg]:
        """Lectura que no bloquea a nadie. Con instantanea=True, todo lo leído es de un mismo momento (REPEATABLE READ)."""
        con = self.abrir()
        try:
            if instantanea:
                con.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            yield con
        finally:
            con.close()
