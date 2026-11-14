"""Métricas del servicio para un sistema de monitoreo (formato Prometheus). Solo administradores."""

import logging

from fastapi import APIRouter, Depends, Response

from .. import metricas
from ..ajustes import Ajustes
from ..almacen import Principal
from ..consultas import ConsultasPanel
from ..dependencias import exigir, obtener_ajustes, obtener_consultas
from ..metadatos import RESPUESTAS_ERROR

router = APIRouter()
log = logging.getLogger("modelo_ia.api")


@router.get(
    "/v1/metricas",
    tags=["Estado"],
    summary="Métricas del servicio (Prometheus)",
    operation_id="metricas",
    description="Peticiones por ruta y estado, duración y pagos evaluados por decisión, en el formato de texto de Prometheus. "
    "Más `antifraude_deriva_psi` y `antifraude_deriva_pagos` (deriva de los puntajes de los últimos 7 días). "
    "Solo con clave de **administrador**. Las rutas salen como plantilla (`/v1/evaluaciones/{id_evaluacion}`) y no hay series por comercio.",
    responses={
        200: {"description": "Texto en formato Prometheus.", "content": {"text/plain": {"schema": {"type": "string"}}}},
        **{k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 429)},
    },
    response_class=Response,
)
def leer_metricas(
    p: Principal = Depends(exigir("admin")),
    consultas: ConsultasPanel = Depends(obtener_consultas),
    ajustes: Ajustes = Depends(obtener_ajustes),
):
    cuerpo, tipo = metricas.texto()
    try:  # la deriva sale de la base; si algo falla ahí, las demás métricas se entregan igual
        cuerpo += consultas.lineas_deriva_prometheus(7, ajustes.deriva_min_muestra).encode("utf-8")
    except Exception:
        log.warning("No se pudieron calcular las métricas de deriva.", exc_info=True)
    return Response(content=cuerpo, media_type=tipo)
