"""Piezas que comparten todas las rutas: quién llama (clave y rol), los filtros del panel y el acceso a los servicios de la aplicación."""

from typing import Optional

from fastapi import Depends, Header, Query, Request, Security
from fastapi.security import APIKeyHeader

from . import metricas
from .ajustes import Ajustes
from .almacen import Almacen, Principal
from .consultas import ConsultasPanel, Filtros
from .ip_cliente import ip_del_cliente
from .servicio import ErrorAPI, ServicioEvaluacion

esquema_clave = APIKeyHeader(
    name="X-API-Key", auto_error=False, description="Clave de API de tu comercio. Se entrega una sola vez al crearla."
)


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
        if request.app.state.almacen.comercio_suspendido_por_clave(
            clave
        ):  # una clave válida de un comercio suspendido: se le dice claro (no revela nada a quien no la tiene)
            raise ErrorAPI(
                403, "Comercio suspendido", "Este comercio está suspendido. Contacta al administrador del servicio."
            )
        ip = ip_del_cliente(
            request.client.host if request.client else None,
            request.headers.get("x-forwarded-for"),
            request.app.state.redes_proxy,
        )
        permitido, espera = request.app.state.limitador_invalidas.registrar(ip)
        metricas.registrar_clave_invalida(limitada=not permitido)
        if (
            not permitido
        ):  # demasiadas claves inventadas desde esa IP: se frena el ritmo (las claves válidas nunca llegan aquí)
            raise ErrorAPI(
                429,
                "Demasiados intentos",
                "Hubo demasiados intentos con claves no válidas desde tu dirección. Espera un momento.",
                {"Retry-After": str(espera)},
            )
        raise ErrorAPI(401, "No autenticado", "La clave de API no es válida.")
    permitido, espera = request.app.state.limitador.permitir(p.clave_id)
    if not permitido:
        raise ErrorAPI(
            429,
            "Demasiadas peticiones",
            f"Superaste el límite de {request.app.state.ajustes.limite_peticiones_por_minuto} por minuto.",
            {"Retry-After": str(espera)},
        )
    request.state.rol = p.rol  # solo para el registro de la petición (observabilidad.py)
    request.state.comercio_id = p.comercio_id if p.rol == "comercio" else None
    return p


def exigir(*roles: str):
    def dependencia(p: Principal = Depends(principal_actual)) -> Principal:
        if p.rol not in roles:
            raise ErrorAPI(403, "Sin permiso", "Tu clave no tiene permiso para esta operación.")
        return p

    return dependencia


def exigir_admin_con_mfa(
    request: Request,
    p: Principal = Depends(exigir("admin")),
    codigo: Optional[str] = Header(
        None,
        alias="X-TOTP",
        description="Código de 6 dígitos de tu app de autenticación. Solo se pide si el servicio exige segundo factor (MODELO_IA_EXIGIR_MFA).",
    ),
) -> Principal:
    """Las acciones de administrador que CAMBIAN algo: además de la clave, piden el código TOTP cuando el servicio lo exige. El orden importa:
    primero el rol (un comercio o analista recibe 403 sin saber nada del segundo factor) y luego el estado del segundo factor de ESA clave."""
    if not request.app.state.ajustes.exigir_mfa:
        return p
    almacen = request.app.state.almacen
    if almacen.mfa_estado(p.clave_id) != "activa":
        metricas.registrar_mfa("no_configurado")
        raise ErrorAPI(
            403,
            "Segundo factor no configurado",
            "Esta clave de administrador no tiene el segundo factor activo. Actívalo desde la consola del servidor con «admin activar-mfa».",
        )
    if not codigo:
        metricas.registrar_mfa("falta_codigo")
        raise ErrorAPI(
            401, "Falta el código de verificación", "Envía el código de tu app de autenticación en la cabecera X-TOTP."
        )
    resultado, espera = almacen.mfa_verificar(p.clave_id, codigo, request.app.state.servicio.reloj().timestamp())
    metricas.registrar_mfa(resultado)
    if resultado == "ok":
        return p
    if resultado == "bloqueada":
        raise ErrorAPI(
            429,
            "Clave bloqueada por intentos fallidos",
            "Hubo demasiados códigos incorrectos seguidos. Espera antes de volver a intentar.",
            {"Retry-After": str(espera)},
        )
    raise ErrorAPI(
        401, "Código de verificación no válido", "El código no es correcto, ya se usó o venció. Genera uno nuevo."
    )


def filtros_panel(
    comercio_id: Optional[int] = Query(
        None, ge=1, description="Solo analistas y administradores; un comercio solo ve lo suyo."
    ),
    desde: Optional[str] = Query(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Fecha del pago, AAAA-MM-DD (incluida)."
    ),
    hasta: Optional[str] = Query(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Fecha del pago, AAAA-MM-DD (incluida)."
    ),
    decision: Optional[str] = Query(None, description="APROBADA, RECHAZADA o EN_REVISION (la decisión inicial)."),
    modo: Optional[str] = Query(None, description="normal o degradado."),
    monto_min: Optional[float] = Query(None, ge=0),
    monto_max: Optional[float] = Query(None, ge=0),
    id_cliente: Optional[str] = Query(None, max_length=64),
    id_transaccion: Optional[str] = Query(None, max_length=64),
    id_seguimiento: Optional[str] = Query(
        None, max_length=16, description="Identificador de seguimiento (AF-XXXX-XXXX)."
    ),
    regla: Optional[str] = Query(None, max_length=64, description="Nombre exacto de una regla que se activó."),
    ip: Optional[str] = Query(
        None, max_length=8, description="Código corto de IP (8 caracteres) como el que muestra el panel."
    ),
    dispositivo: Optional[str] = Query(None, max_length=8, description="Código corto de dispositivo (8 caracteres)."),
    riesgo_min: Optional[float] = Query(
        None, ge=0, le=1, description="Probabilidad de fraude mínima (0 a 1). Los pagos sin puntaje no entran."
    ),
    riesgo_max: Optional[float] = Query(None, ge=0, le=1, description="Probabilidad de fraude máxima (0 a 1)."),
    q: Optional[str] = Query(
        None, max_length=64, description="Búsqueda exacta por ID de seguimiento, ID del comercio o cliente."
    ),
    p: Principal = Depends(exigir("comercio", "analista", "admin")),
) -> Filtros:
    return Filtros(
        p,
        comercio_id,
        desde,
        hasta,
        decision,
        modo,
        monto_min,
        monto_max,
        id_cliente,
        id_transaccion,
        id_seguimiento,
        regla,
        ip,
        dispositivo,
        riesgo_min,
        riesgo_max,
        q,
    )
