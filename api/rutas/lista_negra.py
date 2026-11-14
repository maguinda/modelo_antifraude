"""Clientes que cada comercio quiere rechazar siempre."""
from typing import List

from fastapi import APIRouter, Body, Depends, Path, Response

from .. import esquemas as es
from ..almacen import Principal
from ..dependencias import exigir, obtener_servicio
from ..metadatos import RESPUESTAS_ERROR
from ..servicio import ServicioEvaluacion


router = APIRouter()


@router.get("/v1/lista-negra", tags=["Lista negra"], response_model=List[es.EntradaListaNegra], summary="Ver tu lista negra",
            operation_id="ver_lista_negra", responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 429)})
def ver_lista_negra(p: Principal = Depends(exigir("comercio")), servicio: ServicioEvaluacion = Depends(obtener_servicio)):
    return servicio.listar_lista_negra(p)


@router.put("/v1/lista-negra/{id_cliente}", tags=["Lista negra"], response_model=es.EntradaListaNegra,
            summary="Agregar un cliente a tu lista negra", operation_id="agregar_lista_negra",
            description="Los pagos de ese cliente se rechazan siempre en tu comercio. Repetir la llamada es seguro.",
            responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 422, 429)})
def agregar_lista_negra(id_cliente: str = Path(pattern=es.PATRON_ID), solicitud: es.SolicitudListaNegra = Body(default_factory=es.SolicitudListaNegra),
                        p: Principal = Depends(exigir("comercio")), servicio: ServicioEvaluacion = Depends(obtener_servicio)):
    return servicio.agregar_lista_negra(p, id_cliente, solicitud.motivo)


@router.delete("/v1/lista-negra/{id_cliente}", tags=["Lista negra"], status_code=204, summary="Quitar un cliente de tu lista negra",
               operation_id="quitar_lista_negra", responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 429)})
def quitar_lista_negra(id_cliente: str = Path(pattern=es.PATRON_ID), p: Principal = Depends(exigir("comercio")), servicio: ServicioEvaluacion = Depends(obtener_servicio)):
    servicio.quitar_lista_negra(p, id_cliente)
    return Response(status_code=204)
