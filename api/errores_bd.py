"""Los errores de base de datos de cada motor, juntos: el código que atrapa «ya existe» o «la base no está disponible» no debe saber cuál motor hay detrás."""

import sqlite3
from typing import Tuple, Type

try:  # el controlador de PostgreSQL es opcional (solo hace falta si se usa PostgreSQL)
    import psycopg

    _PG_INTEGRIDAD: Tuple[Type[BaseException], ...] = (psycopg.errors.IntegrityError,)
    _PG_OPERACION: Tuple[Type[BaseException], ...] = (psycopg.errors.OperationalError,)
    _PG_CUALQUIERA: Tuple[Type[BaseException], ...] = (psycopg.Error,)
except ImportError:  # pragma: no cover
    _PG_INTEGRIDAD = _PG_OPERACION = _PG_CUALQUIERA = ()

INTEGRIDAD: Tuple[Type[BaseException], ...] = (
    sqlite3.IntegrityError,
) + _PG_INTEGRIDAD  # una restricción (única, clave foránea…) se violó
# la base no está disponible, está bloqueada o se cortó la conexión
OPERACION: Tuple[Type[BaseException], ...] = (sqlite3.OperationalError,) + _PG_OPERACION
CUALQUIERA: Tuple[Type[BaseException], ...] = (sqlite3.Error,) + _PG_CUALQUIERA
