"""Arranca el servicio en tu computador.

    python -m modelo_ia.api.servidor                      # http://127.0.0.1:8000/docs
    python -m modelo_ia.api.servidor --https              # https://127.0.0.1:8000  (certificado de prueba autofirmado)
    python -m modelo_ia.api.servidor --workers 4          # varios procesos

Con varios procesos, el límite de peticiones por clave se cuenta POR PROCESO (en producción va en un almacén compartido).
"""
import argparse

import uvicorn

from .app import crear_app


def fabrica():
    """Cada proceso crea su propia aplicación (uvicorn --workers lo exige)."""
    return crear_app()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Servidor de la API antifraude.")
    ap.add_argument("--host", default="127.0.0.1", help="por defecto solo tu computador (no se expone a la red)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--workers", type=int, default=1, help="procesos del servidor")
    ap.add_argument("--https", action="store_true", help="HTTPS con un certificado de prueba autofirmado")
    a = ap.parse_args(argv)
    opciones = {"host": a.host, "port": a.port}
    if a.https:
        from .certificados import asegurar
        cert, llave = asegurar()
        opciones.update(ssl_certfile=str(cert), ssl_keyfile=str(llave))
    if a.workers > 1:
        uvicorn.run("modelo_ia.api.servidor:fabrica", factory=True, workers=a.workers, **opciones)
    else:
        uvicorn.run(crear_app(), **opciones)


if __name__ == "__main__":
    main()
