"""Aplicación FastAPI: arma la app y registra sus piezas.

Las rutas viven en `rutas/` (una por tema), la autenticación y los filtros en `dependencias.py`, los errores y las cabeceras de seguridad en `manejo.py`,
el contrato OpenAPI en `contrato.py` y los textos de Swagger en `metadatos.py`.
"""

import logging
import os
from typing import Dict, Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .. import predecir
from .ajustes import Ajustes
from .almacen import Almacen
from .consultas import ConsultasPanel
from .contrato import instalar_contrato
from .documentacion import ESTATICOS, registrar_documentacion
from .manejo import registrar_manejo
from .metadatos import DESCRIPCION, ETIQUETAS, VERSION_API
from .panel import registrar_panel
from .proveedores_ip import crear_proveedor
from .rutas import comercios, estado, evaluaciones, lista_negra, panel_datos, revisiones
from .seguridad import LimitadorTasa
from .servicio import ServicioEvaluacion

RUTAS = (
    evaluaciones,
    revisiones,
    panel_datos,
    lista_negra,
    comercios,
    estado,
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
    if os.environ.get("MODELO_IA_ENTORNO") == "produccion":  # en un servidor real, nada de secretos de desarrollo
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
    app.mount("/estaticos", StaticFiles(directory=str(ESTATICOS)), name="estaticos")
    if docs:
        registrar_documentacion(app, ajustes.csp_modo)
        registrar_panel(app, ajustes.csp_modo)
    app.state.almacen, app.state.servicio, app.state.ajustes, app.state.consultas = (
        almacen,
        servicio,
        ajustes,
        consultas,
    )
    app.state.limitador = LimitadorTasa(ajustes.limite_peticiones_por_minuto)
    registrar_manejo(app, ajustes)
    for modulo in RUTAS:
        app.include_router(modulo.router)
    instalar_contrato(app)
    return app
