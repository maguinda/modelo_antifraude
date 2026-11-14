"""Panel de control: datos de solo lectura para las pantallas."""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Path, Query

from ..almacen import Principal
from ..consultas import ConsultasPanel, Filtros, ORDENES
from ..dependencias import exigir, filtros_panel, obtener_consultas
from ..metadatos import PATRON_UUID, RESPUESTAS_ERROR


router = APIRouter()


_err_panel = {k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 422, 429)}


@router.get("/v1/panel/transacciones", tags=["Panel de control"], summary="Pagos con filtros (solo lectura)", operation_id="panel_transacciones",
            description="Lista paginada de pagos evaluados. Filtra por comercio, fechas, decisión, modo, monto, cliente, regla, IP o dispositivo. "
                        "La IP y la huella del dispositivo se muestran como **códigos cortos**, nunca en claro. Analistas y administradores ven todos los comercios; "
                        "un comercio, solo los suyos.", response_model=Dict[str, Any], responses=_err_panel)
def panel_transacciones(f: Filtros = Depends(filtros_panel), orden: str = Query("fecha_desc", pattern="^(" + "|".join(ORDENES) + ")$"),
                        limite: int = Query(50, ge=1, le=200), desplazamiento: int = Query(0, ge=0, le=100000), consultas: ConsultasPanel = Depends(obtener_consultas)):
    return consultas.transacciones(f, orden, limite, desplazamiento)


@router.get("/v1/panel/transacciones/{id_pago}", tags=["Panel de control"], summary="Todo sobre un pago: cómo se decidió", operation_id="panel_detalle",
            description="Detalle completo: identificadores (el del comercio y el de seguimiento), datos recibidos y cuáles NO se mandaron, puntaje y umbrales, "
                        "reglas activadas y su efecto, revisión, resultado posterior, auditoría y pagos relacionados (misma IP, mismo dispositivo, mismo cliente). "
                        "Acepta `id_seguimiento` (AF-XXXX-XXXX) o `id_evaluacion`.", response_model=Dict[str, Any], responses=_err_panel)
def panel_detalle(id_pago: str = Path(pattern=PATRON_UUID), p: Principal = Depends(exigir("comercio", "analista", "admin")), consultas: ConsultasPanel = Depends(obtener_consultas)):
    return consultas.detalle(p, id_pago)


@router.get("/v1/panel/resumen", tags=["Panel de control"], summary="Resumen: totales, por día, por comercio, reglas y calidad", operation_id="panel_resumen",
            description="Indicadores del periodo y los filtros pedidos. `por_comercio` solo trae varios comercios para analistas y administradores.",
            response_model=Dict[str, Any], responses=_err_panel)
def panel_resumen(f: Filtros = Depends(filtros_panel), consultas: ConsultasPanel = Depends(obtener_consultas)):
    return consultas.resumen(f)


@router.get("/v1/panel/vinculos", tags=["Panel de control"], summary="Patrones entre pagos: misma IP, mismo dispositivo, ráfagas", operation_id="panel_vinculos",
            description="Varios clientes desde una misma IP, varios clientes con un mismo dispositivo, un cliente con varias IPs y ráfagas de pagos desde una IP. "
                        "`minimo` es cuántos clientes, IPs o pagos hacen falta para listarlo. Cada fila trae un `filtro` para ver esos pagos.",
            response_model=Dict[str, Any], responses=_err_panel)
def panel_vinculos(f: Filtros = Depends(filtros_panel), minimo: int = Query(3, ge=2, le=1000), consultas: ConsultasPanel = Depends(obtener_consultas)):
    return consultas.vinculos(f, minimo)


@router.get("/v1/panel/comercios", tags=["Panel de control"], summary="Comercios (para el filtro del panel)", operation_id="panel_comercios",
            description="Solo analistas y administradores.", response_model=List[Dict[str, Any]],
            responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 429)})
def panel_comercios(p: Principal = Depends(exigir("analista", "admin")), consultas: ConsultasPanel = Depends(obtener_consultas)):
    return consultas.comercios()
