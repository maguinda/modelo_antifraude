"""Páginas de documentación (Swagger y ReDoc) servidas desde este mismo servidor, sin CDN.

- Los archivos de terceros están en `estaticos/` (con su huella en MANIFIESTO.txt).
- El código de arranque de Swagger va en un archivo aparte (`/init/<nombre>.js`), así la política de contenido
  puede prohibir scripts en línea.
- En modo demo, cada página trae su clave puesta (solo en 127.0.0.1; ver `demo.py`).
"""
import json
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

ESTATICOS = Path(__file__).resolve().parent / "estaticos"
NOMBRE_ESQUEMA = "APIKeyHeader"          # nombre con el que FastAPI registra la cabecera X-API-Key
PARAMETROS_SWAGGER = {"persistAuthorization": True, "displayRequestDuration": True, "tryItOutEnabled": True,
                      "defaultModelsExpandDepth": -1, "docExpansion": "list", "filter": True}

CSP_DOCS = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; form-action 'none'; "
            "base-uri 'none'")
CSP_ANTI_MARCOS = "; frame-ancestors 'none'"       # solo vale al bloquear; observando, el navegador lo rechaza


def cabecera_csp(modo: str) -> Dict[str, str]:
    """Primero se observa (report-only: no bloquea nada); cuando no hay avisos en la consola, se pasa a 'enforce'."""
    if modo == "enforce":
        return {"Content-Security-Policy": CSP_DOCS + CSP_ANTI_MARCOS}
    return {"Content-Security-Policy-Report-Only": CSP_DOCS}


def _html_swagger(titulo: str, init: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{titulo}</title><link rel="icon" href="/estaticos/favicon.png">
<link rel="stylesheet" href="/estaticos/swagger-ui.css"><link rel="stylesheet" href="/estaticos/swagger-movil.css"></head>
<body><div id="swagger-ui"></div>
<script src="/estaticos/swagger-ui-bundle.js"></script><script src="/init/{init}.js"></script></body></html>"""


def _js_inicio(clave: Optional[str]) -> str:
    gancho = (f'onComplete: function () {{ window.ui.preauthorizeApiKey("{NOMBRE_ESQUEMA}", {json.dumps(clave)}); }},'
              if clave else "")
    return ("window.ui = SwaggerUIBundle(Object.assign(" + json.dumps(PARAMETROS_SWAGGER) +
            ', {url: "/openapi.json", dom_id: "#swagger-ui", layout: "BaseLayout", deepLinking: true, '
            'presets: [SwaggerUIBundle.presets.apis], ' + gancho + "}));")


def registrar_documentacion(app: FastAPI, modo_csp: str = "report-only", claves: Optional[Dict[str, str]] = None) -> None:
    """Agrega /docs y /redoc. Con `claves` (demo) agrega además /docs-analista y /docs-admin ya autorizadas."""
    paginas = {"/docs": ("comercio", "API antifraude"), }
    if claves:
        paginas.update({"/docs-analista": ("analista", "API antifraude · analista"), "/docs-admin": ("admin", "API antifraude · admin")})
    cabeceras = cabecera_csp(modo_csp)

    for ruta, (rol, titulo) in paginas.items():
        clave = claves.get(rol) if claves else None
        nombre = rol if clave else "base"
        app.add_api_route(ruta, (lambda t=titulo, n=nombre: HTMLResponse(_html_swagger(t, n), headers=cabeceras)),
                          methods=["GET"], include_in_schema=False, response_class=HTMLResponse)
        if clave or nombre == "base":
            app.add_api_route(f"/init/{nombre}.js", (lambda c=clave: Response(_js_inicio(c), media_type="application/javascript",
                                                                         headers={"Cache-Control": "no-store"})),
                              methods=["GET"], include_in_schema=False)

    def redoc():
        html = ('<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>API antifraude</title><link rel="icon" href="/estaticos/favicon.png"></head><body>'
                '<redoc spec-url="/openapi.json"></redoc><script src="/estaticos/redoc.standalone.js"></script></body></html>')
        return HTMLResponse(html, headers=cabeceras)
    app.add_api_route("/redoc", redoc, methods=["GET"], include_in_schema=False, response_class=HTMLResponse)
