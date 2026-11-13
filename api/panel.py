"""Pantalla de analistas (revisión manual): /panel. Usa la API (`/v1/revisiones`) con la clave del analista."""
import json
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

from .documentacion import ESTATICOS

# Sin scripts ni estilos en línea y sin terceros. Primero se observa; luego se bloquea (ver Ajustes.csp_modo).
CSP_PANEL = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
             "form-action 'none'; base-uri 'none'")


def cabecera_panel(modo: str):
    if modo == "enforce":
        return {"Content-Security-Policy": CSP_PANEL + "; frame-ancestors 'none'", "Cache-Control": "no-store"}
    return {"Content-Security-Policy-Report-Only": CSP_PANEL, "Cache-Control": "no-store"}


def registrar_panel(app: FastAPI, modo_csp: str = "report-only", clave_demo: Optional[str] = None) -> None:
    """Agrega /panel. En modo demo (127.0.0.1) entra solo, con la clave de analista de la demo."""
    cabeceras = cabecera_panel(modo_csp)
    html = (ESTATICOS / "panel" / "index.html").read_text(encoding="utf-8")
    app.add_api_route("/panel", lambda: HTMLResponse(html, headers=cabeceras), methods=["GET"], include_in_schema=False,
                      response_class=HTMLResponse)
    config = "window.PANEL_CONFIG = " + json.dumps({"claveDemo": clave_demo} if clave_demo else {}) + ";"
    app.add_api_route("/init/panel-config.js", lambda: Response(config, media_type="application/javascript", headers={"Cache-Control": "no-store"}),
                      methods=["GET"], include_in_schema=False)
