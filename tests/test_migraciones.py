"""Migraciones versionadas: bases de cada forma histórica, fallos a mitad, base más nueva, copia previa, procesos arrancando a la vez y la consola."""

import ast
import hashlib
import inspect
import json
import os
import sqlite3
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from modelo_ia.api import admin
from modelo_ia.api import migraciones as mig
from modelo_ia.api.ajustes import Ajustes
from modelo_ia.api.almacen import DDL, INDICES_PANEL, PLATAFORMA, Almacen
from modelo_ia.api.app import crear_app
from modelo_ia.api.migraciones import BaseMasNueva, CopiaPreviaFallo, Migracion, copia_previa, sentencias, validar
from modelo_ia.api.repositorios.esquema import Esquema

IDS = [1, 2]  # las migraciones que existen hoy; quien agregue una nueva cambia solo estas dos constantes
SIGUIENTE = 3  # el número de la próxima (las pruebas usan migraciones de juguete con este número)
PEPPER = "p" * 40
RAIZ = Path(__file__).resolve().parents[2]  # la carpeta que contiene modelo_ia/ (para lanzar procesos que lo importen)

BASE = {  # solo las columnas que existieron SIEMPRE: son las que no pueden cambiar al migrar, sea cual sea la forma de la base de partida
    "comercios": ["id", "nombre", "creado", "activo"],
    "claves": ["id", "hash", "comercio_id", "rol", "etiqueta", "activa", "creada"],
    "evaluaciones": [
        "id",
        "comercio_id",
        "id_transaccion",
        "id_cliente",
        "fecha",
        "huella",
        "fila_json",
        "solicitud_hash",
        "decision",
        "decision_modelo",
        "probabilidad",
        "motivos_json",
        "reglas_json",
        "modelo_nombre",
        "modelo_version",
        "modelo_conjunto",
        "modo",
        "latencia_ms",
        "creada",
    ],
    "revisiones": [
        "id_evaluacion",
        "comercio_id",
        "estado",
        "decision_analista",
        "nota",
        "clave_analista",
        "creada",
        "resuelta",
    ],
    "resultados": ["id_evaluacion", "comercio_id", "resultado", "fecha_confirmacion", "nota", "recibido"],
    "lista_negra": ["comercio_id", "id_cliente", "motivo", "creada"],
    "auditoria": ["id", "momento", "comercio_id", "clave_id", "accion", "id_evaluacion", "detalle"],
}


def poblar(alm: Almacen) -> str:
    """Una base con algo de todo: comercios, claves, 5 pagos (uno en revisión y uno con resultado), lista negra y auditoría. Devuelve una clave de comercio."""
    a = alm.crear_comercio("Tienda A")
    alm.crear_comercio("Tienda B")
    clave = alm.crear_clave(a, "comercio", "caja 1")
    alm.crear_usuario("analista", "Ana Pérez")
    with alm.transaccion() as con:
        for i in range(5):
            huella, ip = f"FP-{i}", f"abcdef01{i:08d}"
            fila = {
                "id_cliente": f"C{i}",
                "monto_cop": 100000 + 1000 * i,
                "ip_huella": ip,
                "huella_dispositivo": huella,
            }
            con.execute(
                "INSERT INTO evaluaciones (id, comercio_id, id_transaccion, id_cliente, fecha, huella, fila_json, solicitud_hash, decision, decision_modelo, probabilidad, "
                "motivos_json, reglas_json, modelo_nombre, modelo_version, modelo_conjunto, modo, latencia_ms, creada, id_seguimiento, ip_codigo, disp_codigo, monto_cop) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"11111111-1111-4111-8111-00000000000{i}",
                    a,
                    f"T{i}",
                    f"C{i}",
                    f"2026-11-1{i}T10:00:00",
                    huella,
                    json.dumps(fila),
                    f"h{i}",
                    "EN_REVISION" if i == 0 else "APROBADA",
                    "APROBADA",
                    0.1 + i / 10,
                    "[]",
                    "[]",
                    "logistica",
                    "0.3.0",
                    "completo",
                    "normal",
                    12.5,
                    f"2026-11-1{i}T10:00:01",
                    f"AF-ABC{i}-DEF{i}",
                    alm.codigo_ip(ip),
                    alm.codigo_dispositivo(huella),
                    fila["monto_cop"],
                ),
            )
        con.execute(
            "INSERT INTO revisiones (id_evaluacion, comercio_id, estado, creada) VALUES (?,?,?,?)",
            ("11111111-1111-4111-8111-000000000000", a, "pendiente", "2026-11-10T10:00:02"),
        )
        con.execute(
            "INSERT INTO resultados (id_evaluacion, comercio_id, resultado, fecha_confirmacion, recibido) VALUES (?,?,?,?,?)",
            ("11111111-1111-4111-8111-000000000001", a, "LEGITIMA", "2026-11-11T10:00:00", "2026-11-11T10:00:05"),
        )
        con.execute(
            "INSERT INTO lista_negra (comercio_id, id_cliente, motivo, creada) VALUES (?,?,?,?)",
            (a, "C-MALO", "contracargos", "2026-11-12T10:00:00"),
        )
    return clave


def foto(ruta) -> dict:
    con = sqlite3.connect(ruta)
    try:
        return {
            t: con.execute(f"SELECT {', '.join(cols)} FROM {t} ORDER BY 1, 2").fetchall() for t, cols in BASE.items()
        }
    finally:
        con.close()


def derivados(ruta):
    con = sqlite3.connect(ruta)
    try:
        return con.execute("SELECT id, ip_codigo, disp_codigo, monto_cop FROM evaluaciones ORDER BY id").fetchall()
    finally:
        con.close()


def esquema(ruta):
    con = sqlite3.connect(ruta)
    try:
        objetos = sorted(con.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
        return objetos, {
            n: sorted(r[1] for r in con.execute(f"PRAGMA table_info({n})")) for t, n in objetos if t == "table"
        }
    finally:
        con.close()


def sana(ruta) -> bool:
    con = sqlite3.connect(ruta)
    try:
        return (
            con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            and con.execute("PRAGMA foreign_key_check").fetchall() == []
        )
    finally:
        con.close()


def volver_vieja(ruta, forma: int) -> None:
    """Deja la base como la habría dejado una versión anterior del programa. 0: sin tabla de migraciones (la de hoy antes de este cambio); 1: sin acuerdos ni columnas de
    suspensión; 2: además sin último uso de las claves; 3: además sin las columnas e índices del panel; 4: además sin red_origen ni estado_origen."""
    con = sqlite3.connect(ruta)
    try:
        con.execute("DROP TABLE migraciones")
        if forma >= 1:
            con.execute("DROP TABLE acuerdos")
            for c in ("razon_social", "nit", "suspendido_en", "motivo_suspension"):
                con.execute(f"ALTER TABLE comercios DROP COLUMN {c}")
        if forma >= 2:
            for c in ("ultimo_uso", "creada_por"):
                con.execute(f"ALTER TABLE claves DROP COLUMN {c}")
        if forma >= 3:
            for ix in ("ux_eval_seguimiento", "ix_eval_ip", "ix_eval_disp"):
                con.execute(f"DROP INDEX {ix}")
            for c in ("id_seguimiento", "ip_codigo", "disp_codigo", "monto_cop", "no_enviados_json", "senales_json"):
                con.execute(f"ALTER TABLE evaluaciones DROP COLUMN {c}")
        if forma >= 4:
            for c in ("red_origen", "estado_origen"):
                con.execute(f"ALTER TABLE evaluaciones DROP COLUMN {c}")
        con.commit()
    finally:
        con.close()


def base_vieja(carpeta: Path, forma: int, nombre: str = "v.sqlite3"):
    """(ruta, clave, foto de lo que no puede cambiar): una base con datos, envejecida a la forma pedida."""
    ruta = carpeta / nombre
    alm = Almacen(ruta, PEPPER)
    alm.inicializar()
    clave = poblar(alm)
    antes = foto(ruta)
    volver_vieja(ruta, forma)
    assert foto(ruta) == antes  # envejecerla no tocó lo que debe conservarse
    return ruta, clave, antes


# ------------------------------------------------------------------ una base nueva
def test_una_base_nueva_se_migra_una_vez_sin_copia_y_reabrirla_no_hace_nada(tmp_path):
    alm = Almacen(tmp_path / "n.sqlite3", PEPPER)
    assert alm.inicializar() == IDS and alm.inicializar() == [] and alm.inicializar() == []
    assert alm.copias_previas() == []  # no había nada que proteger
    est = alm.estado_migraciones()
    assert (
        [(e["id"], e["desconocida"]) for e in est] == [(i, False) for i in IDS]
        and est[0]["aplicada"]
        and est[0]["nombre"].startswith("Esquema inicial")
    )
    con = sqlite3.connect(tmp_path / "n.sqlite3")
    assert (
        con.execute("SELECT COUNT(*) FROM migraciones").fetchone()[0] == len(IDS)
        and con.execute("SELECT nombre FROM comercios WHERE id=?", (PLATAFORMA,)).fetchone()[0] == "PLATAFORMA"
    )
    assert (
        sana(tmp_path / "n.sqlite3") and con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    )  # y la base queda en modo WAL


def test_los_numeros_de_las_migraciones_son_seguidos_y_el_estado_no_modifica_nada(tmp_path):
    alm = Almacen(tmp_path / "e.sqlite3", PEPPER)
    validar(alm._migraciones())
    assert [m.id for m in alm._migraciones()] == IDS
    con = sqlite3.connect(tmp_path / "e.sqlite3")  # una base vacía: el estado dice que falta todo y NO crea la tabla
    con.execute("CREATE TABLE otra (x INTEGER)")
    con.commit()
    con.row_factory = sqlite3.Row
    assert [(e["id"], e["aplicada"]) for e in mig.estado(con, alm._migraciones())] == [(i, None) for i in IDS]
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name='migraciones'").fetchone() is None


# ------------------------------------------------------------------ bases de cada forma histórica
@pytest.mark.parametrize("forma", [0, 1, 2, 3, 4])
def test_una_base_de_cada_forma_historica_se_adopta_sin_perder_nada(tmp_path, forma):
    ruta, clave, antes = base_vieja(tmp_path, forma)
    derivados_antes = derivados(ruta) if forma < 3 else None
    ref = tmp_path / "ref.sqlite3"
    Almacen(ref, PEPPER).inicializar()

    alm = Almacen(ruta, PEPPER)
    assert alm.inicializar() == IDS
    assert foto(ruta) == antes  # ni una fila ni un valor de lo que existía cambió
    assert esquema(ruta) == esquema(ref) and sana(ruta)  # y quedó exactamente con el esquema de una base nueva
    assert alm.autenticar(clave).comercio_id is not None  # las claves siguen entrando
    if forma < 3:
        assert derivados(ruta) == derivados_antes
    con = sqlite3.connect(ruta)
    filas = con.execute(
        "SELECT id_seguimiento, ip_codigo, disp_codigo, monto_cop FROM evaluaciones ORDER BY id"
    ).fetchall()
    assert len({f[0] for f in filas}) == 5 and all(
        f[0] and f[1] and f[2] and f[3] for f in filas
    )  # los códigos que faltaban se calcularon, sin repetirse
    assert [f[1:] for f in filas] == [tuple(d[1:]) for d in derivados(ruta)]
    assert con.execute("SELECT id FROM migraciones").fetchall() == [(i,) for i in IDS]

    copias = alm.copias_previas()  # y antes de tocarla se guardó UNA copia de cómo estaba
    assert (
        len(copias) == 1 and copias[0].name.startswith("v.sqlite3.antes-de-migrar-v1-") and copias[0].parent == tmp_path
    )
    assert foto(copias[0]) == antes and sana(copias[0])
    assert (
        sqlite3.connect(copias[0]).execute("SELECT COUNT(*) FROM migraciones").fetchone()[0] == 0
    )  # copia de ANTES de migrar
    assert stat.S_IMODE(copias[0].stat().st_mode) == 0o600
    assert (
        sqlite3.connect(copias[0]).execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    )  # un solo archivo, sin -wal ni -shm que arrastrar

    assert alm.inicializar() == [] and len(alm.copias_previas()) == 1  # reabrirla no migra ni copia otra vez


def test_los_codigos_derivados_de_una_base_vieja_coinciden_con_los_que_habria_calculado_el_programa(tmp_path):
    ruta, _, _ = base_vieja(tmp_path, 3)
    ref = tmp_path / "ref2.sqlite3"
    ref_alm = Almacen(ref, PEPPER)
    ref_alm.inicializar()
    poblar(ref_alm)
    Almacen(ruta, PEPPER).inicializar()
    assert derivados(ruta) == derivados(
        ref
    )  # mismo id, mismo código de IP, mismo de dispositivo, mismo monto: sin «o» que lo suavice


# ------------------------------------------------------------------ fallos a mitad
def test_si_una_migracion_falla_no_queda_nada_a_medias_y_se_puede_reintentar(tmp_path):
    ruta, _, antes = base_vieja(tmp_path, 0)
    alm = Almacen(ruta, PEPPER)

    def falla(con):
        con.execute("CREATE TABLE a_medias (x INTEGER)")
        con.execute("INSERT INTO a_medias VALUES (1)")
        con.execute("INSERT INTO tabla_que_no_existe VALUES (1)")  # falla aquí, con lo anterior ya hecho

    with pytest.raises(sqlite3.OperationalError):
        alm.inicializar(alm._migraciones() + [Migracion(SIGUIENTE, "falla a propósito", falla)])
    con = sqlite3.connect(ruta)
    assert (
        con.execute("SELECT name FROM sqlite_master WHERE name='a_medias'").fetchone() is None
    )  # lo que alcanzó a hacer se deshizo entero
    assert con.execute("SELECT id FROM migraciones").fetchall() == [
        (i,) for i in IDS
    ]  # las reales quedaron aplicadas y la de juguete NO se registró
    assert foto(ruta) == antes and sana(ruta) and len(alm.copias_previas()) == 1

    assert alm.inicializar(
        alm._migraciones() + [Migracion(SIGUIENTE, "arreglada", lambda c: c.execute("CREATE TABLE nueva (x INTEGER)"))]
    ) == [SIGUIENTE]
    copias = alm.copias_previas()
    assert (
        len(copias) == 2 and any("-v1-" in c.name for c in copias) and any(f"-v{SIGUIENTE}-" in c.name for c in copias)
    )  # cada actualización (la primera, y la de la migración arreglada), con su copia previa
    assert sqlite3.connect(ruta).execute("SELECT id FROM migraciones ORDER BY id").fetchall() == [
        (i,) for i in IDS + [SIGUIENTE]
    ]


@pytest.mark.parametrize("forma", [None, 0, 3])
def test_si_falla_la_migracion_1_a_mitad_no_queda_el_esquema_a_medias(tmp_path, monkeypatch, forma):
    """La 1 hace muchos pasos (tablas, columnas, índices, códigos). Si el último falla, ninguno debe quedar: ni en una base nueva ni en una vieja."""
    if forma is None:
        ruta, antes = tmp_path / "n.sqlite3", None
    else:
        ruta, _, antes = base_vieja(tmp_path, forma)
    obj_antes = esquema(ruta) if ruta.exists() else None

    def rompe(self, con):
        raise RuntimeError("falla después de crear tablas y columnas")

    original = Esquema.rellenar_columnas_nuevas
    monkeypatch.setattr(Esquema, "rellenar_columnas_nuevas", lambda self, con: (original(self, con), rompe(self, con)))
    with pytest.raises(RuntimeError, match="falla después"):
        Almacen(ruta, PEPPER).inicializar()
    con = sqlite3.connect(ruta)
    assert con.execute("SELECT COUNT(*) FROM migraciones").fetchone()[0] == 0  # no quedó registrada
    nombres = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
    if obj_antes is None:
        assert nombres == {"migraciones"}  # una base nueva no quedó con tablas sueltas
    else:
        assert (
            esquema(ruta)[1].keys() - {"migraciones"} == obj_antes[1].keys()
            and {k: v for k, v in esquema(ruta)[1].items() if k != "migraciones"} == obj_antes[1]
        )
        assert foto(ruta) == antes and sana(ruta)
    monkeypatch.undo()
    assert Almacen(ruta, PEPPER).inicializar() == IDS and sana(ruta)  # y reintentar funciona


def test_una_migracion_ya_aplicada_no_se_vuelve_a_ejecutar_aunque_cambie(tmp_path):
    alm = Almacen(tmp_path / "x.sqlite3", PEPPER)
    alm.inicializar()
    llamadas = []
    assert alm.inicializar(alm._migraciones() + [Migracion(SIGUIENTE, "paso", lambda c: llamadas.append(1))]) == [
        SIGUIENTE
    ]
    assert alm.inicializar(
        alm._migraciones() + [Migracion(SIGUIENTE, "paso", lambda c: llamadas.append(1))]
    ) == [] and llamadas == [1]


def test_si_no_se_puede_guardar_la_copia_no_se_migra_nada(tmp_path, monkeypatch):
    ruta, _, antes = base_vieja(tmp_path, 1)
    obj_antes = esquema(ruta)

    def sin_espacio(*a, **k):
        raise CopiaPreviaFallo("sin espacio")

    monkeypatch.setattr(mig, "copia_previa", sin_espacio)
    with pytest.raises(CopiaPreviaFallo):
        Almacen(ruta, PEPPER).inicializar()
    assert esquema(ruta)[1].get("migraciones") == ["aplicada", "id", "nombre"]  # solo existe la tabla vacía de control
    con = sqlite3.connect(ruta)
    assert con.execute("SELECT COUNT(*) FROM migraciones").fetchone()[0] == 0  # no se registró nada
    assert {k: v for k, v in esquema(ruta)[1].items() if k != "migraciones"} == {
        k: v for k, v in obj_antes[1].items()
    }  # y el esquema quedó como estaba
    assert foto(ruta) == antes


def test_la_copia_previa_queda_junto_a_la_base_con_permisos_600_y_si_falla_no_deja_basura(tmp_path):
    carpeta = tmp_path / "b"
    carpeta.mkdir()
    ruta = carpeta / "v.sqlite3"
    alm = Almacen(ruta, PEPPER)
    alm.inicializar()
    poblar(alm)
    copia = copia_previa(ruta, 5)
    assert (
        copia.parent == carpeta
        and copia.name.startswith("v.sqlite3.antes-de-migrar-v5-")
        and copia.name.endswith(".sqlite3")
    )
    assert stat.S_IMODE(copia.stat().st_mode) == 0o600 and sana(copia)
    if os.geteuid() == 0:
        pytest.skip("como administrador del sistema los permisos de carpeta no se aplican")
    existentes = sorted(p.name for p in carpeta.iterdir())
    os.chmod(carpeta, 0o500)
    try:
        with pytest.raises(CopiaPreviaFallo, match="No se migró nada"):
            copia_previa(ruta, 6)
    finally:
        os.chmod(carpeta, 0o700)
    assert sorted(p.name for p in carpeta.iterdir()) == existentes  # no quedó ni un archivo a medias


def test_dos_copias_en_el_mismo_segundo_no_se_pisan(tmp_path):
    ruta = tmp_path / "v.sqlite3"
    alm = Almacen(ruta, PEPPER)
    alm.inicializar()
    poblar(alm)
    fijo = lambda: __import__("datetime").datetime(2026, 11, 14, 10, 0, 0)
    a, b = copia_previa(ruta, 1, fijo), copia_previa(ruta, 1, fijo)
    assert a != b and a.exists() and b.exists()


def test_se_copia_solo_si_la_base_tiene_algo_que_valga_la_pena(tmp_path):
    def hay(nombre, preparar=None):
        alm = Almacen(tmp_path / nombre, PEPPER)
        alm.inicializar()
        if preparar:
            preparar(alm)
        with alm.lectura() as con:
            return alm._hay_datos(con)

    assert hay("a.sqlite3") is False  # solo la plataforma
    assert hay("b.sqlite3", lambda a: a.crear_comercio("T")) is True
    assert hay("c.sqlite3", lambda a: a.crear_usuario("analista", "Ana Pérez")) is True  # solo una clave
    assert Almacen._hay_datos(sqlite3.connect(":memory:")) is False  # una base sin tablas


# ------------------------------------------------------------------ una base más nueva que el programa
def test_una_base_de_una_version_mas_nueva_se_rechaza_sin_tocarla(tmp_path):
    ruta = tmp_path / "f.sqlite3"
    alm = Almacen(ruta, PEPPER)
    alm.inicializar()
    poblar(alm)
    con = sqlite3.connect(ruta)
    con.execute(
        "INSERT INTO migraciones (id, nombre, aplicada) VALUES (99, 'cambio del futuro', '2099-01-01T00:00:00')"
    )
    con.commit()
    con.close()
    antes, obj = foto(ruta), esquema(ruta)
    with pytest.raises(BaseMasNueva, match="versión más nueva"):
        Almacen(ruta, PEPPER).inicializar()
    assert foto(ruta) == antes and esquema(ruta) == obj and alm.copias_previas() == []
    assert [(e["id"], e["desconocida"]) for e in alm.estado_migraciones()] == [(i, False) for i in IDS] + [(99, True)]
    with pytest.raises(BaseMasNueva):
        crear_app(Ajustes(ruta_bd=ruta, pepper=PEPPER))  # el servicio ni siquiera arranca


def test_un_archivo_vacio_cuenta_como_base_nueva_y_uno_dañado_falla_con_claridad(tmp_path):
    vacio = tmp_path / "vacio.sqlite3"
    vacio.write_bytes(b"")
    assert Almacen(vacio, PEPPER).inicializar() == IDS
    roto = tmp_path / "roto.sqlite3"
    roto.write_bytes(b"esto no es una base de datos" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        Almacen(roto, PEPPER).inicializar()
    assert roto.read_bytes() == b"esto no es una base de datos" * 100  # y no la tocó


# ------------------------------------------------------------------ las piezas sueltas
def test_el_cortador_de_sql_respeta_triggers_cadenas_y_comentarios():
    partes = sentencias(DDL)
    assert sum(p.startswith("CREATE TRIGGER") for p in partes) == 4 and all(
        sqlite3.complete_statement(p) for p in partes
    )
    sql = "CREATE TABLE t (x TEXT); -- comentario; con punto y coma\nINSERT INTO t VALUES ('a;b');\n"
    assert sentencias(sql) == [
        "CREATE TABLE t (x TEXT); -- comentario; con punto y coma",
        "INSERT INTO t VALUES ('a;b');",
    ]
    con = sqlite3.connect(":memory:")
    for p in sentencias(sql):
        con.execute(p)
    assert con.execute("SELECT x FROM t").fetchall() == [("a;b",)]  # el «;» dentro del texto no cortó nada
    con = sqlite3.connect(":memory:")
    for p in partes:
        con.execute(p)  # todas se pueden ejecutar una a una dentro de una transacción
    with pytest.raises(ValueError, match="incompleto"):
        sentencias("CREATE TABLE t (x TEXT")


def test_una_lista_de_migraciones_mal_numerada_es_un_error_de_programacion():
    ok = lambda c: None
    for mala in (
        [Migracion(2, "salta", ok)],
        [Migracion(1, "a", ok), Migracion(1, "repetida", ok)],
        [Migracion(1, "a", ok), Migracion(3, "salta", ok)],
        [Migracion(1, "  ", ok)],
    ):
        with pytest.raises(ValueError):
            validar(mala)
    validar([Migracion(1, "a", ok), Migracion(2, "b", ok)])


def textos_de_la_migracion_1() -> str:
    """Todos los textos fijos de la migración 1 (el SQL y los nombres de columnas que agrega), en orden. Se usan los textos y no el árbol del código porque
    `ast.dump` cambia entre versiones de Python (3.9 y 3.12 darían huellas distintas) y los textos no."""
    fuente = ast.parse(textwrap.dedent(inspect.getsource(Esquema.m1_esquema_inicial))).body[0]
    cuerpo = (
        fuente.body[1:]
        if isinstance(fuente.body[0], ast.Expr) and isinstance(getattr(fuente.body[0], "value", None), ast.Constant)
        else fuente.body
    )  # sin el docstring
    return "|".join(
        n.value for c in cuerpo for n in ast.walk(c) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )


def test_la_migracion_1_esta_congelada_para_cambiar_el_esquema_hay_que_agregar_una_nueva():
    """Una base ya migrada no vuelve a ejecutar la 1: editarla cambiaría solo las bases NUEVAS y dejaría las demás distintas sin que nada avise."""
    huella = hashlib.sha256((DDL + "|" + INDICES_PANEL + "|" + textos_de_la_migracion_1()).encode("utf-8")).hexdigest()
    assert huella == HUELLA_MIGRACION_1, (
        "Cambiaste el esquema de la migración 1 (DDL, INDICES_PANEL o los textos de su función). No la edites: agrega una migración 2 en "
        f"Almacen._migraciones que haga el cambio sobre las bases existentes. (huella actual: {huella})"
    )


HUELLA_MIGRACION_1 = "12545152abbee6fc3481c3a0ff1744a7c631ae477324eb9820d9aa4a3abb3a34"


# ------------------------------------------------------------------ procesos arrancando a la vez
CODIGO_PROCESO = """
import json, pathlib, sys, time
from modelo_ia.api.almacen import Almacen
ir = pathlib.Path(sys.argv[2])
while not ir.exists():
    time.sleep(0.001)
print(json.dumps(Almacen(sys.argv[1], "p" * 40).inicializar()))
"""


def test_concurrencia_30_rondas_tres_procesos_arrancando_a_la_vez_migran_una_sola_vez(tmp_path):
    maestra, _, antes = base_vieja(tmp_path, 2, "maestra.sqlite3")
    con = sqlite3.connect(maestra)
    entorno = {**os.environ, "PYTHONPATH": str(RAIZ)}
    for ronda in range(30):
        ruta = tmp_path / f"r{ronda}.sqlite3"
        destino = sqlite3.connect(ruta)
        con.backup(destino)
        destino.close()  # una copia consistente de la misma base vieja para cada ronda
        ir = tmp_path / f"ir{ronda}"
        procs = [
            subprocess.Popen(
                [sys.executable, "-W", "ignore", "-c", CODIGO_PROCESO, str(ruta), str(ir)],
                cwd=str(RAIZ),
                env=entorno,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(3)
        ]
        time.sleep(0.2)  # que los tres lleguen a la línea de salida
        ir.touch()
        salidas = [p.communicate(timeout=120) for p in procs]
        assert [p.returncode for p in procs] == [0, 0, 0], (ronda, [s[1][-300:] for s in salidas])
        aplicadas = sorted(i for out, _ in salidas for i in json.loads(out))
        assert aplicadas == IDS, (ronda, aplicadas)  # la migración se aplicó UNA sola vez entre los tres
        alm = Almacen(ruta, PEPPER)
        assert sqlite3.connect(ruta).execute("SELECT id FROM migraciones").fetchall() == [(i,) for i in IDS]
        assert len(alm.copias_previas()) == 1, ronda  # y hay una sola copia previa
        assert foto(ruta) == antes and sana(ruta), ronda
    con.close()


# ------------------------------------------------------------------ la consola
@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MODELO_IA_BD", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("MODELO_IA_PEPPER", "q" * 40)

    def correr(*argv):
        try:
            admin.main(list(argv))
            codigo, mensaje = 0, ""
        except SystemExit as e:
            codigo, mensaje = (
                (e.code if isinstance(e.code, int) else 2 if e.code else 0),
                (e.code if isinstance(e.code, str) else ""),
            )
        s = capsys.readouterr()
        return codigo, s.out, s.err + mensaje

    return correr


def test_la_consola_consulta_sin_modificar_y_migra_cuando_se_le_pide(cli, tmp_path):
    ruta = tmp_path / "c.sqlite3"
    c, salida, _ = cli("migraciones")
    assert c == 0 and "todavía no existe" in salida and not ruta.exists()  # consultar no crea la base
    c, salida, _ = cli("migrar")
    assert c == 0 and f"migraciones aplicadas {IDS}" in salida
    c, salida, _ = cli("migrar")
    assert c == 0 and "ya está al día" in salida
    c, salida, _ = cli("migraciones")
    assert c == 0 and "aplicada" in salida and "Esquema inicial" in salida and "Copias previas a migrar: 0" in salida


def test_con_una_base_vieja_consultar_no_la_toca_y_cualquier_otro_comando_la_migra_y_avisa(cli, tmp_path):
    ruta, _, antes = base_vieja(tmp_path, 1, "c.sqlite3")
    c, salida, _ = cli("migraciones")
    assert c == 0 and "PENDIENTE" in salida and not Almacen(ruta, PEPPER).copias_previas()
    assert (
        sqlite3.connect(ruta).execute("SELECT 1 FROM sqlite_master WHERE name='migraciones'").fetchone() is None
    )  # consultar no la modificó
    c, salida, _ = cli("listar-claves")
    assert (
        c == 0
        and f"Base actualizada: migraciones aplicadas {IDS}" in salida
        and "Copia previa guardada: c.sqlite3.antes-de-migrar-v1-" in salida
    )
    c, salida, _ = cli("migraciones")
    assert "aplicada" in salida and "Copias previas a migrar: 1" in salida and "cuídalas igual" in salida
    assert foto(ruta) == antes


def test_la_consola_se_niega_con_un_mensaje_claro_ante_una_base_mas_nueva(cli, tmp_path):
    ruta = tmp_path / "c.sqlite3"
    alm = Almacen(ruta, "q" * 40)
    alm.inicializar()
    con = sqlite3.connect(ruta)
    con.execute("INSERT INTO migraciones VALUES (99, 'futura', '2099-01-01T00:00:00')")
    con.commit()
    con.close()
    for orden in ("migrar", "listar-claves", "crear-comercio"):
        c, _, err = cli(orden, *(["--nombre", "T"] if orden == "crear-comercio" else []))
        assert c != 0 and "versión más nueva" in err and "Traceback" not in err, orden
    c, salida, _ = cli("migraciones")
    assert c == 0 and "DESCONOCIDA" in salida
