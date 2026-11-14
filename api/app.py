"""Aplicación FastAPI: arma la app y registra sus piezas.

Las rutas viven en `rutas/` (una por tema), la autenticación y los filtros en `dependencias.py`, los errores y las cabeceras de seguridad en `manejo.py`,
el contrato OpenAPI en `contrato.py` y los textos de Swagger en `metadatos.py`.
"""

import logging
import os
from typing import Dict, Optional

from fastapi import FastAPI

from . import registro_modelos
from .ajustes import Ajustes
from .almacen import Almacen
from .consultas import ConsultasPanel
from .contrato import instalar_contrato
from .documentacion import montar_estaticos, registrar_documentacion
from .manejo import registrar_manejo
from .metadatos import DESCRIPCION, ETIQUETAS, VERSION_API
from .observabilidad import ObservabilidadMiddleware, configurar_logs
from .panel import registrar_panel
from .proveedores_ip import crear_proveedor
from .rutas import comercios, estado, evaluaciones, lista_negra, metricas, modelos, panel_datos, revisiones
from .seguridad import LimitadorClavesInvalidas, LimitadorTasa
from .servicio import ServicioEvaluacion

RUTAS = (
    evaluaciones,
    revisiones,
    panel_datos,
    lista_negra,
    comercios,
    estado,
    metricas,
    modelos,
)  # en este orden aparecen en el contrato OpenAPI


def crear_app(
    ajustes: Optional[Ajustes] = None,
    artefacto: Optional[Dict] = None,
    reloj=None,
    docs: bool = True,
    proveedor_ip=None,
    artefacto_sin_estado: Optional[Dict] = None,
) -> FastAPI:
    ajustes = ajustes or Ajustes.desde_entorno()
    configurar_logs(ajustes.log_formato, os.environ.get("MODELO_IA_ENTORNO"))
    if os.environ.get("MODELO_IA_ENTORNO") == "produccion":  # en un servidor real, nada de secretos de desarrollo
        problemas = ajustes.problemas_de_produccion()
        if problemas["bloqueantes"]:
            raise RuntimeError("El servicio NO arranca en producción: " + " ".join(problemas["bloqueantes"]))
        for aviso in problemas["avisos"]:
            logging.getLogger("antifraude").warning("Producción: %s", aviso)
    almacen = Almacen(ajustes.ruta_bd, ajustes.pepper, ajustes.durabilidad)
    almacen.inicializar()
    # el modelo se carga SOLO después de verificar su huella contra el registro (ver registro_modelos.py)
    artefacto, artefacto_sin_estado, info_modelos = registro_modelos.preparar(
        almacen, ajustes, artefacto, artefacto_sin_estado
    )
    servicio = ServicioEvaluacion(
        almacen, artefacto, ajustes, reloj, proveedor_ip or crear_proveedor(ajustes.proveedor_ip), artefacto_sin_estado
    )
    consultas = ConsultasPanel(almacen, servicio)

    app = FastAPI(
        title="API antifraude para pymes",
        version=VERSION_API,
        description=DESCRIPCION,
        openapi_tags=ETIQUETAS,
        contact={"name": "Equipo antifraude"},
        docs_url=None,
        redoc_url=None,
    )
    montar_estaticos(app, ajustes.servir_pantallas)
    if docs:
        registrar_documentacion(app, ajustes.csp_modo)
        registrar_panel(app, ajustes.csp_modo, pantallas=ajustes.servir_pantallas)
    app.state.almacen, app.state.servicio, app.state.ajustes, app.state.consultas = (
        almacen,
        servicio,
        ajustes,
        consultas,
    )
    app.state.info_modelos = info_modelos  # qué modelo se verificó con qué huella (lo muestra GET /v1/panel/modelos)
    app.state.limitador = LimitadorTasa(ajustes.limite_peticiones_por_minuto)
    if ajustes.limite_claves_invalidas < 1:
        raise RuntimeError("MODELO_IA_LIMITE_INVALIDAS debe ser 1 o más.")
    app.state.limitador_invalidas = LimitadorClavesInvalidas(ajustes.limite_claves_invalidas)
    app.state.redes_proxy = ajustes.redes_proxy()  # un valor mal escrito en MODELO_IA_PROXY_CONFIABLE impide arrancar
    registrar_manejo(app, ajustes)
    # el último middleware que se agrega queda por fuera: este ve la respuesta final de todos los demás
    app.add_middleware(ObservabilidadMiddleware)
    for modulo in RUTAS:
        app.include_router(modulo.router)
    instalar_contrato(app)
    return app
