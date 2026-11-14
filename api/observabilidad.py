"""Observabilidad: identificador de cada petición, un registro (log) por petición y las métricas.

Qué NUNCA se escribe en un registro: la clave de API, el cuerpo de la petición, el query string ni la IP. De la petición solo salen método, la PLANTILLA
de la ruta (no el valor real, que puede llevar un id), el estado, la duración, el rol y, si es un comercio, su número. Además, un filtro tapa cualquier
texto con forma de clave de API (`mia_<rol>_...`) que se cuele por un mensaje de error o una traza.
"""

import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from starlette.datastructures import MutableHeaders

from . import metricas
from .contexto import id_peticion

CABECERA = "X-Request-ID"
# lo que se acepta si el cliente manda un identificador; cualquier otra cosa se reemplaza
ID_VALIDO = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
CLAVE_API = re.compile(r"mia_[A-Za-z0-9]+_[A-Za-z0-9_\-]{16,}")
FORMATOS = ("auto", "json", "texto")
log = logging.getLogger("modelo_ia.acceso")


def redactar(texto: str) -> str:
    return CLAVE_API.sub("[clave-oculta]", texto)


class FormatoJSON(logging.Formatter):
    """Una línea JSON por registro: fácil de buscar y de enviar a un servicio de logs."""

    def format(self, r: logging.LogRecord) -> str:
        d: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(r.created, timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "nivel": r.levelname,
            "logger": r.name,
            "mensaje": r.getMessage(),
        }
        rid = id_peticion.get()
        if rid:
            d["request_id"] = rid
        d.update(getattr(r, "campos", {}))
        if r.exc_info:
            d["excepcion"] = r.exc_info[0].__name__ if r.exc_info[0] else None
            d["traza"] = self.formatException(r.exc_info)
        return redactar(json.dumps(d, ensure_ascii=False, default=str))


class FormatoTexto(logging.Formatter):
    """Legible para quien desarrolla: `hora NIVEL [id] mensaje`."""

    def format(self, r: logging.LogRecord) -> str:
        rid = id_peticion.get()
        base = f"{self.formatTime(r, '%H:%M:%S')} {r.levelname:<7} [{rid[:8] if rid else '-'}] {r.getMessage()}"
        if r.exc_info:
            base += "\n" + self.formatException(r.exc_info)
        return redactar(base)


class _SalidaEstandar(logging.StreamHandler):
    """Escribe en la salida estándar de ESE momento (así no queda atada a un archivo cerrado, p. ej. el que captura pytest)."""

    def __init__(self) -> None:
        super().__init__()

    @property  # type: ignore[override]
    def stream(self):  # noqa: D102
        return sys.stdout

    @stream.setter
    def stream(self, _valor) -> None:
        pass


def configurar_logs(formato: str = "auto", entorno: Optional[str] = None, flujo: Any = None) -> logging.Handler:
    """Deja los registros del servicio en JSON (producción) o en texto legible (desarrollo). Se puede llamar muchas veces: reemplaza el anterior."""
    if formato not in FORMATOS:
        raise RuntimeError(f"MODELO_IA_LOG_FORMATO debe ser uno de {', '.join(FORMATOS)} (recibí «{formato}»).")
    json_ = formato == "json" or (formato == "auto" and entorno == "produccion")
    manejador: logging.Handler = logging.StreamHandler(flujo) if flujo is not None else _SalidaEstandar()
    manejador.setFormatter(FormatoJSON() if json_ else FormatoTexto())
    manejador._antifraude = True  # type: ignore[attr-defined]
    for nombre in ("modelo_ia", "antifraude"):
        logger = logging.getLogger(nombre)
        logger.handlers = [h for h in logger.handlers if not getattr(h, "_antifraude", False)] + [manejador]
        if logger.level == logging.NOTSET:
            logger.setLevel(logging.INFO)
    return manejador


def _plantilla(scope: dict) -> str:
    """La ruta como está declarada (`/v1/evaluaciones/{id_evaluacion}`): nunca el valor real, así las series y los logs no crecen sin límite."""
    ruta = scope.get("route")
    plantilla = getattr(ruta, "path", None)
    if plantilla:
        return str(plantilla)
    if str(scope.get("path", "")).startswith("/estaticos/"):
        return "/estaticos/{archivo}"
    return "desconocida"


class ObservabilidadMiddleware:
    """ASGI puro (sin BaseHTTPMiddleware): fija el `X-Request-ID`, mide, cuenta y escribe UN registro por petición. Va por fuera de los demás middlewares."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        entrante = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"x-request-id"), "")
        rid = (
            entrante if ID_VALIDO.fullmatch(entrante) else str(uuid.uuid4())
        )  # fullmatch: «$» aceptaría un \n al final
        id_peticion.set(rid)
        inicio = time.perf_counter()
        estado = 500

        async def enviar(mensaje) -> None:
            nonlocal estado
            if mensaje["type"] == "http.response.start":
                estado = mensaje["status"]
                cabeceras = MutableHeaders(scope=mensaje)
                if CABECERA not in cabeceras:
                    cabeceras[CABECERA] = rid
            await send(mensaje)

        try:
            await self.app(scope, receive, enviar)
        finally:
            segundos = time.perf_counter() - inicio
            ruta = _plantilla(scope)
            metodo = scope.get("method", "")
            metricas.registrar_peticion(metodo, ruta, estado, segundos)
            nivel = logging.DEBUG if ruta == "/v1/salud" else (logging.WARNING if estado >= 500 else logging.INFO)
            if log.isEnabledFor(nivel):
                usuario = scope.get("state") or {}
                campos = {
                    "evento": "peticion",
                    "metodo": metodo,
                    "ruta": ruta,
                    "estado": estado,
                    "duracion_ms": round(segundos * 1000, 1),
                }
                if usuario.get("rol"):
                    campos["rol"] = usuario["rol"]
                if usuario.get("comercio_id") is not None:
                    campos["comercio_id"] = usuario["comercio_id"]
                log.log(nivel, f"{metodo} {ruta} {estado} {segundos * 1000:.1f} ms", extra={"campos": campos})
