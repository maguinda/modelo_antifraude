"""Quién soy con esta clave, qué modelo se usa y si el servicio está vivo."""
from fastapi import APIRouter, Depends

from .. import esquemas as es
from ..ajustes import Ajustes
from ..almacen import Principal
from ..dependencias import exigir, obtener_ajustes, obtener_servicio, principal_actual
from ..metadatos import RESPUESTAS_ERROR, VERSION_API
from ..servicio import ServicioEvaluacion


router = APIRouter()


@router.get("/v1/yo", tags=["Estado"], response_model=es.RespuestaYo, summary="¿Quién soy con esta clave?", operation_id="quien_soy",
            description="Devuelve el rol de la clave y, si es de un analista o administrador, el nombre de su dueño. Sirve para que las pantallas digan «Hola, Ana» y para confirmar que usas TU clave.",
            responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 429)})
def quien_soy(p: Principal = Depends(exigir("comercio", "analista", "admin"))):
    return {"rol": p.rol, "usuario": p.usuario or None, "comercio": p.nombre}


@router.get("/v1/modelo", tags=["Estado"], response_model=es.InfoModelo, summary="Modelo en uso", operation_id="modelo_en_uso",
            responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 429)})
def modelo_en_uso(p: Principal = Depends(principal_actual), servicio: ServicioEvaluacion = Depends(obtener_servicio)):
    return servicio.info_modelo


@router.get("/v1/salud", tags=["Estado"], response_model=es.RespuestaSalud, summary="¿El servicio está vivo?",
            operation_id="salud", description="No necesita clave. Úsalo para monitoreo.")
def salud(servicio: ServicioEvaluacion = Depends(obtener_servicio), ajustes: Ajustes = Depends(obtener_ajustes)):
    return {"estado": "ok", "version_api": VERSION_API, "modelo": servicio.info_modelo,
            "configuracion_insegura": ajustes.pepper_inseguro, "advertencias": servicio.advertencias}
