"""Alta de comercios, su suspensión y su acuerdo de datos."""
from typing import List

from fastapi import APIRouter, Body, Depends, Path

from .. import esquemas as es
from ..almacen import Almacen, ComercioInexistente, OperacionNoPermitida, Principal
from ..dependencias import exigir, obtener_almacen, obtener_servicio
from ..metadatos import RESPUESTAS_ERROR
from ..servicio import ErrorAPI, ServicioEvaluacion


router = APIRouter()


@router.post("/v1/comercios", tags=["Administración"], response_model=es.RespuestaComercio, status_code=201,
             summary="Crear un comercio y su clave", operation_id="crear_comercio",
             description="Solo administradores. La `clave_api` se muestra **una sola vez**.",
             responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 422, 429)})
def crear_comercio(solicitud: es.SolicitudComercio, p: Principal = Depends(exigir("admin")), almacen: Almacen = Depends(obtener_almacen)):
    try:
        id_comercio = almacen.crear_comercio(solicitud.nombre, solicitud.razon_social, solicitud.nit, por=p.clave_id)
    except ValueError as e:
        raise ErrorAPI(422, "Datos inválidos", str(e)) from None
    clave = almacen.crear_clave(id_comercio, "comercio", "alta por API", p.clave_id)
    return {"id_comercio": id_comercio, "nombre": solicitud.nombre, "clave_api": clave}

def _comercio_o_error(operacion):
    """Convierte los errores del almacén en respuestas claras: 404 (no existe), 409 (no se permite) y 422 (dato inválido)."""
    try:
        return operacion()
    except ComercioInexistente:
        raise ErrorAPI(404, "No encontrado", "No existe ese comercio.") from None
    except OperacionNoPermitida as e:
        raise ErrorAPI(409, "Operación no permitida", str(e)) from None
    except ValueError as e:
        raise ErrorAPI(422, "Datos inválidos", str(e)) from None

_ERR_COMERCIOS = {k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 409, 422, 429)}


@router.get("/v1/comercios", tags=["Administración"], response_model=List[es.FilaComercio], operation_id="listar_comercios",
            summary="Comercios, su estado y su acuerdo de datos",
            description="Solo analistas y administradores. El NIT solo lo ve un administrador.",
            responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 429)})
def listar_comercios(p: Principal = Depends(exigir("analista", "admin")), servicio: ServicioEvaluacion = Depends(obtener_servicio), almacen: Almacen = Depends(obtener_almacen)):
    return almacen.listar_comercios(servicio.reloj().date(), con_nit=p.rol == "admin")


@router.post("/v1/comercios/{id_comercio}/suspender", tags=["Administración"], response_model=es.RespuestaEstadoComercio, operation_id="suspender_comercio",
             summary="Suspender un comercio",
             description="Solo administradores. Todas las claves del comercio dejan de funcionar al instante (reciben `403`); ningún pago se guarda después. "
                         "Sus revisiones pendientes siguen visibles para los analistas. Repetir la llamada es seguro. La plataforma (el equipo) no se puede suspender.",
             responses=_ERR_COMERCIOS)
def suspender_comercio(id_comercio: int = Path(ge=1), solicitud: es.SolicitudSuspension = Body(...), p: Principal = Depends(exigir("admin")), almacen: Almacen = Depends(obtener_almacen)):
    cambio = _comercio_o_error(lambda: almacen.suspender_comercio(id_comercio, solicitud.motivo, p.clave_id))
    return {"id_comercio": id_comercio, "estado": "suspendido", "cambio": cambio}


@router.post("/v1/comercios/{id_comercio}/reactivar", tags=["Administración"], response_model=es.RespuestaEstadoComercio, operation_id="reactivar_comercio",
             summary="Reactivar un comercio suspendido", description="Solo administradores. Sus claves vuelven a funcionar. Repetir la llamada es seguro.",
             responses=_ERR_COMERCIOS)
def reactivar_comercio(id_comercio: int = Path(ge=1), p: Principal = Depends(exigir("admin")), almacen: Almacen = Depends(obtener_almacen)):
    cambio = _comercio_o_error(lambda: almacen.reactivar_comercio(id_comercio, p.clave_id))
    return {"id_comercio": id_comercio, "estado": "activo", "cambio": cambio}


@router.put("/v1/comercios/{id_comercio}/acuerdo", tags=["Administración"], response_model=es.RespuestaAcuerdo, status_code=201, operation_id="registrar_acuerdo",
            summary="Registrar el acuerdo de datos de un comercio",
            description="Solo administradores. Guarda los DATOS del acuerdo (versión, fechas y cómo encontrar el archivo firmado), **no el documento**. "
                        "El historial no se edita: el último registrado es el que vale.", responses=_ERR_COMERCIOS)
def registrar_acuerdo(id_comercio: int = Path(ge=1), solicitud: es.SolicitudAcuerdo = Body(...), p: Principal = Depends(exigir("admin")), servicio: ServicioEvaluacion = Depends(obtener_servicio), almacen: Almacen = Depends(obtener_almacen)):
    hoy = servicio.reloj().date()
    i = _comercio_o_error(lambda: almacen.registrar_acuerdo(id_comercio, solicitud.version, solicitud.firmado_en, solicitud.vigente_hasta, solicitud.referencia, p.clave_id, hoy))
    return {"id_acuerdo": i, "acuerdo": almacen.historial_acuerdos(id_comercio, hoy)[0]}


@router.get("/v1/comercios/{id_comercio}/acuerdos", tags=["Administración"], response_model=List[es.AcuerdoHistorial], operation_id="historial_acuerdos",
            summary="Historial de acuerdos de un comercio", description="Solo analistas y administradores. Del más reciente (el que vale) al más viejo.",
            responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 429)})
def historial_acuerdos(id_comercio: int = Path(ge=1), p: Principal = Depends(exigir("analista", "admin")), servicio: ServicioEvaluacion = Depends(obtener_servicio), almacen: Almacen = Depends(obtener_almacen)):
    h = almacen.historial_acuerdos(id_comercio, servicio.reloj().date())
    if h is None:
        raise ErrorAPI(404, "No encontrado", "No existe ese comercio.")
    return h
