"""Aplicación FastAPI: endpoints, seguridad y documentación OpenAPI/Swagger."""
import logging
import re
from typing import Dict, List, Optional

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
from .servicio import ErrorAPI, ServicioEvaluacion

log = logging.getLogger("modelo_ia.api")
VERSION_API = "1.0.0"
MEDIA_PROBLEMA = "application/problem+json"
PATRON_UUID = r"^[0-9a-fA-F\-]{36}$"

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
        "id_evaluacion": "3f1c9a52-8e0b-4d1f-9a54-0c6f6c2a7b11", "id_transaccion": "PED-100234", "decision": "APROBADA",
        "decision_final": "APROBADA", "puntaje_riesgo": 0.053, "motivos": [], "reglas_activadas": [], "modo": "normal",
        "modelo": {"nombre": "gradient_boosting", "version": "0.2.0", "conjunto_variables": "completo"}, "revision": None,
        "resultado_informado": None, "evaluada_en": "2026-11-12T18:30:00.123456", "latencia_ms": 18.4, "repetida": False}},
    "rechazada": {"summary": "Rechazada por el modelo y una regla de seguridad", "value": {
        "id_evaluacion": "7a2d41f0-1b7c-4b0e-8d2a-52e2f5b3c9aa", "id_transaccion": "PED-100235", "decision": "RECHAZADA",
        "decision_final": "RECHAZADA", "puntaje_riesgo": 0.997, "motivos": [],
        "reglas_activadas": [{"regla": "dispositivo_no_reconocido_con_ip_anonima", "accion": "BLOQUEAR",
                              "motivo": "Dispositivo no reconocido y desde una IP anónima"}], "modo": "normal",
        "modelo": {"nombre": "gradient_boosting", "version": "0.2.0", "conjunto_variables": "completo"}, "revision": None,
        "resultado_informado": None, "evaluada_en": "2026-11-12T03:12:01.004211", "latencia_ms": 21.7, "repetida": False}},
    "en_revision": {"summary": "En revisión: espera la decisión de un analista", "value": {
        "id_evaluacion": "b9e0c8d3-5a64-49c1-b0a7-1f3d2e4c5d66", "id_transaccion": "PED-100237", "decision": "EN_REVISION",
        "decision_final": None, "puntaje_riesgo": 0.41, "motivos": [], "reglas_activadas": [], "modo": "normal",
        "modelo": {"nombre": "gradient_boosting", "version": "0.2.0", "conjunto_variables": "completo"},
        "revision": {"estado": "pendiente", "decision_analista": None, "nota": None, "resuelta_en": None},
        "resultado_informado": None, "evaluada_en": "2026-11-12T18:31:00.000000", "latencia_ms": 19.9, "repetida": False}},
}


def crear_app(ajustes: Optional[Ajustes] = None, artefacto: Optional[Dict] = None, reloj=None, docs: bool = True, proveedor_ip=None,
              artefacto_sin_estado: Optional[Dict] = None) -> FastAPI:
    ajustes = ajustes or Ajustes.desde_entorno()
    almacen = Almacen(ajustes.ruta_bd, ajustes.pepper, ajustes.durabilidad)
    almacen.inicializar()
    if artefacto is None:
        artefacto = predecir.cargar_artefacto(ajustes.ruta_modelo)
        if artefacto_sin_estado is None:
            artefacto_sin_estado = predecir.cargar_artefacto_sin_estado()
    servicio = ServicioEvaluacion(almacen, artefacto, ajustes, reloj, proveedor_ip or crear_proveedor(ajustes.proveedor_ip), artefacto_sin_estado)

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
        return r

    @app.get("/v1/evaluaciones/{id_evaluacion}", tags=["Evaluaciones"], response_model=es.RespuestaEvaluacion,
             summary="Consultar una evaluación", operation_id="consultar_evaluacion",
             description="Devuelve la decisión guardada. Para pagos `EN_REVISION`, mira `decision_final`: es `null` hasta que un analista resuelve.",
             responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 429)})
    def consultar(id_evaluacion: str = Path(pattern=PATRON_UUID, description="Identificador devuelto al evaluar."),
                  p: Principal = Depends(exigir("comercio", "analista", "admin"))):
        return servicio.obtener(p, id_evaluacion)

    @app.post("/v1/evaluaciones/{id_evaluacion}/resultado", tags=["Resultados"], response_model=es.RespuestaEvaluacion,
              summary="Informar qué pasó con el pago", operation_id="informar_resultado",
              description="Avisa si el pago resultó **fraude confirmado**, **contracargo** o **legítimo**. Repetir el mismo resultado es seguro; uno distinto responde `409`.",
              responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 409, 422, 429)})
    def informar_resultado(solicitud: es.SolicitudResultado,
                           id_evaluacion: str = Path(pattern=PATRON_UUID),
                           p: Principal = Depends(exigir("comercio"))):
        return servicio.registrar_resultado(p, id_evaluacion, solicitud)

    # ------------------------------------------------------------- revisiones
    @app.get("/v1/revisiones", tags=["Revisiones"], response_model=es.ListaRevisiones, summary="Cola de revisión manual",
             operation_id="listar_revisiones", description="Pagos que esperan a un analista, del más antiguo al más reciente. Solo analistas y administradores.",
             responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 422, 429)})
    def listar_revisiones(estado: str = Query("pendiente", pattern="^(pendiente|resuelta)$"),
                          limite: int = Query(50, ge=1, le=200), desplazamiento: int = Query(0, ge=0),
                          p: Principal = Depends(exigir("analista", "admin"))):
        return servicio.listar_revisiones(estado, limite, desplazamiento)

    @app.post("/v1/revisiones/{id_evaluacion}/resolver", tags=["Revisiones"], response_model=es.RespuestaEvaluacion,
              summary="Resolver una revisión", operation_id="resolver_revision",
              description="El analista decide APROBADA o RECHAZADA. Solo se puede resolver una vez; la segunda responde `409`.",
              responses={k: v for k, v in RESPUESTAS_ERROR.items() if k in (401, 403, 404, 409, 422, 429)})
    def resolver(solicitud: es.SolicitudResolverRevision, id_evaluacion: str = Path(pattern=PATRON_UUID),
                 p: Principal = Depends(exigir("analista", "admin"))):
        return servicio.resolver_revision(p, id_evaluacion, solicitud)

    # ------------------------------------------------------------- lista negra
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
