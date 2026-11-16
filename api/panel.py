"""Pantallas de analistas: /panel (revisión manual, usa `/v1/revisiones`) y /dashboard (panel de control de solo lectura, usa `/v1/panel/*`)."""

import json
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

from . import documentacion
from .documentacion import FRONTEND

# Sin scripts ni estilos en línea y sin terceros. Primero se observa; luego se bloquea (ver Ajustes.csp_modo).
CSP_PANEL = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "form-action 'none'; base-uri 'none'"
)


def cabecera_panel(modo: str):
    if modo == "enforce":
        return {"Content-Security-Policy": CSP_PANEL + "; frame-ancestors 'none'", "Cache-Control": "no-store"}
    return {"Content-Security-Policy-Report-Only": CSP_PANEL, "Cache-Control": "no-store"}


def registrar_panel(
    app: FastAPI, modo_csp: str = "report-only", clave_demo: Optional[str] = None, pantallas: bool = True
) -> None:
    """Agrega /panel y /dashboard. En modo demo (127.0.0.1) entra solo, con la clave de analista de la demo."""
    if not pantallas:  # las entrega otro servidor web: aquí solo queda lo que es de la API
        registrar_config_panel(app, clave_demo)
        return
    cabeceras = cabecera_panel(modo_csp)
    html = (FRONTEND / "panel" / "index.html").read_text(encoding="utf-8")
    app.add_api_route(
        "/panel",
        lambda: HTMLResponse(html, headers=cabeceras),
        methods=["GET"],
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    pagina = (FRONTEND / "dashboard" / "index.html").read_text(encoding="utf-8")
    app.add_api_route(
        "/dashboard",
        lambda: HTMLResponse(pagina, headers=cabeceras),
        methods=["GET"],
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    registrar_app(app, cabeceras)
    registrar_config_panel(app, clave_demo)


def registrar_app(app: FastAPI, cabeceras: dict) -> bool:
    """/app: el panel de analistas nuevo (React). Solo se publica si está compilado (frontend/app/dist); mismas cabeceras (CSP) que /panel y /dashboard."""
    index = documentacion.DIST_APP / "index.html"
    if not index.is_file():
        return False
    pagina = index.read_text(encoding="utf-8")
    app.add_api_route(
        "/app",
        lambda: HTMLResponse(pagina, headers=cabeceras),
        methods=["GET"],
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    return True


def registrar_config_panel(app: FastAPI, clave_demo: Optional[str] = None) -> None:
    """/init/panel-config.js: lo piden /panel y /dashboard. Es de la API (en producción solo devuelve {}), así que existe aunque las pantallas
    las entregue otro servidor web (MODELO_IA_PANTALLAS=0)."""
    config = "window.PANEL_CONFIG = " + json.dumps({"claveDemo": clave_demo} if clave_demo else {}) + ";"
    app.add_api_route(
        "/init/panel-config.js",
        lambda: Response(config, media_type="application/javascript", headers={"Cache-Control": "no-store"}),
        methods=["GET"],
        include_in_schema=False,
    )
