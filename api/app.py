"""Aplicación FastAPI: endpoints, seguridad y documentación OpenAPI/Swagger."""
import logging
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, Path, Query, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import predecir
from . import esquemas as es
from .documentacion import ESTATICOS, registrar_documentacion
from .panel import registrar_panel
from .proveedores_ip import crear_proveedor
from .ejemplos import EJEMPLOS_SOLICITUD
from .ajustes import Ajustes
from .almacen import PLATAFORMA, Almacen, Principal
from .seguridad import LimitadorTasa
from .consultas import ConsultasPanel, Filtros, ORDENES
from .servicio import ErrorAPI, ServicioEvaluacion

log = logging.getLogger("modelo_ia.api")
VERSION_API = "1.0.0"
MEDIA_PROBLEMA = "application/problem+json"
PATRON_UUID = r"^(?:[0-9a-fA-F\-]{36}|[Aa][Ff]-[0-9A-Za-z]{4}-[0-9A-Za-z]{4})$"        # el identificador interno o el de seguimiento (AF-XXXX-XXXX)

DESCRIPCION = """
API para decidir, **antes de aprobar un pago**, si una transacción se **aprueba**, se **rechaza** o queda **en revisión**.

## Cómo se usa
1. Tu equipo recibe una **clave de API** (una por comercio) y la envía en la cabecera `X-API-Key` en cada llamada.
2. Por cada pago, llamas a `POST /v1/evaluaciones` con los datos del pago.
3. La respuesta trae la `decision`:
   * **APROBADA**: procesa el pago.
   * **RECHAZADA**: no lo proceses.
   * **EN_REVISION**: retén el pago. Un analista lo resolverá; consulta `GET /v1/evaluaciones/{id}` hasta que `decision_final` deje de ser `null` (será APROBADA o RECHAZADA).
4. Cuando sepas qué pasó de verdad con el pago (fraude confirmado, contracargo o legítimo), avísalo con `POST /v1/evaluaciones/{id}/resultado`: así el modelo mejora.

## Cómo probar desde esta página (Swagger)
1. Pulsa **Authorize** y pega tu clave de API (si ya viene autorizada, no hace falta).
2. Abre `POST /v1/evaluaciones` → **Try it out**.
3. En el menú **Examples** elige un pago de ejemplo (aprobado, en revisión, rechazado, viaje imposible, modo degradado...) y pulsa **Execute**.
4. Para evaluar otro pago parecido, **cambia `id_transaccion`**: repetir el mismo id devuelve la respuesta guardada (`repetida: true`).

## Reglas importantes
* **Nunca envíes datos de tarjeta** (número, vencimiento, CVV). La API los rechaza y no los guarda.
* **Estado del dispositivo (opcional):** `dispositivo.estado` lo sabe el sistema de cuentas del comercio (¿este cliente ya usó este equipo?). Si no lo envías, la API usa un modelo entrenado sin ese dato (algo menos preciso: en las pruebas con datos sintéticos, más falsas alarmas) y la respuesta lo indica en `estado_origen`.
* **IP del comprador:** envía `red.ip` (la que ve tu servidor). La API la ubica y la evalúa (país, tipo de conexión, reputación) y **no la guarda, no la devuelve ni la registra**. Lo demás de `red` es opcional y, si lo envías, **manda sobre** lo que diga el proveedor. La respuesta indica de dónde salieron los datos de red (`red_origen`).
* **Reintentos seguros:** repetir una llamada con el mismo `id_transaccion` y los mismos datos devuelve la misma respuesta, sin contarla dos veces. Con datos distintos responde `409`.
* **Si la API no responde o devuelve 5xx**, trata el pago como **EN_REVISION** en tu sistema.
* **Modo degradado:** si el modelo no se puede usar (faltan datos o hay una falla), la respuesta trae `modo: degradado` y el pago queda en revisión, salvo que una regla de seguridad lo rechace. Nunca se aprueba a ciegas.
* Los errores usan el formato estándar `application/problem+json`.

> Los modelos actuales se entrenaron con **datos sintéticos**. No son una promesa de desempeño sobre fraude real.
"""

ETIQUETAS = [
    {"name": "Evaluaciones", "description": "Decidir sobre un pago y consultar la decisión."},
    {"name": "Resultados", "description": "Informar qué pasó finalmente con un pago (mejora continua)."},
    {"name": "Revisiones", "description": "Cola de revisión manual para analistas."},
    {"name": "Lista negra", "description": "Clientes que tu comercio quiere rechazar siempre."},
    {"name": "Administración", "description": "Alta de comercios y claves (solo administradores)."},
    {"name": "Estado", "description": "Salud del servicio y modelo en uso."},
]

RESPUESTAS_ERROR = {
    401: {"model": es.Problema, "description": "Falta la clave de API o no es válida."},
    403: {"model": es.Problema, "description": "La clave no tiene permiso para esta operación."},
    404: {"model": es.Problema, "description": "No existe (o no pertenece a tu comercio)."},
    409: {"model": es.Problema, "description": "Conflicto: el recurso ya existe con datos distintos o ya fue resuelto."},
    413: {"model": es.Problema, "description": "La petición es demasiado grande."},
    422: {"model": es.Problema, "description": "Datos inválidos (incluye el envío de datos de tarjeta)."},
    429: {"model": es.Problema, "description": "Demasiadas peticiones. Respeta la cabecera `Retry-After`."},
    503: {"model": es.Problema, "description": "Servicio no disponible: trata el pago como EN_REVISION y reintenta."},
}


def _problema(status: int, titulo: str, detalle: str, instancia: str, errores=None, cabeceras=None) -> JSONResponse:
    cuerpo = {"type": "about:blank", "title": titulo, "status": status, "detail": detalle, "instance": instancia}
    if errores:
        cuerpo["errores"] = errores
    return JSONResponse(cuerpo, status_code=status, media_type=MEDIA_PROBLEMA, headers=cabeceras or {})


EJEMPLOS_RESPUESTA = {
    "aprobada": {"summary": "Aprobada", "value": {
        "id_evaluacion": "3f1c9a52-8e0b-4d1f-9a54-0c6f6c2a7b11", "id_seguimiento": "AF-7K3Q-9M2X", "id_transaccion": "PED-100234", "decision": "APROBADA",
        "decision_final": "APROBADA", "puntaje_riesgo": 0.053, "motivos": [], "reglas_activadas": [], "modo": "normal",
        "modelo": {"nombre": "gradient_boosting", "version": "0.2.0", "conjunto_variables": "completo"}, "revision": None,
        "resultado_informado": None, "evaluada_en": "2026-11-12T18:30:00.123456", "latencia_ms": 18.4, "repetida": False}},
    "rechazada": {"summary": "Rechazada por el modelo y una regla de seguridad", "value": {
        "id_evaluacion": "7a2d41f0-1b7c-4b0e-8d2a-52e2f5b3c9aa", "id_seguimiento": "AF-4H8D-2WZN", "id_transaccion": "PED-100235", "decision": "RECHAZADA",
        "decision_final": "RECHAZADA", "puntaje_riesgo": 0.997, "motivos": [],
        "reglas_activadas": [{"regla": "dispositivo_no_reconocido_con_ip_anonima", "accion": "BLOQUEAR",
                              "motivo": "Dispositivo no reconocido y desde una IP anónima"}], "modo": "normal",
        "modelo": {"nombre": "gradient_boosting", "version": "0.2.0", "conjunto_variables": "completo"}, "revision": None,
        "resultado_informado": None, "evaluada_en": "2026-11-12T03:12:01.004211", "latencia_ms": 21.7, "repetida": False}},
    "en_revision": {"summary": "En revisión: espera la decisión de un analista", "value": {
        "id_evaluacion": "b9e0c8d3-5a64-49c1-b0a7-1f3d2e4c5d66", "id_seguimiento": "AF-B6TC-0R5J", "id_transaccion": "PED-100237", "decision": "EN_REVISION",
        "decision_final": None, "puntaje_riesgo": 0.41, "motivos": [], "reglas_activadas": [], "modo": "normal",
        "modelo": {"nombre": "gradient_boosting", "version": "0.2.0", "conjunto_variables": "completo"},
        "revision": {"estado": "pendiente", "decision_analista": None, "nota": None, "resuelta_en": None},
        "resultado_informado": None, "evaluada_en": "2026-11-12T18:31:00.000000", "latencia_ms": 19.9, "repetida": False}},
}


def crear_app(ajustes: Optional[Ajustes] = None, artefacto: Optional[Dict] = None, reloj=None, docs: bool = True, proveedor_ip=None,
              artefacto_sin_estado: Optional[Dict] = None) -> FastAPI:
    ajustes = ajustes or Ajustes.desde_entorno()
    if os.environ.get("MODELO_IA_ENTORNO") == "produccion":                      # en un servidor real, nada de secretos de desarrollo
        problemas = ajustes.problemas_de_produccion()
        if problemas["bloqueantes"]:
            raise RuntimeError("El servicio NO arranca en producción: " + " ".join(problemas["bloqueantes"]))
        for aviso in problemas["avisos"]:
            logging.getLogger("antifraude").warning("Producción: %s", aviso)
    almacen = Almacen(ajustes.ruta_bd, ajustes.pepper, ajustes.durabilidad)
    almacen.inicializar()
    if artefacto is None:
        artefacto = predecir.cargar_artefacto(ajustes.ruta_modelo)
        if artefacto_sin_estado is None:
            artefacto_sin_estado = predecir.cargar_artefacto_sin_estado()
    servicio = ServicioEvaluacion(almacen, artefacto, ajustes, reloj, proveedor_ip or crear_proveedor(ajustes.proveedor_ip), artefacto_sin_estado)
    consultas = ConsultasPanel(almacen, servicio)

    app = FastAPI(title="API antifraude para pymes", version=VERSION_API, description=DESCRIPCION, openapi_tags=ETIQUETAS,
                  contact={"name": "Equipo antifraude"}, docs_url=None, redoc_url=None)
    app.mount("/estaticos", StaticFiles(directory=str(ESTATICOS)), name="estaticos")
    if docs:
        registrar_documentacion(app, ajustes.csp_modo)
        registrar_panel(app, ajustes.csp_modo)
    app.state.almacen, app.state.servicio, app.state.ajustes = almacen, servicio, ajustes
    app.state.limitador = LimitadorTasa(ajustes.limite_peticiones_por_minuto)

    esquema_clave = APIKeyHeader(name="X-API-Key", auto_error=False,
                                 description="Clave de API de tu comercio. Se entrega una sola vez al crearla.")

    def principal_actual(request: Request, clave: Optional[str] = Security(esquema_clave)) -> Principal:
        if not clave:
            raise ErrorAPI(401, "No autenticado", "Falta la cabecera X-API-Key.")
        p = almacen.autenticar(clave)
        if p is None:
            raise ErrorAPI(401, "No autenticado", "La clave de API no es válida.")
        permitido, espera = app.state.limitador.permitir(p.clave_id)
        if not permitido:
            raise ErrorAPI(429, "Demasiadas peticiones", f"Superaste el límite de {ajustes.limite_peticiones_por_minuto} por minuto.",
                           {"Retry-After": str(espera)})
        return p

    def exigir(*roles: str):
        def dependencia(p: Principal = Depends(principal_actual)) -> Principal:
            if p.rol not in roles:
                raise ErrorAPI(403, "Sin permiso", "Tu clave no tiene permiso para esta operación.")
            return p
        return dependencia

    # ------------------------------------------------------------- errores y límites
    @app.exception_handler(ErrorAPI)
    async def _error_api(request: Request, e: ErrorAPI):
        return _problema(e.status, e.titulo, e.detalle, request.url.path, cabeceras=e.cabeceras)

    @app.exception_handler(RequestValidationError)
    async def _validacion(request: Request, e: RequestValidationError):
        errores = []
        for err in e.errors():                                   # sin repetir lo enviado: podría traer datos sensibles
            campo = ".".join(str(x) for x in err.get("loc", []) if x != "body")
            errores.append({"campo": campo or "cuerpo", "mensaje": re.sub(r"^Value error, ", "", str(err.get("msg", "inválido")))})
        return _problema(422, "Datos inválidos", "La petición no cumple el contrato. Revisa los campos indicados.",
                         request.url.path, errores)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, e: StarletteHTTPException):
        titulos = {404: "No encontrado", 405: "Método no permitido"}
        return _problema(e.status_code, titulos.get(e.status_code, "Error"), str(e.detail), request.url.path)

    @app.exception_handler(Exception)
    async def _inesperado(request: Request, e: Exception):
        log.exception("error inesperado")
        return _problema(500, "Error interno", "Ocurrió un error inesperado. Trata el pago como EN_REVISION y reintenta.", request.url.path)

    @app.middleware("http")
    async def _tamano_maximo(request: Request, siguiente):
        largo = request.headers.get("content-length")
        if largo is not None and largo.isdigit() and int(largo) > ajustes.tamano_maximo_bytes:
            return _problema(413, "Petición demasiado grande", f"El máximo es {ajustes.tamano_maximo_bytes} bytes.", request.url.path)
        if largo is None and request.method in ("POST", "PUT", "PATCH"):
            if len(await request.body()) > ajustes.tamano_maximo_bytes:
                return _problema(413, "Petición demasiado grande", f"El máximo es {ajustes.tamano_maximo_bytes} bytes.", request.url.path)
        return await siguiente(request)

    @app.middleware("http")
    async def _cabeceras_de_seguridad(request: Request, siguiente):
        """Se agregan a TODAS las respuestas (también a los errores 401, 413...). HSTS lo pone el proxy HTTPS."""
        r = await siguiente(request)
        r.headers["X-Content-Type-Options"] = "nosniff"
        r.headers["Referrer-Policy"] = "no-referrer"
        r.headers["X-Frame-Options"] = "DENY"
        if ajustes.hsts_segundos > 0 and request.url.scheme == "https":
            r.headers["Strict-Transport-Security"] = f"max-age={ajustes.hsts_segundos}"
        if request.url.path.startswith("/v1/"):          # decisiones sobre pagos: nunca en caché; es JSON, no se ejecuta nada
            r.headers["Cache-Control"] = "no-store"
            r.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return r

    # ------------------------------------------------------------- evaluaciones
    @app.post("/v1/evaluaciones", tags=["Evaluaciones"], response_model=es.RespuestaEvaluacion, status_code=201,
              summary="Evaluar un pago", operation_id="evaluar_pago",
              description="Decide si el pago se **aprueba**, se **rechaza** o queda **en revisión**. Devuelve `201` la primera vez y "
                          "`200` si repites el mismo `id_transaccion` con los mismos datos (respuesta guardada, `repetida: true`).",
              responses={200: {"model": es.RespuestaEvaluacion, "description": "Respuesta guardada de una solicitud repetida.",
                               "content": {"application/json": {"examples": EJEMPLOS_RESPUESTA}}},
                         201: {"description": "Evaluación nueva.", "content": {"application/json": {"examples": EJEMPLOS_RESPUESTA}}},
                         **{k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 409, 413, 422, 429, 503)}})
    def evaluar(response: Response,
                solicitud: es.SolicitudEvaluacion = Body(..., openapi_examples=EJEMPLOS_SOLICITUD),
                p: Principal = Depends(exigir("comercio"))):
        r = servicio.evaluar(p, solicitud)
        response.status_code = 200 if r["repetida"] else 201
        response.headers["X-Id-Seguimiento"] = r["id_seguimiento"]
        return r

    @app.get("/v1/evaluaciones/{id_evaluacion}", tags=["Evaluaciones"], response_model=es.RespuestaEvaluacion,
             summary="Consultar una evaluación", operation_id="consultar_evaluacion",
             description="Devuelve la decisión guardada. Para pagos `EN_REVISION`, mira `decision_final`: es `null` hasta que un analista resuelve.",
             responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 429)})
    def consultar(response: Response, id_evaluacion: str = Path(pattern=PATRON_UUID, description="`id_evaluacion` o `id_seguimiento` (AF-XXXX-XXXX) devueltos al evaluar."),
                  p: Principal = Depends(exigir("comercio", "analista", "admin"))):
        r = servicio.obtener(p, id_evaluacion)
        response.headers["X-Id-Seguimiento"] = r["id_seguimiento"]
        return r

    @app.post("/v1/evaluaciones/{id_evaluacion}/resultado", tags=["Resultados"], response_model=es.RespuestaEvaluacion,
              summary="Informar qué pasó con el pago", operation_id="informar_resultado",
              description="Avisa si el pago resultó **fraude confirmado**, **contracargo** o **legítimo**. Repetir el mismo resultado es seguro; uno distinto responde `409`.",
              responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 409, 422, 429)})
    def informar_resultado(solicitud: es.SolicitudResultado,
                           id_evaluacion: str = Path(pattern=PATRON_UUID),
                           p: Principal = Depends(exigir("comercio"))):
        return servicio.registrar_resultado(p, id_evaluacion, solicitud)

    # ------------------------------------------------------------- filtros comunes (panel de control y cola de revisión)
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

    # ------------------------------------------------------------- revisiones
    @app.get("/v1/revisiones", tags=["Revisiones"], response_model=es.ListaRevisiones, summary="Cola de revisión manual",
             operation_id="listar_revisiones", description="Pagos que esperan a un analista (por defecto, del más antiguo al más reciente). Acepta los mismos filtros del panel de control: comercio, fechas, decisión, monto, riesgo, regla, cliente y búsqueda por ID. Solo analistas y administradores.",
             responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 422, 429)})
    def listar_revisiones(estado: str = Query("pendiente", pattern="^(pendiente|resuelta)$"),
                          limite: int = Query(50, ge=1, le=200), desplazamiento: int = Query(0, ge=0),
                          orden: str = Query("antiguos", pattern="^(antiguos|recientes|riesgo_desc|monto_desc)$", description="antiguos (los que más esperan primero), recientes, riesgo_desc o monto_desc."),
                          f: Filtros = Depends(filtros_panel), p: Principal = Depends(exigir("analista", "admin"))):
        return servicio.listar_revisiones(estado, limite, desplazamiento, f.condiciones, f.parametros, orden)

    @app.post("/v1/revisiones/{id_evaluacion}/resolver", tags=["Revisiones"], response_model=es.RespuestaEvaluacion,
              summary="Resolver una revisión", operation_id="resolver_revision",
              description="El analista decide APROBADA o RECHAZADA. Solo se puede resolver una vez; la segunda responde `409`.",
              responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 409, 422, 429)})
    def resolver(solicitud: es.SolicitudResolverRevision, id_evaluacion: str = Path(pattern=PATRON_UUID),
                 p: Principal = Depends(exigir("analista", "admin"))):
        return servicio.resolver_revision(p, id_evaluacion, solicitud)

    # ------------------------------------------------------------- lista negra
    # ------------------------------------------------------------- panel de control (solo lectura)
    _err_panel = {k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 422, 429)}

    @app.get("/v1/panel/transacciones", tags=["Panel de control"], summary="Pagos con filtros (solo lectura)", operation_id="panel_transacciones",
             description="Lista paginada de pagos evaluados. Filtra por comercio, fechas, decisión, modo, monto, cliente, regla, IP o dispositivo. "
                         "La IP y la huella del dispositivo se muestran como **códigos cortos**, nunca en claro. Analistas y administradores ven todos los comercios; "
                         "un comercio, solo los suyos.", response_model=Dict[str, Any], responses=_err_panel)
    def panel_transacciones(f: Filtros = Depends(filtros_panel), orden: str = Query("fecha_desc", pattern="^(" + "|".join(ORDENES) + ")$"),
                            limite: int = Query(50, ge=1, le=200), desplazamiento: int = Query(0, ge=0, le=100000)):
        return consultas.transacciones(f, orden, limite, desplazamiento)

    @app.get("/v1/panel/transacciones/{id_pago}", tags=["Panel de control"], summary="Todo sobre un pago: cómo se decidió", operation_id="panel_detalle",
             description="Detalle completo: identificadores (el del comercio y el de seguimiento), datos recibidos y cuáles NO se mandaron, puntaje y umbrales, "
                         "reglas activadas y su efecto, revisión, resultado posterior, auditoría y pagos relacionados (misma IP, mismo dispositivo, mismo cliente). "
                         "Acepta `id_seguimiento` (AF-XXXX-XXXX) o `id_evaluacion`.", response_model=Dict[str, Any], responses=_err_panel)
    def panel_detalle(id_pago: str = Path(pattern=PATRON_UUID), p: Principal = Depends(exigir("comercio", "analista", "admin"))):
        return consultas.detalle(p, id_pago)

    @app.get("/v1/panel/resumen", tags=["Panel de control"], summary="Resumen: totales, por día, por comercio, reglas y calidad", operation_id="panel_resumen",
             description="Indicadores del periodo y los filtros pedidos. `por_comercio` solo trae varios comercios para analistas y administradores.",
             response_model=Dict[str, Any], responses=_err_panel)
    def panel_resumen(f: Filtros = Depends(filtros_panel)):
        return consultas.resumen(f)

    @app.get("/v1/panel/vinculos", tags=["Panel de control"], summary="Patrones entre pagos: misma IP, mismo dispositivo, ráfagas", operation_id="panel_vinculos",
             description="Varios clientes desde una misma IP, varios clientes con un mismo dispositivo, un cliente con varias IPs y ráfagas de pagos desde una IP. "
                         "`minimo` es cuántos clientes, IPs o pagos hacen falta para listarlo. Cada fila trae un `filtro` para ver esos pagos.",
             response_model=Dict[str, Any], responses=_err_panel)
    def panel_vinculos(f: Filtros = Depends(filtros_panel), minimo: int = Query(3, ge=2, le=1000)):
        return consultas.vinculos(f, minimo)

    @app.get("/v1/panel/comercios", tags=["Panel de control"], summary="Comercios (para el filtro del panel)", operation_id="panel_comercios",
             description="Solo analistas y administradores.", response_model=List[Dict[str, Any]],
             responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 429)})
    def panel_comercios(p: Principal = Depends(exigir("analista", "admin"))):
        return consultas.comercios()

    @app.get("/v1/lista-negra", tags=["Lista negra"], response_model=List[es.EntradaListaNegra], summary="Ver tu lista negra",
             operation_id="ver_lista_negra", responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 429)})
    def ver_lista_negra(p: Principal = Depends(exigir("comercio"))):
        return servicio.listar_lista_negra(p)

    @app.put("/v1/lista-negra/{id_cliente}", tags=["Lista negra"], response_model=es.EntradaListaNegra,
             summary="Agregar un cliente a tu lista negra", operation_id="agregar_lista_negra",
             description="Los pagos de ese cliente se rechazan siempre en tu comercio. Repetir la llamada es seguro.",
             responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 422, 429)})
    def agregar_lista_negra(id_cliente: str = Path(pattern=es.PATRON_ID), solicitud: es.SolicitudListaNegra = Body(default_factory=es.SolicitudListaNegra),
                            p: Principal = Depends(exigir("comercio"))):
        return servicio.agregar_lista_negra(p, id_cliente, solicitud.motivo)

    @app.delete("/v1/lista-negra/{id_cliente}", tags=["Lista negra"], status_code=204, summary="Quitar un cliente de tu lista negra",
                operation_id="quitar_lista_negra", responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 429)})
    def quitar_lista_negra(id_cliente: str = Path(pattern=es.PATRON_ID), p: Principal = Depends(exigir("comercio"))):
        servicio.quitar_lista_negra(p, id_cliente)
        return Response(status_code=204)

    # ------------------------------------------------------------- administración y estado
    @app.post("/v1/comercios", tags=["Administración"], response_model=es.RespuestaComercio, status_code=201,
              summary="Crear un comercio y su clave", operation_id="crear_comercio",
              description="Solo administradores. La `clave_api` se muestra **una sola vez**.",
              responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 422, 429)})
    def crear_comercio(solicitud: es.SolicitudComercio, p: Principal = Depends(exigir("admin"))):
        id_comercio = almacen.crear_comercio(solicitud.nombre)
        clave = almacen.crear_clave(id_comercio, "comercio", "alta por API")
        return {"id_comercio": id_comercio, "nombre": solicitud.nombre, "clave_api": clave}

    @app.get("/v1/yo", tags=["Estado"], response_model=es.RespuestaYo, summary="¿Quién soy con esta clave?", operation_id="quien_soy",
             description="Devuelve el rol de la clave y, si es de un analista o administrador, el nombre de su dueño. Sirve para que las pantallas digan «Hola, Ana» y para confirmar que usas TU clave.",
             responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 429)})
    def quien_soy(p: Principal = Depends(exigir("comercio", "analista", "admin"))):
        return {"rol": p.rol, "usuario": p.usuario or None, "comercio": p.nombre}

    @app.get("/v1/modelo", tags=["Estado"], response_model=es.InfoModelo, summary="Modelo en uso", operation_id="modelo_en_uso",
             responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 429)})
    def modelo_en_uso(p: Principal = Depends(principal_actual)):
        return servicio.info_modelo

    @app.get("/v1/salud", tags=["Estado"], response_model=es.RespuestaSalud, summary="¿El servicio está vivo?",
             operation_id="salud", description="No necesita clave. Úsalo para monitoreo.")
    def salud():
        return {"estado": "ok", "version_api": VERSION_API, "modelo": servicio.info_modelo,
                "configuracion_insegura": ajustes.pepper_inseguro, "advertencias": servicio.advertencias}

    def contrato() -> Dict:
        """OpenAPI con los errores documentados tal como se devuelven: application/problem+json."""
        if app.openapi_schema:
            return app.openapi_schema
        esquema = get_openapi(title=app.title, version=app.version, openapi_version=app.openapi_version, description=app.description,
                              routes=app.routes, tags=app.openapi_tags, contact=app.contact)
        problema = {"$ref": "#/components/schemas/Problema"}
        for ruta in esquema["paths"].values():
            for operacion in ruta.values():
                respuestas = operacion.get("responses", {})
                if "422" in respuestas:
                    respuestas["422"] = {"description": RESPUESTAS_ERROR[422]["description"],
                                         "content": {MEDIA_PROBLEMA: {"schema": problema}}}
                for codigo, r in respuestas.items():
                    if codigo.isdigit() and int(codigo) >= 400 and "application/json" in r.get("content", {}):
                        r["content"] = {MEDIA_PROBLEMA: r["content"]["application/json"]}
        for sobra in ("HTTPValidationError", "ValidationError"):
            esquema.get("components", {}).get("schemas", {}).pop(sobra, None)
        app.openapi_schema = esquema
        return esquema

    app.openapi = contrato
    return app
