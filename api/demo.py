"""Demo local: levanta el servicio con una base de demostración para probar en Swagger.

    python -m modelo_ia.api.demo               # primera vez: crea la base y las claves de demostración
    python -m modelo_ia.api.demo --reiniciar   # borra todo lo de la demo y empieza de cero

Páginas (solo en tu computador, http://127.0.0.1:8000):
    /docs            Swagger YA autorizado como COMERCIO  (evaluar pagos, resultados, lista negra)
    /docs-analista   Swagger YA autorizado como ANALISTA  (cola de revisión y resolverla)
    /docs-admin      Swagger YA autorizado como ADMIN     (crear comercios)
    /panel           Pantalla de ANALISTAS ya dentro      (cola de revisión manual)
    /dashboard       Panel de control (solo lectura)      (pagos, filtros, detalle de cada decisión, vínculos)
    /tienda          Tienda de prueba con el SDK          (compras de ejemplo de punta a punta)

Las claves de la demo se guardan en api/demo/claves_demo.txt (solo lectura para ti). Sirven únicamente para esta
base de demostración. NUNCA uses este modo con datos reales ni lo expongas a la red: por eso solo escucha en 127.0.0.1.
"""
import argparse
import os
import secrets
from pathlib import Path
from typing import Dict, Tuple

import uvicorn
from fastapi.responses import RedirectResponse

from .. import config
from .ajustes import Ajustes
from .almacen import PLATAFORMA, Almacen
from .app import crear_app
from .documentacion import registrar_documentacion
from .panel import registrar_panel
from .tienda import registrar_tienda

CARPETA = config.RAIZ / "api" / "demo"
BD, CLAVES, PEPPER = CARPETA / "demo.sqlite3", CARPETA / "claves_demo.txt", CARPETA / "pepper_demo.txt"


def usar_carpeta(carpeta: Path) -> None:
    """Cambia dónde viven la base y las claves de la demo (para pruebas en una carpeta desechable)."""
    global CARPETA, BD, CLAVES, PEPPER
    CARPETA = Path(carpeta)
    BD, CLAVES, PEPPER = CARPETA / "demo.sqlite3", CARPETA / "claves_demo.txt", CARPETA / "pepper_demo.txt"


def _escribir_privado(ruta: Path, texto: str) -> None:
    ruta.write_text(texto, encoding="utf-8")
    os.chmod(ruta, 0o600)                # solo tu usuario puede leerlo


def preparar(reiniciar: bool = False) -> Tuple[Ajustes, Dict[str, str]]:
    CARPETA.mkdir(exist_ok=True)
    os.chmod(CARPETA, 0o700)
    if reiniciar or not (BD.exists() and CLAVES.exists() and PEPPER.exists()):
        for f in CARPETA.glob("*"):
            f.unlink()
        pepper = secrets.token_hex(16)
        _escribir_privado(PEPPER, pepper)
        alm = Almacen(BD, pepper)
        alm.inicializar()
        comercio = alm.crear_comercio("Tienda Demo")
        claves = {"comercio": alm.crear_clave(comercio, "comercio", "demo"), "analista": alm.crear_clave(PLATAFORMA, "analista", "demo"),
                  "admin": alm.crear_clave(PLATAFORMA, "admin", "demo")}
        _escribir_privado(CLAVES, "CLAVES DE LA DEMO (solo sirven en esta base de demostración)\n"
                          + "".join(f"{rol.upper()}={clave}\n" for rol, clave in claves.items()))
    else:
        pepper = PEPPER.read_text(encoding="utf-8").strip()
        claves = {linea.split("=", 1)[0].lower(): linea.split("=", 1)[1].strip()
                  for linea in CLAVES.read_text(encoding="utf-8").splitlines() if "=" in linea}
    return Ajustes(ruta_bd=BD, pepper=pepper, limite_peticiones_por_minuto=5000), claves


def crear_app_demo(reiniciar: bool = False):
    ajustes, claves = preparar(reiniciar)
    app = crear_app(ajustes, docs=False)
    registrar_documentacion(app, ajustes.csp_modo, claves)
    registrar_panel(app, ajustes.csp_modo, claves["analista"])
    registrar_tienda(app, app.state.servicio, app.state.almacen.autenticar(claves["comercio"]), ajustes.csp_modo)
    app.add_api_route("/", lambda: RedirectResponse("/docs"), methods=["GET"], include_in_schema=False)
    return app


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Demo local de la API antifraude (solo 127.0.0.1).")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reiniciar", action="store_true", help="borra la base de demostración y empieza de cero")
    ap.add_argument("--carpeta", type=Path, default=None, help="carpeta de la base y las claves de la demo (por defecto api/demo/)")
    a = ap.parse_args(argv)
    if a.carpeta:
        usar_carpeta(a.carpeta)
    app = crear_app_demo(a.reiniciar)
    print(f"\nDemo lista:  http://127.0.0.1:{a.port}/docs   (analista: /docs-analista, admin: /docs-admin)")
    print(f"Las claves están en {CLAVES} (no las pegues en ningún chat).\n")
    uvicorn.run(app, host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
