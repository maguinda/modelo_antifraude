"""Evaluar un pago, consultarlo e informar qué pasó con él."""

from fastapi import APIRouter, Body, Depends, Path, Response

from .. import esquemas as es
from .. import metricas
from ..almacen import Principal
from ..dependencias import exigir, obtener_servicio
from ..ejemplos import EJEMPLOS_SOLICITUD
from ..metadatos import EJEMPLOS_RESPUESTA, PATRON_UUID, RESPUESTAS_ERROR
from ..servicio import ServicioEvaluacion


router = APIRouter()


@router.post(
    "/v1/evaluaciones",
    tags=["Evaluaciones"],
    response_model=es.RespuestaEvaluacion,
    status_code=201,
    summary="Evaluar un pago",
    operation_id="evaluar_pago",
    description="Decide si el pago se **aprueba**, se **rechaza** o queda **en revisión**. Devuelve `201` la primera vez y "
    "`200` si repites el mismo `id_transaccion` con los mismos datos (respuesta guardada, `repetida: true`).",
    responses={
        200: {
            "model": es.RespuestaEvaluacion,
            "description": "Respuesta guardada de una solicitud repetida.",
            "content": {"application/json": {"examples": EJEMPLOS_RESPUESTA}},
        },
        201: {"description": "Evaluación nueva.", "content": {"application/json": {"examples": EJEMPLOS_RESPUESTA}}},
        **{k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 409, 413, 422, 429, 503)},
    },
)
def evaluar(
    response: Response,
    solicitud: es.SolicitudEvaluacion = Body(..., openapi_examples=EJEMPLOS_SOLICITUD),
    p: Principal = Depends(exigir("comercio")),
    servicio: ServicioEvaluacion = Depends(obtener_servicio),
):
    r = servicio.evaluar(p, solicitud)
    if not r["repetida"]:
        metricas.registrar_evaluacion(r["decision"], r["modo"])
    response.status_code = 200 if r["repetida"] else 201
    response.headers["X-Id-Seguimiento"] = r["id_seguimiento"]
    return r


@router.get(
    "/v1/evaluaciones/{id_evaluacion}",
    tags=["Evaluaciones"],
    response_model=es.RespuestaEvaluacion,
    summary="Consultar una evaluación",
    operation_id="consultar_evaluacion",
    description="Devuelve la decisión guardada. Para pagos `EN_REVISION`, mira `decision_final`: es `null` hasta que un analista resuelve.",
    responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 429)},
)
def consultar(
    response: Response,
    id_evaluacion: str = Path(
        pattern=PATRON_UUID, description="`id_evaluacion` o `id_seguimiento` (AF-XXXX-XXXX) devueltos al evaluar."
    ),
    p: Principal = Depends(exigir("comercio", "analista", "admin")),
    servicio: ServicioEvaluacion = Depends(obtener_servicio),
):
    r = servicio.obtener(p, id_evaluacion)
    response.headers["X-Id-Seguimiento"] = r["id_seguimiento"]
    return r


@router.post(
    "/v1/evaluaciones/{id_evaluacion}/resultado",
    tags=["Resultados"],
    response_model=es.RespuestaEvaluacion,
    summary="Informar qué pasó con el pago",
    operation_id="informar_resultado",
    description="Avisa si el pago resultó **fraude confirmado**, **contracargo** o **legítimo**. Repetir el mismo resultado es seguro; uno distinto responde `409`.",
    responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 409, 422, 429)},
)
def informar_resultado(
    solicitud: es.SolicitudResultado,
    id_evaluacion: str = Path(pattern=PATRON_UUID),
    p: Principal = Depends(exigir("comercio")),
    servicio: ServicioEvaluacion = Depends(obtener_servicio),
):
    return servicio.registrar_resultado(p, id_evaluacion, solicitud)
