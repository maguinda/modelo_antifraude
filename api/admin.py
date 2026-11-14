"""Administración local: crear comercios y claves. Las claves se muestran UNA sola vez, en tu terminal.

Uso:
    python -m modelo_ia.api.admin crear-comercio --nombre "Mi Tienda"
    python -m modelo_ia.api.admin crear-clave --rol admin    --nombre "Marlon A."          # una clave POR PERSONA, con su nombre
    python -m modelo_ia.api.admin crear-clave --rol analista --nombre "Ana Pérez"
    python -m modelo_ia.api.admin crear-clave --rol comercio --comercio-id 2
    python -m modelo_ia.api.admin listar-claves                                              # quién tiene acceso y cuándo la usó por última vez
    python -m modelo_ia.api.admin rotar-clave --id 3                                         # cambia la clave de una persona (la anterior se apaga)
    python -m modelo_ia.api.admin desactivar-clave --id 3                                    # cuando alguien deja de trabajar con el sistema

Regla: cada analista y cada administrador tiene SU clave, con su nombre. Así se sabe quién decidió cada revisión y se puede dar de baja a una persona
sin afectar a las demás. No compartas claves.
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
    k.add_argument("--nombre", default=None, help="obligatorio para analista y admin: la persona dueña de la clave")
    sub.add_parser("listar-claves")
    r = sub.add_parser("rotar-clave"); r.add_argument("--id", type=int, required=True)
    d = sub.add_parser("desactivar-clave"); d.add_argument("--id", type=int, required=True)
    a = ap.parse_args(argv)

    ajustes = Ajustes.desde_entorno()
    almacen = Almacen(ajustes.ruta_bd, ajustes.pepper)
    almacen.inicializar()
    if ajustes.pepper_inseguro:
        print("AVISO: usando el secreto de desarrollo. Define MODELO_IA_PEPPER antes de usar esto de verdad.")
    try:
        if a.orden == "crear-comercio":
            id_comercio = almacen.crear_comercio(a.nombre)
            print(f"Comercio creado con id {id_comercio}. Ahora crea su clave con: crear-clave --rol comercio --comercio-id {id_comercio}")
        elif a.orden == "crear-clave":
            if a.rol == "comercio" and a.comercio_id is None:
                ap.error("para el rol comercio indica --comercio-id")
            if a.rol in ("analista", "admin"):
                if not a.nombre:
                    ap.error(f"para el rol {a.rol} indica --nombre: la clave es de UNA persona y debe llevar su nombre")
                id_clave, clave = almacen.crear_usuario(a.rol, a.nombre)
                print(f"Clave número {id_clave} para {a.nombre.strip()} ({a.rol}). Se muestra UNA sola vez; entrégasela solo a esa persona:")
            else:
                clave = almacen.crear_clave(a.comercio_id, a.rol, a.etiqueta or (a.nombre or ""))
                print("Clave (se muestra UNA sola vez; guárdala en un lugar seguro):")
            print(clave)
        elif a.orden == "listar-claves":
            filas = almacen.listar_claves()
            print(f"{'id':>3}  {'estado':8} {'rol':9} {'comercio':22} {'nombre o etiqueta':26} {'creada':10} último uso")
            for f in filas:
                uso = (f["ultimo_uso"] or "nunca")[:16].replace("T", " ")
                print(f"{f['id']:>3}  {'activa' if f['activa'] else 'apagada':8} {f['rol']:9} {f['comercio'][:22]:22} {(f['etiqueta'] or '—')[:26]:26} {f['creada'][:10]} {uso}")
            sin_nombre = [f for f in filas if f["activa"] and f["rol"] in ("analista", "admin") and not f["etiqueta"]]
            if sin_nombre:
                print(f"\nAviso: {len(sin_nombre)} clave(s) de analista o admin sin nombre (ids {', '.join(str(f['id']) for f in sin_nombre)}). Cámbialas por claves con nombre con «rotar-clave» o crea nuevas y apaga estas.")
        elif a.orden == "rotar-clave":
            nuevo, clave = almacen.rotar_clave(a.id)
            print(f"La clave {a.id} quedó apagada. Clave nueva número {nuevo} (se muestra UNA sola vez):")
            print(clave)
        else:
            almacen.desactivar_clave(a.id)
            print("Clave desactivada.")
    except ValueError as e:
        raise SystemExit(f"ERROR: {e}")


if __name__ == "__main__":
    main()
