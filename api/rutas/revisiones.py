"""Cola de revisión manual para analistas y su resolución."""

from fastapi import APIRouter, Depends, Path, Query

from .. import esquemas as es
from ..almacen import Principal
from ..consultas import Filtros
from ..dependencias import exigir, filtros_panel, obtener_servicio
from ..metadatos import PATRON_UUID, RESPUESTAS_ERROR
from ..servicio import ServicioEvaluacion


router = APIRouter()


@router.get(
    "/v1/revisiones",
    tags=["Revisiones"],
    response_model=es.ListaRevisiones,
    summary="Cola de revisión manual",
    operation_id="listar_revisiones",
    description="Pagos que esperan a un analista (por defecto, del más antiguo al más reciente). Acepta los mismos filtros del panel de control: comercio, fechas, decisión, monto, riesgo, regla, cliente y búsqueda por ID. Solo analistas y administradores.",
    responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 422, 429)},
)
def listar_revisiones(
    estado: str = Query("pendiente", pattern="^(pendiente|resuelta)$"),
    limite: int = Query(50, ge=1, le=200),
    desplazamiento: int = Query(0, ge=0),
    orden: str = Query(
        "antiguos",
        pattern="^(antiguos|recientes|riesgo_desc|monto_desc)$",
        description="antiguos (los que más esperan primero), recientes, riesgo_desc o monto_desc.",
    ),
    f: Filtros = Depends(filtros_panel),
    p: Principal = Depends(exigir("analista", "admin")),
    servicio: ServicioEvaluacion = Depends(obtener_servicio),
):
    return servicio.listar_revisiones(estado, limite, desplazamiento, f, orden)


@router.post(
    "/v1/revisiones/{id_evaluacion}/resolver",
    tags=["Revisiones"],
    response_model=es.RespuestaEvaluacion,
    summary="Resolver una revisión",
    operation_id="resolver_revision",
    description="El analista decide APROBADA o RECHAZADA. Solo se puede resolver una vez; la segunda responde `409`.",
    responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 409, 422, 429)},
)
def resolver(
    solicitud: es.SolicitudResolverRevision,
    id_evaluacion: str = Path(pattern=PATRON_UUID),
    p: Principal = Depends(exigir("analista", "admin")),
    servicio: ServicioEvaluacion = Depends(obtener_servicio),
):
    return servicio.resolver_revision(p, id_evaluacion, solicitud)
