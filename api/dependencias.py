"""Piezas que comparten todas las rutas: quién llama (clave y rol), los filtros del panel y el acceso a los servicios de la aplicación."""
from typing import Optional

from fastapi import Depends, Query, Request, Security
from fastapi.security import APIKeyHeader

from .ajustes import Ajustes
from .almacen import Almacen, Principal
from .consultas import ConsultasPanel, Filtros
from .servicio import ErrorAPI, ServicioEvaluacion

esquema_clave = APIKeyHeader(name="X-API-Key", auto_error=False,
                             description="Clave de API de tu comercio. Se entrega una sola vez al crearla.")


# Los servicios viven en `app.state` (los pone `crear_app`); las rutas los piden como dependencias, así cada router se puede probar solo.
def obtener_almacen(request: Request) -> Almacen:
    return request.app.state.almacen


def obtener_servicio(request: Request) -> ServicioEvaluacion:
    return request.app.state.servicio


def obtener_consultas(request: Request) -> ConsultasPanel:
    return request.app.state.consultas


def obtener_ajustes(request: Request) -> Ajustes:
    return request.app.state.ajustes


def principal_actual(request: Request, clave: Optional[str] = Security(esquema_clave)) -> Principal:
    if not clave:
        raise ErrorAPI(401, "No autenticado", "Falta la cabecera X-API-Key.")
    p = request.app.state.almacen.autenticar(clave)
    if p is None:
        if request.app.state.almacen.comercio_suspendido_por_clave(clave):      # una clave válida de un comercio suspendido: se le dice claro (no revela nada a quien no la tiene)
            raise ErrorAPI(403, "Comercio suspendido", "Este comercio está suspendido. Contacta al administrador del servicio.")
        raise ErrorAPI(401, "No autenticado", "La clave de API no es válida.")
    permitido, espera = request.app.state.limitador.permitir(p.clave_id)
    if not permitido:
        raise ErrorAPI(429, "Demasiadas peticiones", f"Superaste el límite de {request.app.state.ajustes.limite_peticiones_por_minuto} por minuto.",
                       {"Retry-After": str(espera)})
    return p

def exigir(*roles: str):
    def dependencia(p: Principal = Depends(principal_actual)) -> Principal:
        if p.rol not in roles:
            raise ErrorAPI(403, "Sin permiso", "Tu clave no tiene permiso para esta operación.")
        return p
    return dependencia


def filtros_panel(comercio_id: Optional[int] = Query(None, ge=1, description="Solo analistas y administradores; un comercio solo ve lo suyo."),
                  desde: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Fecha del pago, AAAA-MM-DD (incluida)."),
                  hasta: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Fecha del pago, AAAA-MM-DD (incluida)."),
                  decision: Optional[str] = Query(None, description="APROBADA, RECHAZADA o EN_REVISION (la decisión inicial)."),
                  modo: Optional[str] = Query(None, description="normal o degradado."),
                  monto_min: Optional[float] = Query(None, ge=0), monto_max: Optional[float] = Query(None, ge=0),
                  id_cliente: Optional[str] = Query(None, max_length=64), id_transaccion: Optional[str] = Query(None, max_length=64),
                  id_seguimiento: Optional[str] = Query(None, max_length=16, description="Identificador de seguimiento (AF-XXXX-XXXX)."),
                  regla: Optional[str] = Query(None, max_length=64, description="Nombre exacto de una regla que se activó."),
                  ip: Optional[str] = Query(None, max_length=8, description="Código corto de IP (8 caracteres) como el que muestra el panel."),
                  dispositivo: Optional[str] = Query(None, max_length=8, description="Código corto de dispositivo (8 caracteres)."),
                  riesgo_min: Optional[float] = Query(None, ge=0, le=1, description="Probabilidad de fraude mínima (0 a 1). Los pagos sin puntaje no entran."),
                  riesgo_max: Optional[float] = Query(None, ge=0, le=1, description="Probabilidad de fraude máxima (0 a 1)."),
                  q: Optional[str] = Query(None, max_length=64, description="Búsqueda exacta por ID de seguimiento, ID del comercio o cliente."),
                  p: Principal = Depends(exigir("comercio", "analista", "admin"))) -> Filtros:
    return Filtros(p, comercio_id, desde, hasta, decision, modo, monto_min, monto_max, id_cliente, id_transaccion, id_seguimiento, regla, ip, dispositivo,
                   riesgo_min, riesgo_max, q)
