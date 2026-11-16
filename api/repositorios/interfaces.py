"""Lo que el servicio de evaluaciones necesita de la persistencia, como INTERFACES (typing.Protocol).

Las implementaciones con SQLite están en los demás archivos de esta carpeta. Tener la interfaz aparte permite dos cosas: probar el servicio con repositorios
FALSOS (sin base de datos, y simulando carreras de forma determinista) y cambiar de base de datos escribiendo otra implementación sin tocar el servicio.

Cada método recibe la conexión (`con`) de quien llama: las transacciones y el orden de los bloqueos son del servicio. Python no exige estas interfaces al ejecutar:
una prueba (tests/test_interfaces_repositorios.py) comprueba que las implementaciones las cumplen.
"""

from typing import Any, Dict, List, Optional, Protocol, Tuple

ORDENES_PANEL = (
    "fecha_desc",
    "fecha_asc",
    "monto_desc",
    "riesgo_desc",
)  # órdenes que acepta el listado del panel (nombres, no SQL)
ORDENES_REVISIONES = ("antiguos", "recientes", "riesgo_desc", "monto_desc")  # órdenes que acepta la cola de revisión


class EvaluacionesRepo(Protocol):
    def por_transaccion(self, con: Any, comercio_id: int, id_transaccion: str) -> Optional[Any]: ...
    def por_id(self, con: Any, id_evaluacion: str) -> Optional[Any]: ...
    def por_identificador(self, con: Any, identificador: str) -> Optional[Any]: ...
    def seguimiento_existe(self, con: Any, candidato: str) -> bool: ...
    def marcas(self, con: Any, comercio_id: int, id_cliente: str, huella: Optional[str]) -> Tuple[int, int]: ...
    def previos_cliente(self, con: Any, comercio_id: int, id_cliente: str, fecha: str, limite: int) -> List[Dict]: ...
    def previos_huella(
        self, con: Any, comercio_id: int, huella: str, id_cliente: str, fecha: str, limite: int
    ) -> List[Dict]: ...
    def insertar(
        self,
        con: Any,
        *,
        id: str,
        comercio_id: int,
        id_transaccion: str,
        id_cliente: str,
        fecha: str,
        huella: Optional[str],
        fila_json: str,
        solicitud_hash: str,
        decision: str,
        decision_modelo: Optional[str],
        probabilidad: Optional[float],
        motivos_json: str,
        reglas_json: str,
        modelo_nombre: str,
        modelo_version: str,
        modelo_conjunto: str,
        modo: str,
        latencia_ms: float,
        creada: str,
        red_origen: str,
        estado_origen: str,
        id_seguimiento: str,
        ip_codigo: Optional[str],
        disp_codigo: Optional[str],
        monto_cop: Any,
        no_enviados_json: str,
        senales_json: Optional[str],
    ) -> None: ...


class RevisionesRepo(Protocol):
    def por_evaluacion(self, con: Any, id_evaluacion: str) -> Optional[Any]: ...
    def existe(self, con: Any, id_evaluacion: str) -> bool: ...
    def crear_pendiente(self, con: Any, id_evaluacion: str, comercio_id: int, creada: str) -> None: ...
    def resolver(
        self, con: Any, id_evaluacion: str, decision: str, nota: Optional[str], clave_analista: int, resuelta: str
    ) -> int: ...
    def listar(
        self, con: Any, estado: str, filtros: Optional[Any], orden: str, limite: int, desplazamiento: int
    ) -> Tuple[Any, List[Any]]: ...


class ResultadosRepo(Protocol):
    def por_evaluacion(self, con: Any, id_evaluacion: str) -> Optional[Any]: ...
    def registrar(
        self,
        con: Any,
        id_evaluacion: str,
        comercio_id: int,
        resultado: str,
        fecha_confirmacion: str,
        nota: Optional[str],
        recibido: str,
    ) -> None: ...


class ListaNegraRepo(Protocol):
    def ids_de_cliente(self, con: Any, comercio_id: int, id_cliente: str) -> List[str]: ...
    def agregar(self, con: Any, comercio_id: int, id_cliente: str, motivo: Optional[str], creada: str) -> None: ...
    def quitar(self, con: Any, comercio_id: int, id_cliente: str) -> int: ...
    def listar(self, con: Any, comercio_id: int) -> List[Any]: ...


class ModelosRegistroRepo(Protocol):
    """El registro de modelos: huellas, estados (candidato, en uso, archivado) y la referencia de puntajes para medir la deriva."""

    def registrar(
        self,
        archivo: str,
        sha256: str,
        nombre: str,
        version: str,
        conjunto: str,
        rol: str,
        referencia: Optional[Dict[str, Any]] = None,
    ) -> int: ...
    def listar(self) -> List[Dict[str, Any]]: ...
    def por_id(self, id_: int) -> Optional[Dict[str, Any]]: ...
    def en_uso(self, rol: str) -> Optional[Dict[str, Any]]: ...
    def promover(self, id_: int) -> Dict[str, Any]: ...
    def revertir(self, rol: str) -> Dict[str, Any]: ...
    def guardar_referencia(self, id_: int, referencia: Dict[str, Any]) -> None: ...


class ConsultasRepo(Protocol):
    """Lecturas del panel. Reciben un objeto de filtros ya validado (`consultas.Filtros`) y devuelven filas crudas; no dan forma a la respuesta."""

    def listar(self, con: Any, f: Any, orden: str, limite: int, desplazamiento: int) -> Tuple[int, List[Any]]: ...
    def revision_de(self, con: Any, id_evaluacion: str) -> Optional[Any]: ...
    def resultado_de(self, con: Any, id_evaluacion: str) -> Optional[Any]: ...
    def nombre_comercio(self, con: Any, comercio_id: int) -> str: ...
    def auditoria_de(self, con: Any, id_evaluacion: str) -> List[Any]: ...
    def pagos_con_codigo(
        self, con: Any, columna: str, valor: str, id_evaluacion: str, alcance_comercio: Optional[int]
    ) -> Tuple[int, List[Any]]: ...
    def otros_pagos_del_cliente(self, con: Any, comercio_id: int, id_cliente: str, id_evaluacion: str) -> int: ...
    def resumen(self, con: Any, f: Any, con_analistas: bool) -> Dict[str, Any]: ...
    def vinculos(
        self, con: Any, f: Any, minimo: int, limite: int = 50, desplazamiento: int = 0
    ) -> Dict[str, List[Any]]: ...
    def comercios_con_pagos(self, con: Any) -> List[Any]: ...
    def distribucion_puntaje(self, con: Any, nombre: str, version: str, conjunto: str, desde_iso: str) -> List[int]: ...
