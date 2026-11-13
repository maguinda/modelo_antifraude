"""Administración local: crear comercios y claves. La clave se muestra UNA vez, en tu terminal.

Uso:
    python -m modelo_ia.api.admin crear-comercio --nombre "Mi Tienda"
    python -m modelo_ia.api.admin crear-clave --rol admin
    python -m modelo_ia.api.admin crear-clave --rol analista
    python -m modelo_ia.api.admin crear-clave --rol comercio --comercio-id 2
    python -m modelo_ia.api.admin desactivar-clave --id 3
"""
import argparse

from .ajustes import Ajustes
from .almacen import PLATAFORMA, Almacen


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Administración del servicio antifraude.")
    sub = ap.add_subparsers(dest="orden", required=True)
    c = sub.add_parser("crear-comercio"); c.add_argument("--nombre", required=True)
    k = sub.add_parser("crear-clave"); k.add_argument("--rol", required=True, choices=["comercio", "analista", "admin"])
    k.add_argument("--comercio-id", type=int, default=None); k.add_argument("--etiqueta", default="")
    d = sub.add_parser("desactivar-clave"); d.add_argument("--id", type=int, required=True)
    a = ap.parse_args(argv)

    ajustes = Ajustes.desde_entorno()
    almacen = Almacen(ajustes.ruta_bd, ajustes.pepper)
    almacen.inicializar()
    if ajustes.pepper_inseguro:
        print("AVISO: usando el secreto de desarrollo. Define MODELO_IA_PEPPER antes de usar esto de verdad.")
    if a.orden == "crear-comercio":
        id_comercio = almacen.crear_comercio(a.nombre)
        print(f"Comercio creado con id {id_comercio}. Ahora crea su clave con: crear-clave --rol comercio --comercio-id {id_comercio}")
    elif a.orden == "crear-clave":
        if a.rol == "comercio" and a.comercio_id is None:
            ap.error("para el rol comercio indica --comercio-id")
        comercio = PLATAFORMA if a.rol in ("analista", "admin") else a.comercio_id
        print("Clave (se muestra UNA sola vez; guárdala en un lugar seguro):")
        print(almacen.crear_clave(comercio, a.rol, a.etiqueta))
    else:
        almacen.desactivar_clave(a.id)
        print("Clave desactivada.")


if __name__ == "__main__":
    main()
