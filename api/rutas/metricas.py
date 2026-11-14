"""Métricas del servicio para un sistema de monitoreo (formato Prometheus). Solo administradores."""

from fastapi import APIRouter, Depends, Response

from .. import metricas
from ..almacen import Principal
from ..dependencias import exigir
from ..metadatos import RESPUESTAS_ERROR

router = APIRouter()


@router.get(
    "/v1/metricas",
    tags=["Estado"],
    summary="Métricas del servicio (Prometheus)",
    operation_id="metricas",
    description="Peticiones por ruta y estado, duración y pagos evaluados por decisión, en el formato de texto de Prometheus. "
    "Solo con clave de **administrador**. Las rutas salen como plantilla (`/v1/evaluaciones/{id_evaluacion}`) y no hay series por comercio.",
    responses={
        200: {"description": "Texto en formato Prometheus.", "content": {"text/plain": {"schema": {"type": "string"}}}},
        **{k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 429)},
    },
    response_class=Response,
)
def leer_metricas(p: Principal = Depends(exigir("admin"))):
    cuerpo, tipo = metricas.texto()
    return Response(content=cuerpo, media_type=tipo)
