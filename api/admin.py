"""Administración local: crear comercios y claves. Las claves se muestran UNA sola vez, en tu terminal.

Uso:
    python -m modelo_ia.api.admin crear-comercio --nombre "Mi Tienda"
    python -m modelo_ia.api.admin crear-clave --rol admin    --nombre "Marlon A."          # una clave POR PERSONA, con su nombre
    python -m modelo_ia.api.admin crear-clave --rol analista --nombre "Ana Pérez"
    python -m modelo_ia.api.admin crear-clave --rol comercio --comercio-id 2
    python -m modelo_ia.api.admin listar-claves                                              # quién tiene acceso y cuándo la usó por última vez
    python -m modelo_ia.api.admin activar-mfa --id 3                                         # segundo factor (código de 6 dígitos) de UN administrador: muestra el secreto UNA vez
    python -m modelo_ia.api.admin confirmar-mfa --id 3 --codigo 123456                       # lo activa de verdad, con el primer código de su app de autenticación
    python -m modelo_ia.api.admin desactivar-mfa --id 3                                      # si perdió el teléfono (se anota en la auditoría)
    python -m modelo_ia.api.admin modelo-adoptar                                             # registra y pone EN USO los modelo_actual*.joblib de siempre (con su huella)
    python -m modelo_ia.api.admin modelo-listar                                              # modelos registrados: huella, estado y cuál está en uso
    python -m modelo_ia.api.admin modelo-registrar --archivo gradient_boosting_completo_v0.3.0.joblib      # un modelo nuevo (queda candidato)
    python -m modelo_ia.api.admin modelo-promover --id 4                                     # lo pone en uso (el anterior queda archivado); reinicia el servicio
    python -m modelo_ia.api.admin modelo-revertir --rol principal                            # vuelve al modelo anterior
    python -m modelo_ia.api.admin modelo-verificar                                           # comprueba la huella de los que están en uso
    python -m modelo_ia.api.admin modelo-referencia --id 4 --datos datos/dataset.csv.gz      # calcula la referencia de deriva de un modelo ya entrenado
    python -m modelo_ia.api.admin rotar-clave --id 3                                         # cambia la clave de una persona (la anterior se apaga)
    python -m modelo_ia.api.admin desactivar-clave --id 3                                    # cuando alguien deja de trabajar con el sistema
    python -m modelo_ia.api.admin migraciones                                                # qué cambios de la base están aplicados y cuáles faltan (no toca nada)
    python -m modelo_ia.api.admin migrar                                                     # aplica los que falten (con copia previa); también lo hace el servicio al arrancar

Regla: cada analista y cada administrador tiene SU clave, con su nombre. Así se sabe quién decidió cada revisión y se puede dar de baja a una persona
sin afectar a las demás. No compartas claves.
"""

import argparse
from datetime import datetime
from pathlib import Path

from .errores_bd import CUALQUIERA
from . import totp
from .ajustes import Ajustes
from .almacen import Almacen, ComercioInexistente, OperacionNoPermitida
from .migraciones import BaseMasNueva, CopiaPreviaFallo


def _modelos(almacen, a) -> None:
    """Los comandos del registro de modelos (se importan aquí: cargar scikit-learn y pandas es lento y los demás comandos no lo necesitan)."""
    from . import registro_modelos as rm

    if a.orden == "modelo-adoptar":
        for rol, id_, archivo in rm.adoptar_actuales(almacen):
            print(f"Rol {rol}: modelo {id_} ({archivo}) registrado y EN USO.")
        print("Reinicia el servicio para que use el modelo verificado.")
    elif a.orden == "modelo-listar":
        filas = almacen.registro_modelos.listar()
        print(f"{'id':>3}  {'rol':10} {'estado':9} {'versión':8} {'modelo':18} {'huella':12} {'referencia':10} archivo")
        for m in filas:
            print(
                f"{m['id']:>3}  {m['rol']:10} {m['estado']:9} {m['version']:8} {m['nombre'][:18]:18} {m['sha256'][:12]:12} {'sí' if m['referencia'] else 'no':10} {m['archivo']}"
            )
        if not filas:
            print("(no hay modelos registrados: usa «modelo-adoptar» o «modelo-registrar»)")
    elif a.orden == "modelo-registrar":
        from .. import config

        ruta = Path(a.archivo)
        id_ = rm.registrar_archivo(
            almacen, ruta if ruta.parent != Path(".") else config.CARPETA_MODELOS / ruta.name, a.rol
        )
        print(f"Modelo registrado con el número {id_} (candidato). Para usarlo: modelo-promover --id {id_}")
    elif a.orden == "modelo-promover":
        m = almacen.registro_modelos.promover(a.id)
        print(
            f"Modelo {m['id']} ({m['archivo']}) EN USO para el rol {m['rol']}. Reinicia el servicio para que lo cargue (verificando su huella)."
        )
    elif a.orden == "modelo-revertir":
        m = almacen.registro_modelos.revertir(a.rol)
        print(f"Vuelto al modelo {m['id']} ({m['archivo']}) para el rol {a.rol}. Reinicia el servicio.")
    elif a.orden == "modelo-verificar":
        resultados = rm.verificar_en_uso(almacen)
        for rol, archivo, estado in resultados:
            print(f"{rol:10} {archivo}: {estado}")
        if not resultados:
            print("No hay modelos en uso en el registro.")
        if any(estado != "ok" for _, _, estado in resultados):
            raise SystemExit("ERROR: algún modelo en uso NO coincide con su huella registrada.")
    elif a.orden == "modelo-referencia":
        ref = rm.calcular_referencia(almacen, a.id, Path(a.datos) if a.datos else None)
        print(f"Referencia guardada para el modelo {a.id}: {ref['n']} puntajes de validación en 10 cubetas.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Administración del servicio antifraude.")
    sub = ap.add_subparsers(dest="orden", required=True)
    c = sub.add_parser("crear-comercio")
    c.add_argument("--nombre", required=True)
    c.add_argument("--razon-social", default=None)
    c.add_argument("--nit", default=None)
    sub.add_parser("listar-comercios")
    sp = sub.add_parser("suspender-comercio")
    sp.add_argument("--id", type=int, required=True)
    sp.add_argument("--motivo", required=True)
    rc = sub.add_parser("reactivar-comercio")
    rc.add_argument("--id", type=int, required=True)
    ac = sub.add_parser("registrar-acuerdo")
    ac.add_argument("--comercio-id", type=int, required=True)
    ac.add_argument("--version", required=True)
    ac.add_argument("--firmado", required=True, help="AAAA-MM-DD")
    ac.add_argument("--vigente-hasta", default=None, help="AAAA-MM-DD (opcional)")
    ac.add_argument(
        "--referencia", required=True, help="nombre o huella SHA-256 del archivo firmado (el documento no se guarda)"
    )
    k = sub.add_parser("crear-clave")
    k.add_argument("--rol", required=True, choices=["comercio", "analista", "admin"])
    k.add_argument("--comercio-id", type=int, default=None)
    k.add_argument("--etiqueta", default="")
    k.add_argument("--nombre", default=None, help="obligatorio para analista y admin: la persona dueña de la clave")
    sub.add_parser("listar-claves")
    sub.add_parser("migraciones", help="qué cambios de la base están aplicados y cuáles faltan (no modifica nada)")
    sub.add_parser(
        "migrar", help="aplica los cambios de la base que falten (guarda una copia antes si la base tiene datos)"
    )
    mp = sub.add_parser(
        "migrar-a-postgres",
        help="copia TODOS los datos de un archivo de SQLite a la base NUEVA de PostgreSQL (MODELO_IA_BD_URL), verificando fila por fila; no modifica el archivo",
    )
    mp.add_argument(
        "--sqlite", required=True, help="el archivo de SQLite de donde salen los datos (se abre solo para leer)"
    )
    r = sub.add_parser("rotar-clave")
    r.add_argument("--id", type=int, required=True)
    m1 = sub.add_parser(
        "activar-mfa",
        help="crea el secreto del segundo factor de una clave de administrador (queda pendiente de confirmar)",
    )
    m1.add_argument("--id", type=int, required=True)
    m2 = sub.add_parser(
        "confirmar-mfa", help="activa el segundo factor con el primer código de la app de autenticación"
    )
    m2.add_argument("--id", type=int, required=True)
    m2.add_argument("--codigo", required=True, help="los 6 dígitos que muestra la app")
    m3 = sub.add_parser("desactivar-mfa", help="quita el segundo factor de una clave (p. ej. si se perdió el teléfono)")
    m3.add_argument("--id", type=int, required=True)
    sub.add_parser("modelo-adoptar", help="registra y pone en uso los modelo_actual*.joblib que el servicio ya usaba")
    sub.add_parser("modelo-listar", help="los modelos registrados")
    r1 = sub.add_parser("modelo-registrar", help="registra un modelo de la carpeta de modelos como candidato")
    r1.add_argument("--archivo", required=True, help="el nombre del .joblib dentro de la carpeta de modelos")
    r1.add_argument(
        "--rol", choices=["principal", "sin_estado"], default=None, help="por defecto se deduce de los datos del modelo"
    )
    r2 = sub.add_parser("modelo-promover", help="pone un modelo en uso (el que estaba en uso queda archivado)")
    r2.add_argument("--id", type=int, required=True)
    r3 = sub.add_parser("modelo-revertir", help="vuelve al modelo que estaba en uso antes")
    r3.add_argument("--rol", choices=["principal", "sin_estado"], default="principal")
    sub.add_parser("modelo-verificar", help="comprueba la huella de los modelos en uso (sin cargarlos)")
    r4 = sub.add_parser("modelo-referencia", help="calcula la referencia de deriva de un modelo ya entrenado")
    r4.add_argument("--id", type=int, required=True)
    r4.add_argument("--datos", default=None, help="el archivo de datos de entrenamiento (por defecto, el de siempre)")
    d = sub.add_parser("desactivar-clave")
    d.add_argument("--id", type=int, required=True)
    a = ap.parse_args(argv)

    ajustes = Ajustes.desde_entorno()
    almacen = Almacen(ajustes.destino_bd, ajustes.pepper)
    if ajustes.pepper_inseguro:
        print("AVISO: usando el secreto de desarrollo. Define MODELO_IA_PEPPER antes de usar esto de verdad.")
    try:
        if a.orden == "migraciones":  # solo consulta: no aplica nada (ni crea la base si no existe)
            if not ajustes.bd_url and not Path(ajustes.ruta_bd).exists():
                print("La base todavía no existe: se crea al arrancar el servicio o con «migrar».")
                return
            for m in almacen.estado_migraciones():
                marca = "PENDIENTE" if m["aplicada"] is None else "aplicada " + m["aplicada"][:16].replace("T", " ")
                print(
                    f"{m['id']:>3}  {marca:26} {m['nombre']}"
                    + (
                        "   ← DESCONOCIDA: la base es de una versión más nueva que este programa"
                        if m["desconocida"]
                        else ""
                    )
                )
            copias = almacen.copias_previas()
            print(
                f"Copias previas a migrar: {len(copias)}"
                + ("".join(f"\n  {c.name}" for c in copias) if copias else "")
                + (
                    "\n(tienen los mismos datos que la base: cuídalas igual y bórralas cuando ya no las necesites)"
                    if copias
                    else ""
                )
            )
            return
        hechas = almacen.inicializar()
        if hechas:
            copias = almacen.copias_previas()
            print(
                f"Base actualizada: migraciones aplicadas {hechas}."
                + (f" Copia previa guardada: {copias[-1].name}" if copias else "")
            )
        if a.orden == "migrar":
            if not hechas:
                print("La base ya está al día: no había nada que migrar.")
            return
        if a.orden == "migrar-a-postgres":
            from .migrar_a_postgres import MigracionDeDatosError, copiar

            if not ajustes.bd_url:
                print(
                    "Define MODELO_IA_BD_URL (o MODELO_IA_BD_URL_ARCHIVO) con la dirección de la base NUEVA de PostgreSQL."
                )
                raise SystemExit(2)
            print(
                "Antes de seguir: el servicio que usa el SQLite debe estar DETENIDO (lo que se escriba después de la copia no estará en PostgreSQL)."
            )
            try:
                resumen = copiar(Path(a.sqlite), almacen)
            except MigracionDeDatosError as e:
                print(f"No se pasó nada: {e}")
                raise SystemExit(1)
            for tabla, r_ in resumen.items():
                print(f"  {tabla:18} {r_['filas']:>8} filas   huella {r_['huella']}  (igual en origen y destino)")
            print(
                "Listo: los datos están en PostgreSQL y la verificación coincidió en todas las tablas. "
                "Usa el MISMO MODELO_IA_PEPPER que con SQLite: si cambia, las claves guardadas dejan de reconocerse."
            )
            return
        if a.orden == "crear-comercio":
            id_comercio = almacen.crear_comercio(a.nombre, a.razon_social, a.nit)
            print(
                f"Comercio creado con id {id_comercio}. Ahora crea su clave con: crear-clave --rol comercio --comercio-id {id_comercio}"
            )
        elif a.orden == "listar-comercios":
            for com in almacen.listar_comercios():
                ac_ = com["acuerdo"]
                print(
                    f"{com['id']:>3}  {com['estado']:10} {com['nombre'][:26]:26} claves {com['claves_activas']}  pagos {com['pagos']:>6}  acuerdo: {ac_['estado']}"
                    + (
                        f" ({ac_['version']}, hasta {ac_['vigente_hasta'] or 'sin vencimiento'})"
                        if ac_["version"]
                        else ""
                    )
                )
        elif a.orden == "suspender-comercio":
            print(
                "Comercio suspendido: sus claves ya no funcionan."
                if almacen.suspender_comercio(a.id, a.motivo)
                else "Ese comercio ya estaba suspendido."
            )
        elif a.orden == "reactivar-comercio":
            print(
                "Comercio reactivado: sus claves vuelven a funcionar."
                if almacen.reactivar_comercio(a.id)
                else "Ese comercio ya estaba activo."
            )
        elif a.orden == "registrar-acuerdo":
            from datetime import date as _fecha

            try:
                firmado, hasta = (
                    _fecha.fromisoformat(a.firmado),
                    (_fecha.fromisoformat(a.vigente_hasta) if a.vigente_hasta else None),
                )
            except ValueError:
                ap.error("las fechas van como AAAA-MM-DD")
            print(
                f"Acuerdo registrado (número {almacen.registrar_acuerdo(a.comercio_id, a.version, firmado, hasta, a.referencia)})."
            )
        elif a.orden == "crear-clave":
            if a.rol == "comercio" and a.comercio_id is None:
                ap.error("para el rol comercio indica --comercio-id")
            if a.rol in ("analista", "admin"):
                if not a.nombre:
                    ap.error(f"para el rol {a.rol} indica --nombre: la clave es de UNA persona y debe llevar su nombre")
                id_clave, clave = almacen.crear_usuario(a.rol, a.nombre)
                print(
                    f"Clave número {id_clave} para {a.nombre.strip()} ({a.rol}). Se muestra UNA sola vez; entrégasela solo a esa persona:"
                )
            else:
                clave = almacen.crear_clave(a.comercio_id, a.rol, a.etiqueta or (a.nombre or ""))
                print("Clave (se muestra UNA sola vez; guárdala en un lugar seguro):")
            print(clave)
        elif a.orden == "listar-claves":
            filas = almacen.listar_claves()
            mfa = almacen.mfa_estados()
            print(
                f"{'id':>3}  {'estado':8} {'rol':9} {'comercio':22} {'nombre o etiqueta':26} {'creada':10} {'último uso':16} 2.º factor"
            )
            for f in filas:
                uso = (f["ultimo_uso"] or "nunca")[:16].replace("T", " ")
                segundo = (
                    {"activa": "activo", "pendiente": "pendiente", "sin_mfa": "no"}[mfa[f["id"]]]
                    if f["id"] in mfa
                    else "—"
                )
                print(
                    f"{f['id']:>3}  {'activa' if f['activa'] else 'apagada':8} {f['rol']:9} {f['comercio'][:22]:22} {(f['etiqueta'] or '—')[:26]:26} {f['creada'][:10]} {uso:16} {segundo}"
                )
            sin_nombre = [f for f in filas if f["activa"] and f["rol"] in ("analista", "admin") and not f["etiqueta"]]
            if sin_nombre:
                print(
                    f"\nAviso: {len(sin_nombre)} clave(s) de analista o admin sin nombre (ids {', '.join(str(f['id']) for f in sin_nombre)}). Cámbialas por claves con nombre con «rotar-clave» o crea nuevas y apaga estas."
                )
        elif a.orden == "activar-mfa":
            secreto = almacen.mfa_iniciar(a.id)
            cuenta = next((f["etiqueta"] for f in almacen.listar_claves() if f["id"] == a.id), None) or f"clave-{a.id}"
            print(
                f"Segundo factor creado para la clave {a.id}, todavía NO activo. Se muestra UNA sola vez; no lo pegues en ningún chat ni documento."
            )
            print(
                "Escribe este secreto en tu app de autenticación (Google Authenticator, Authy, 1Password…) o abre el enlace:"
            )
            print(f"  secreto: {secreto}")
            print(f"  enlace:  {totp.uri(secreto, cuenta)}")
            print(
                f"Luego confírmalo con el primer código de 6 dígitos:  admin confirmar-mfa --id {a.id} --codigo 123456"
            )
        elif a.orden == "confirmar-mfa":
            if almacen.mfa_confirmar(a.id, a.codigo, datetime.now().timestamp()):
                print(
                    f"Listo: la clave {a.id} tiene el segundo factor activo. Desde ahora, con MODELO_IA_EXIGIR_MFA=1, sus acciones de administrador piden el código en X-TOTP."
                )
            else:
                raise SystemExit(
                    "ERROR: el código no es correcto (revisa la hora del teléfono y que sea el de esta clave). El secreto sigue pendiente."
                )
        elif a.orden == "desactivar-mfa":
            almacen.mfa_desactivar(a.id)
            print(
                f"Segundo factor de la clave {a.id} desactivado (queda anotado en la auditoría). Para volver a tenerlo: activar-mfa."
            )
        elif a.orden.startswith("modelo-"):
            _modelos(almacen, a)
        elif a.orden == "rotar-clave":
            nuevo, clave = almacen.rotar_clave(a.id)
            print(f"La clave {a.id} quedó apagada. Clave nueva número {nuevo} (se muestra UNA sola vez):")
            print(clave)
        else:
            almacen.desactivar_clave(a.id)
            print("Clave desactivada.")
    except (ValueError, ComercioInexistente, OperacionNoPermitida, BaseMasNueva, CopiaPreviaFallo) as e:
        raise SystemExit(f"ERROR: {e}")
    except CUALQUIERA as e:  # la base no contesta, la dirección no sirve, falta un permiso…: mensaje corto, sin host, puerto, usuario ni contraseña
        raise SystemExit(
            f"ERROR: no se pudo usar la base de datos ({type(e).__name__}). Revisa la dirección, la red y los permisos del usuario."
        ) from None


if __name__ == "__main__":
    main()
