"""La superficie pública de `Almacen`: la fachada que el resto del programa usa. Si cambia, hay que decidirlo a propósito.

`Almacen` es una fachada sobre los repositorios (`api/repositorios/`): sus métodos conservan nombre, parámetros y valores por defecto aunque el código viva en otro lugar.
Se comparan nombres y valores por defecto, no los textos de los tipos (esos cambian de nombre completo al mover una clase de archivo)."""

import inspect
import sqlite3
from datetime import date

import pytest

from modelo_ia.api import almacen as modulo
from modelo_ia.api.almacen import Almacen

FIRMAS = {
    "_hay_datos": "(con)",
    "_migraciones": "(self)",
    "abrir": "(self)",
    "auditar": "(con, comercio_id, clave_id, accion, id_evaluacion, detalle)",
    "autenticar": "(self, clave)",
    "codigo_dispositivo": "(self, huella)",
    "codigo_ip": "(self, ip_huella)",
    "comercio_suspendido_por_clave": "(self, clave)",
    "copias_previas": "(self)",
    "crear_clave": "(self, comercio_id, rol, etiqueta='', creada_por=None)",
    "crear_comercio": "(self, nombre, razon_social=None, nit=None, por=None)",
    "crear_usuario": "(self, rol, nombre, creada_por=None)",
    "desactivar_clave": "(self, clave_id)",
    "estado_migraciones": "(self)",
    "historial_acuerdos": "(self, comercio_id, hoy=None)",
    "huella_ip": "(self, ip)",
    "inicializar": "(self, migraciones=None)",
    "lectura": "(self, instantanea=False)",
    "listar_claves": "(self)",
    "listar_comercios": "(self, hoy=None, con_nit=False)",
    "reactivar_comercio": "(self, comercio_id, por=None)",
    "registrar_acuerdo": "(self, comercio_id, version, firmado_en, vigente_hasta, referencia, por=None, hoy=None)",
    "rotar_clave": "(self, clave_id, creada_por=None)",
    "suspender_comercio": "(self, comercio_id, motivo, por=None)",
    "transaccion": "(self)",
    "verificar_comercio": "(self, con, comercio_id, exigir_acuerdo=False, hoy=None)",
}
ESTATICOS = ["_hay_datos", "auditar"]
IMPORTABLES = [
    "Almacen",
    "ComercioInexistente",
    "ComercioNoAutorizado",
    "DDL",
    "INDICES_PANEL",
    "OperacionNoPermitida",
    "PLATAFORMA",
    "Principal",
    "ahora_iso",
    "estado_acuerdo",
    "id_seguimiento_libre",
    "normalizar_nombre",
]


def firma(f) -> str:
    return (
        "("
        + ", ".join(
            p.name + ("=" + repr(p.default) if p.default is not inspect.Parameter.empty else "")
            for p in inspect.signature(f).parameters.values()
        )
        + ")"
    )


def test_cada_metodo_publico_de_almacen_sigue_existiendo_con_los_mismos_parametros():
    for nombre, esperada in FIRMAS.items():
        assert hasattr(Almacen, nombre), f"Almacen ya no tiene {nombre}"
        assert firma(getattr(Almacen, nombre)) == esperada, (
            f"{nombre}: {firma(getattr(Almacen, nombre))} (esperado {esperada})"
        )
    nuevos = sorted(
        n for n in dir(Almacen) if not n.startswith("_") and callable(getattr(Almacen, n)) and n not in FIRMAS
    )
    assert not nuevos, f"Métodos públicos nuevos en Almacen: {nuevos}. Si es a propósito, agrégalos a FIRMAS."


def test_los_metodos_estaticos_siguen_siendolo():
    for nombre in ESTATICOS:
        assert isinstance(inspect.getattr_static(Almacen, nombre), staticmethod), nombre
    assert all(not isinstance(inspect.getattr_static(Almacen, n), staticmethod) for n in FIRMAS if n not in ESTATICOS)


def test_los_nombres_que_el_resto_del_programa_importa_de_almacen_py_siguen_ahi():
    for nombre in IMPORTABLES:
        assert hasattr(modulo, nombre), f"almacen.py ya no exporta {nombre}"


# ------------------------------------------------------------------ la estructura de los repositorios
import ast
import threading
from pathlib import Path

REPOS = Path(inspect.getsourcefile(modulo)).parent / "repositorios"


def espera_al_candado(a: Almacen, accion) -> bool:
    """¿`accion`, lanzada desde otro hilo mientras una transacción está abierta, se queda esperando y termina cuando esa se suelta? Eso solo pasa si usa el candado compartido."""
    ocupado, soltar, hecho = threading.Event(), threading.Event(), threading.Event()

    def sujetar():
        with a.transaccion():
            ocupado.set()
            soltar.wait(10)

    t = threading.Thread(target=sujetar)
    t.start()
    assert ocupado.wait(5)
    h = threading.Thread(target=lambda: (accion(), hecho.set()))
    h.start()
    esperando = not hecho.wait(0.3)  # mientras la otra transacción sigue abierta, no debe poder terminar
    soltar.set()
    t.join(10)
    h.join(10)
    return esperando and hecho.is_set()  # y al soltarse, termina


def test_todos_los_repositorios_comparten_la_misma_base_y_el_mismo_candado(tmp_path):
    """La «una sola escritura a la vez» de un proceso depende de UN candado. Si un repositorio tuviera el suyo, las carreras volverían sin que nada avise."""
    a = Almacen(tmp_path / "c.sqlite3", "p" * 40)
    a.inicializar()
    idc = a.crear_comercio("Tienda existente")
    id_usuario, _ = a.crear_usuario("analista", "Ana Pérez")
    assert a.comercios.base is a.claves.base is a.esquema.base is a.base
    assert a.seudonimos is a.claves.seudonimos is a.esquema.seudonimos
    acciones = {
        "comercios.crear_comercio": lambda: a.comercios.crear_comercio("Tienda X"),
        "comercios.suspender_comercio": lambda: a.comercios.suspender_comercio(idc, "motivo de prueba"),
        "comercios.registrar_acuerdo": lambda: a.comercios.registrar_acuerdo(
            idc, "v1", date(2026, 11, 1), None, "a.pdf"
        ),
        "claves.crear_usuario": lambda: a.claves.crear_usuario("admin", "Beto Ruiz"),
        "claves.desactivar_clave": lambda: a.claves.desactivar_clave(id_usuario),
        "fachada.crear_comercio": lambda: a.crear_comercio("Tienda Y"),
    }
    for nombre, accion in acciones.items():
        assert espera_al_candado(a, accion), f"{nombre} no esperó al candado: tiene el suyo propio"


def test_los_repositorios_no_dependen_de_la_fachada_y_la_fachada_no_tiene_sql():
    for archivo in sorted(REPOS.glob("*.py")):
        for nodo in ast.walk(ast.parse(archivo.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.ImportFrom) and nodo.module:
                nombres = {a.name for a in nodo.names}
                importa_la_fachada = (nodo.module or "").split(".")[-1] == "almacen" or (
                    not nodo.module and "almacen" in nombres
                )  # «from ..almacen import x» y «from .. import almacen»
                assert not importa_la_fachada, f"{archivo.name} importa la fachada"
    fuente = inspect.getsource(modulo)
    for palabra in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE ", "ALTER ", "PRAGMA "):
        assert palabra not in fuente, f"almacen.py (la fachada) no debe tener SQL: encontré «{palabra.strip()}»"


def test_las_derivaciones_con_el_secreto_dan_siempre_los_mismos_valores():
    """Si estas cambian, TODAS las claves y huellas ya guardadas dejan de coincidir: nadie podría entrar. Los valores se calcularon aparte, con hmac de la biblioteca estándar."""
    from tempfile import mkdtemp

    a = Almacen(Path(mkdtemp()) / "k.sqlite3", "p" * 40)
    assert (
        a.seudonimos.hash_clave("mia_comercio_ejemplo")
        == "4a8333810ab93f439716a7945c2ed40658a2a507dc99256a749049fa96b0d501"
    )
    assert (
        a.huella_ip("192.0.2.1") == "577177ca935572a9"
        and a.codigo_ip("577177ca935572a9") == "577177CA"
        and a.codigo_dispositivo("FP-1") == "90986733"
    )
    assert (
        a.huella_ip("2001:db8:0:0:0:0:0:1") == a.huella_ip("2001:db8::1") == a.huella_ip("2001:0DB8::0001")
    )  # la misma IP escrita de tres formas
    assert a.huella_ip("2001:db8::1") != a.huella_ip("2001:db8::2")
    assert Almacen(Path(mkdtemp()) / "k2.sqlite3", "otro" * 10).huella_ip("192.0.2.1") != a.huella_ip(
        "192.0.2.1"
    )  # y con otro secreto, otro valor


# ------------------------------------------------------------------ comportamiento que la fachada debe conservar (huecos que ninguna prueba cubría)
def test_verificar_comercio_usa_el_dia_que_se_le_pasa_no_el_de_hoy(tmp_path):
    a = Almacen(tmp_path / "v.sqlite3", "p" * 40)
    a.inicializar()
    c = a.crear_comercio("Tienda V")
    a.registrar_acuerdo(c, "v1", date(2020, 1, 1), date(2020, 12, 31), "a.pdf", hoy=date(2020, 6, 1))
    from modelo_ia.api.almacen import ComercioNoAutorizado

    with a.transaccion() as con:
        a.verificar_comercio(con, c, True, date(2020, 6, 1))  # ese día el acuerdo estaba vigente: no falla
        with pytest.raises(ComercioNoAutorizado) as e:
            a.verificar_comercio(con, c, True, date(2021, 6, 1))  # un año después ya había vencido
        assert e.value.codigo == "sin_acuerdo"


def test_una_lectura_instantanea_ve_una_foto_y_una_normal_ve_lo_nuevo(tmp_path):
    a = Almacen(tmp_path / "l.sqlite3", "p" * 40)
    a.inicializar()
    with a.lectura(instantanea=True) as foto, a.lectura() as viva:
        antes = foto.execute("SELECT COUNT(*) FROM comercios").fetchone()[0]
        a.crear_comercio("Tienda nueva")  # alguien escribe mientras las dos lecturas están abiertas
        assert (
            foto.execute("SELECT COUNT(*) FROM comercios").fetchone()[0] == antes
        )  # la instantánea sigue viendo lo de antes
        assert viva.execute("SELECT COUNT(*) FROM comercios").fetchone()[0] == antes + 1  # la normal ve lo nuevo


def test_rotar_una_clave_deja_escrito_quien_la_roto_y_apaga_la_anterior(tmp_path):
    a = Almacen(tmp_path / "r.sqlite3", "p" * 40)
    a.inicializar()
    admin_id, _ = a.crear_usuario("admin", "Beto Ruiz")
    ana_id, ana = a.crear_usuario("analista", "Ana Pérez")
    nueva_id, nueva = a.rotar_clave(ana_id, creada_por=admin_id)
    assert a.autenticar(ana) is None and a.autenticar(nueva).usuario == "Ana Pérez"
    fila = (
        sqlite3.connect(a.ruta)
        .execute("SELECT creada_por, activa, etiqueta FROM claves WHERE id=?", (nueva_id,))
        .fetchone()
    )
    assert fila == (admin_id, 1, "Ana Pérez")


def test_la_durabilidad_solo_acepta_dos_valores_y_se_aplica_a_cada_conexion(tmp_path):
    for valor, esperado in (("NORMAL", 1), ("FULL", 2)):
        a = Almacen(tmp_path / f"{valor}.sqlite3", "p" * 40, valor)
        con = a.abrir()
        try:
            assert con.execute("PRAGMA synchronous").fetchone()[0] == esperado
        finally:
            con.close()
    for malo in ("MAL", "full", "", None):
        with pytest.raises(ValueError, match="NORMAL o FULL"):
            Almacen(tmp_path / "x.sqlite3", "p" * 40, malo)


def test_la_auditoria_guarda_los_acentos_tal_cual_y_no_escapados(tmp_path):
    a = Almacen(tmp_path / "a.sqlite3", "p" * 40)
    a.inicializar()
    with a.transaccion() as con:
        a.auditar(con, None, None, "prueba", None, {"motivo": "acción de revisión ñandú"})
    texto = sqlite3.connect(a.ruta).execute("SELECT detalle FROM auditoria WHERE accion='prueba'").fetchone()[0]
    assert "acción de revisión ñandú" in texto and "\\u" not in texto
