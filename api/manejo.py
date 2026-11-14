"""Errores con el formato estándar (application/problem+json), límite de tamaño y cabeceras de seguridad de TODAS las respuestas."""
import logging
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .ajustes import Ajustes
from .metadatos import problema
from .servicio import ErrorAPI

log = logging.getLogger("modelo_ia.api")


def registrar_manejo(app: FastAPI, ajustes: Ajustes) -> None:
    @app.exception_handler(ErrorAPI)
    async def _error_api(request: Request, e: ErrorAPI):
        return problema(e.status, e.titulo, e.detalle, request.url.path, cabeceras=e.cabeceras)

    @app.exception_handler(RequestValidationError)
    async def _validacion(request: Request, e: RequestValidationError):
        errores = []
        for err in e.errors():                                   # sin repetir lo enviado: podría traer datos sensibles
            campo = ".".join(str(x) for x in err.get("loc", []) if x != "body")
            errores.append({"campo": campo or "cuerpo", "mensaje": re.sub(r"^Value error, ", "", str(err.get("msg", "inválido")))})
        return problema(422, "Datos inválidos", "La petición no cumple el contrato. Revisa los campos indicados.",
                         request.url.path, errores)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, e: StarletteHTTPException):
        titulos = {404: "No encontrado", 405: "Método no permitido"}
        return problema(e.status_code, titulos.get(e.status_code, "Error"), str(e.detail), request.url.path)

    @app.exception_handler(Exception)
    async def _inesperado(request: Request, e: Exception):
        log.exception("error inesperado")
        return problema(500, "Error interno", "Ocurrió un error inesperado. Trata el pago como EN_REVISION y reintenta.", request.url.path)

    @app.middleware("http")
    async def _tamano_maximo(request: Request, siguiente):
        largo = request.headers.get("content-length")
        if largo is not None and largo.isdigit() and int(largo) > ajustes.tamano_maximo_bytes:
            return problema(413, "Petición demasiado grande", f"El máximo es {ajustes.tamano_maximo_bytes} bytes.", request.url.path)
        if largo is None and request.method in ("POST", "PUT", "PATCH"):
            if len(await request.body()) > ajustes.tamano_maximo_bytes:
                return problema(413, "Petición demasiado grande", f"El máximo es {ajustes.tamano_maximo_bytes} bytes.", request.url.path)
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
