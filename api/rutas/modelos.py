"""Modelos registrados y deriva del modelo en uso: datos de solo lectura para el personal."""

from typing import Any, Dict, List, Union

from fastapi import APIRouter, Depends, Query, Request

from ..almacen import Principal
from ..consultas import ConsultasPanel
from ..dependencias import exigir, obtener_ajustes, obtener_consultas
from ..ajustes import Ajustes
from ..metadatos import RESPUESTAS_ERROR

router = APIRouter()
_errores: Dict[Union[int, str], Dict[str, Any]] = {
    k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 422, 429)
}


@router.get(
    "/v1/panel/deriva",
    tags=["Panel de control"],
    summary="Deriva del modelo en uso (solo personal)",
    operation_id="deriva_del_modelo",
    description="Compara la distribución de los **puntajes** de los pagos recientes con la de la validación del modelo (PSI en 10 cubetas): menos de 0,10 estable, "
    "de 0,10 a 0,25 atención, más de 0,25 alerta. Con menos pagos que el mínimo (`MODELO_IA_DERIVA_MIN_MUESTRA`, 500) no se da estado: el PSI sería ruido. "
    "Mide si los puntajes cambiaron, **no si el modelo acierta** (eso exige las etiquetas, que llegan tarde). Solo analistas y administradores.",
    response_model=Dict[str, Any],
    responses=_errores,
)
def deriva_del_modelo(
    request: Request,
    ventana_dias: int = Query(7, ge=1, le=90, description="Cuántos días hacia atrás se miran."),
    p: Principal = Depends(exigir("analista", "admin")),
    consultas: ConsultasPanel = Depends(obtener_consultas),
    ajustes: Ajustes = Depends(obtener_ajustes),
):
    verificado = bool((request.app.state.info_modelos.get("principal") or {}).get("verificado"))
    return consultas.deriva(ventana_dias, ajustes.deriva_min_muestra, verificado)


@router.get(
    "/v1/panel/modelos",
    tags=["Panel de control"],
    summary="Modelos registrados (solo personal)",
    operation_id="modelos_registrados",
    description="El registro de modelos: huella (12 primeros caracteres de su SHA-256), estado (candidato, en uso, archivado), cuándo se promovió y cuál se está ejecutando "
    "con la huella verificada. Se administra desde la consola del servidor (`admin modelo-registrar`, `modelo-promover`, `modelo-revertir`). Solo analistas y administradores.",
    response_model=List[Dict[str, Any]],
    responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 429)},
)
def modelos_registrados(
    request: Request,
    p: Principal = Depends(exigir("analista", "admin")),
    consultas: ConsultasPanel = Depends(obtener_consultas),
):
    return consultas.modelos(request.app.state.info_modelos)
