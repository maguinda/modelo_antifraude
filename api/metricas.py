"""Métricas del servicio en formato Prometheus (se leen en `GET /v1/metricas`, solo con clave de administrador).

Series (a propósito SIN etiqueta por comercio: la actividad de un comercio no debe poder verse en una serie compartida, y una etiqueta por
comercio o por id de pago crece sin límite):
  antifraude_http_peticiones_total{metodo,ruta,estado}   peticiones atendidas; `ruta` es la PLANTILLA (`/v1/panel/transacciones/{id}`), nunca el valor real
  antifraude_http_duracion_segundos{ruta}                 cuánto tardó cada una (histograma)
  antifraude_evaluaciones_total{decision,modo}            pagos evaluados por primera vez (un reintento idempotente no cuenta)
  antifraude_claves_invalidas_total{resultado}            claves inventadas: «rechazada» (401) o «limitada» (429 por demasiados desde una IP); sin la IP
  antifraude_mfa_total{resultado}                         segundo factor del administrador: ok, falta_codigo, codigo_invalido, bloqueada, no_configurado

Con VARIOS procesos (`--workers 2`), cada uno contaría solo lo suyo: `prometheus_client` en modo multiproceso deja los contadores en archivos de
una carpeta compartida (`PROMETHEUS_MULTIPROC_DIR`, que `servidor.py` crea sola) y al leer los suma. Esa variable debe existir ANTES de importar este
módulo en cada proceso.
"""

import os
from typing import Tuple

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest, multiprocess
from prometheus_client import CONTENT_TYPE_LATEST

METODOS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
)  # cualquier otro verbo se cuenta como OTRO (límite de series)
BUCKETS_SEGUNDOS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

REGISTRO = CollectorRegistry()
PETICIONES = Counter(
    "antifraude_http_peticiones_total", "Peticiones HTTP atendidas.", ["metodo", "ruta", "estado"], registry=REGISTRO
)
DURACION = Histogram(
    "antifraude_http_duracion_segundos",
    "Duración de cada petición HTTP.",
    ["ruta"],
    buckets=BUCKETS_SEGUNDOS,
    registry=REGISTRO,
)
EVALUACIONES = Counter(
    "antifraude_evaluaciones_total", "Pagos evaluados por primera vez.", ["decision", "modo"], registry=REGISTRO
)


CLAVES_INVALIDAS = Counter(
    "antifraude_claves_invalidas_total",
    "Intentos con una clave de API que no existe.",
    ["resultado"],
    registry=REGISTRO,
)
MFA = Counter(
    "antifraude_mfa_total", "Comprobaciones del segundo factor del administrador.", ["resultado"], registry=REGISTRO
)


def registrar_clave_invalida(limitada: bool) -> None:
    CLAVES_INVALIDAS.labels("limitada" if limitada else "rechazada").inc()


def registrar_mfa(resultado: str) -> None:
    MFA.labels(resultado).inc()


def registrar_peticion(metodo: str, ruta: str, estado: int, segundos: float) -> None:
    PETICIONES.labels(metodo if metodo in METODOS else "OTRO", ruta, str(estado)).inc()
    DURACION.labels(ruta).observe(segundos)


def registrar_evaluacion(decision: str, modo: str) -> None:
    EVALUACIONES.labels(decision, modo).inc()


def texto() -> Tuple[bytes, str]:
    """Todas las series en el formato de texto de Prometheus, y su tipo de contenido."""
    carpeta = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
        registro = CollectorRegistry()
        multiprocess.MultiProcessCollector(registro)
        return generate_latest(registro), CONTENT_TYPE_LATEST
    return generate_latest(REGISTRO), CONTENT_TYPE_LATEST
