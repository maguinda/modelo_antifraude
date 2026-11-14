"""Almacén SQLite: la fachada que el resto del programa usa para la persistencia.

El código vive en `repositorios/`, uno por tema (conexión y transacciones, seudónimos, comercios, claves, auditoría y esquema). `Almacen` solo los junta y
delega: conserva los mismos métodos, parámetros y valores por defecto de siempre (una prueba lo vigila), así que quien lo usaba no cambia.
Es una fachada de TRANSICIÓN: cuando el SQL que hoy vive en servicio.py y consultas.py pase también a repositorios, quien lo necesite los usará directo
(`almacen.comercios`, `almacen.claves`, …) y esta fachada se irá adelgazando.

SQLite sirve para desarrollo y para empezar. Para producción se cambia por PostgreSQL manteniendo esta misma interfaz.
"""
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, ContextManager, Dict, List, Optional, Sequence, Tuple

from .migraciones import Migracion
from .repositorios.auditoria import auditar
from .repositorios.base import Base
from .repositorios.claves import ClavesRepositorio, Principal, normalizar_nombre
from .repositorios.comercios import ComercioInexistente, ComercioNoAutorizado, ComerciosRepositorio, OperacionNoPermitida, estado_acuerdo
from .repositorios.comunes import PLATAFORMA, ahora_iso, id_seguimiento_libre, nuevo_id_seguimiento
from .repositorios.esquema import DDL, INDICES_PANEL, Esquema
from .repositorios.evaluaciones import EvaluacionesRepositorio
from .repositorios.consultas import ConsultasRepositorio
from .repositorios.interfaces import ConsultasRepo, EvaluacionesRepo, ListaNegraRepo, ResultadosRepo, RevisionesRepo
from .repositorios.lista_negra import ListaNegraRepositorio
from .repositorios.resultados import ResultadosRepositorio
from .repositorios.revisiones import RevisionesRepositorio
from .repositorios.seudonimos import Seudonimizador

__all__ = ["Almacen", "ComercioInexistente", "ComercioNoAutorizado", "DDL", "INDICES_PANEL", "OperacionNoPermitida", "PLATAFORMA", "Principal", "ahora_iso",
           "estado_acuerdo", "id_seguimiento_libre", "normalizar_nombre", "nuevo_id_seguimiento"]


class Almacen:
    def __init__(self, ruta: Path, pepper: str, durabilidad: str = "NORMAL"):
        """durabilidad: NORMAL (rápida; si se va la luz puede perderse lo último) o FULL (cada guardado se fuerza a disco)."""
        self.base = Base(ruta, durabilidad)              # la única dueña de la conexión y del candado de escritura: todos los repositorios la comparten
        self.ruta, self.pepper, self.durabilidad = self.base.ruta, pepper.encode("utf-8"), durabilidad
        self.seudonimos = Seudonimizador(self.pepper)
        self.comercios = ComerciosRepositorio(self.base)
        self.claves = ClavesRepositorio(self.base, self.seudonimos)
        self.esquema = Esquema(self.base, self.seudonimos)
        # lo que el servicio de evaluaciones necesita, tipado por INTERFAZ (el servicio no sabe que detrás hay SQLite)
        self.evaluaciones: EvaluacionesRepo = EvaluacionesRepositorio()
        self.revisiones: RevisionesRepo = RevisionesRepositorio()
        self.resultados: ResultadosRepo = ResultadosRepositorio()
        self.lista_negra: ListaNegraRepo = ListaNegraRepositorio()
        self.consultas: ConsultasRepo = ConsultasRepositorio()

    # ------------------------------------------------------------ conexión y transacciones
    def abrir(self) -> sqlite3.Connection:
        return self.base.abrir()

    def transaccion(self) -> ContextManager[sqlite3.Connection]:
        """BEGIN IMMEDIATE: una sola escritura a la vez; lo leído dentro no cambia hasta el COMMIT (ver repositorios/base.py)."""
        return self.base.transaccion()

    def lectura(self, instantanea: bool = False) -> ContextManager[sqlite3.Connection]:
        """Lectura que no bloquea a nadie. Con instantanea=True, todo lo leído es de un mismo momento (una foto de la base)."""
        return self.base.lectura(instantanea)

    # ------------------------------------------------------------ esquema y migraciones
    def inicializar(self, migraciones: Optional[Sequence[Migracion]] = None) -> List[int]:
        return self.esquema.inicializar(migraciones)

    def estado_migraciones(self) -> List[Dict[str, Any]]:
        return self.esquema.estado_migraciones()

    def copias_previas(self) -> List[Path]:
        return self.esquema.copias_previas()

    def _migraciones(self) -> List[Migracion]:
        return self.esquema.migraciones()

    _hay_datos = staticmethod(Esquema.hay_datos)

    # ------------------------------------------------------------ seudónimos (lo que usa el secreto)
    def huella_ip(self, ip: str) -> str:
        return self.seudonimos.huella_ip(ip)

    def codigo_ip(self, ip_huella: Optional[str]) -> Optional[str]:
        return self.seudonimos.codigo_ip(ip_huella)

    def codigo_dispositivo(self, huella: str) -> str:
        return self.seudonimos.codigo_dispositivo(huella)

    # ------------------------------------------------------------ comercios y acuerdos
    def crear_comercio(self, nombre: str, razon_social: Optional[str] = None, nit: Optional[str] = None, por: Optional[int] = None) -> int:
        return self.comercios.crear_comercio(nombre, razon_social, nit, por)

    def verificar_comercio(self, con: sqlite3.Connection, comercio_id: int, exigir_acuerdo: bool = False, hoy: Optional[date] = None) -> None:
        return self.comercios.verificar_comercio(con, comercio_id, exigir_acuerdo, hoy)

    def suspender_comercio(self, comercio_id: int, motivo: str, por: Optional[int] = None) -> bool:
        return self.comercios.suspender_comercio(comercio_id, motivo, por)

    def reactivar_comercio(self, comercio_id: int, por: Optional[int] = None) -> bool:
        return self.comercios.reactivar_comercio(comercio_id, por)

    def registrar_acuerdo(self, comercio_id: int, version: str, firmado_en: date, vigente_hasta: Optional[date], referencia: str,
                          por: Optional[int] = None, hoy: Optional[date] = None) -> int:
        return self.comercios.registrar_acuerdo(comercio_id, version, firmado_en, vigente_hasta, referencia, por, hoy)

    def historial_acuerdos(self, comercio_id: int, hoy: Optional[date] = None) -> Optional[List[Dict[str, Any]]]:
        return self.comercios.historial_acuerdos(comercio_id, hoy)

    def listar_comercios(self, hoy: Optional[date] = None, con_nit: bool = False) -> List[Dict[str, Any]]:
        return self.comercios.listar_comercios(hoy, con_nit)

    # ------------------------------------------------------------ claves y personas
    def comercio_suspendido_por_clave(self, clave: str) -> bool:
        return self.claves.comercio_suspendido_por_clave(clave)

    def crear_clave(self, comercio_id: int, rol: str, etiqueta: str = "", creada_por: Optional[int] = None) -> str:
        return self.claves.crear_clave(comercio_id, rol, etiqueta, creada_por)

    def crear_usuario(self, rol: str, nombre: str, creada_por: Optional[int] = None) -> Tuple[int, str]:
        return self.claves.crear_usuario(rol, nombre, creada_por)

    def rotar_clave(self, clave_id: int, creada_por: Optional[int] = None) -> Tuple[int, str]:
        return self.claves.rotar_clave(clave_id, creada_por)

    def listar_claves(self) -> List[Dict[str, Any]]:
        return self.claves.listar_claves()

    def autenticar(self, clave: str) -> Optional[Principal]:
        return self.claves.autenticar(clave)

    def desactivar_clave(self, clave_id: int) -> None:
        return self.claves.desactivar_clave(clave_id)

    # ------------------------------------------------------------ auditoría
    auditar = staticmethod(auditar)
