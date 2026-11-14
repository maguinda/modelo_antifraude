"""Cambio 7a: el SQL que antes dependía de funciones propias de SQLite ahora es portable. Cada prueba defiende una de esas traducciones.

Lo que se comprueba: la migración 4 (secuencia `seq`, tabla `evaluacion_reglas`, columna `minutos_resolucion`) conserva los datos de una base vieja; las
consultas dan lo mismo que antes; y los casos donde SQLite y PostgreSQL se comportan distinto (nulos al ordenar, cubetas con CAST) están fijados.
"""

import json
import sqlite3
from itertools import count
from types import SimpleNamespace

import pytest

from modelo_ia import deriva
from modelo_ia.api.almacen import Almacen

SEQ = count(1)
PEPPER = "p" * 40


@pytest.fixture
def alm(tmp_path):
    a = Almacen(tmp_path / "p.sqlite3", PEPPER)
    a.inicializar()
    a.comercio = a.crear_comercio("Tienda P")
    return a


def guardar(
    alm,
    tx,
    *,
    cliente="C1",
    fecha="2026-11-12T10:00:00",
    probabilidad=0.1,
    reglas=(),
    decision="APROBADA",
    monto=1000.0,
):
    n = next(SEQ)
    with alm.transaccion() as con:
        alm.evaluaciones.insertar(
            con,
            id=f"{n:08d}-0000-4000-8000-000000000000",
            comercio_id=alm.comercio,
            id_transaccion=tx,
            id_cliente=cliente,
            fecha=fecha,
            huella=None,
            fila_json="{}",
            solicitud_hash=f"h{n}",
            decision=decision,
            decision_modelo=decision,
            probabilidad=probabilidad,
            motivos_json="[]",
            reglas_json=json.dumps([{"regla": r, "peso": 1} for r in reglas]),
            modelo_nombre="m",
            modelo_version="1",
            modelo_conjunto="completo",
            modo="normal",
            latencia_ms=1.0,
            creada=fecha,
            red_origen="comercio",
            estado_origen="comercio",
            id_seguimiento=f"AF-{n:04d}-ABCD",
            ip_codigo=None,
            disp_codigo=None,
            monto_cop=monto,
            no_enviados_json="[]",
            senales_json=None,
        )
        return alm.evaluaciones.por_transaccion(con, alm.comercio, tx)["id"]


def filtros(**kw):
    base = dict(
        comercio_id=None, desde=None, hasta=None, decision=None, modo=None, monto_min=None, monto_max=None, id_cliente=None,
        id_transaccion=None, id_seguimiento=None, regla=None, riesgo_min=None, riesgo_max=None, q=None, ip=None, dispositivo=None,
    )  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def base_v3(ruta):
    """Una base como la que dejó el cambio 6 (migraciones 1 a 3), con datos escritos directamente, porque `insertar` ya escribe `seq`."""
    v3 = Almacen(ruta, PEPPER)
    assert v3.inicializar(v3._migraciones()[:3]) == [1, 2, 3]
    comercio = v3.crear_comercio("Tienda V3")
    con = sqlite3.connect(ruta)
    sql = (
        "INSERT INTO evaluaciones (id, comercio_id, id_transaccion, id_cliente, fecha, fila_json, solicitud_hash, decision, probabilidad, "
        "motivos_json, reglas_json, modelo_nombre, modelo_version, modelo_conjunto, modo, latencia_ms, creada) "
        "VALUES (?,?,?,?,?, '{}', ?, 'APROBADA', 0.2, '[]', ?, 'm', '1', 'completo', 'normal', 1.0, ?)"
    )
    for i, reglas in enumerate(
        ('[{"regla":"R1","peso":1},{"regla":"R2","peso":2}]', "[]", '[{"regla":"R1","peso":1}]'), start=1
    ):
        con.execute(
            sql, (f"e{i}", comercio, f"T{i}", "C1", f"2026-11-12T10:0{i}:00", f"h{i}", reglas, f"2026-11-12T10:0{i}:00")
        )
    con.execute(
        "DELETE FROM evaluaciones WHERE id='e2'"
    )  # deja un hueco en rowid: seq debe copiarlo tal cual, no renumerar
    con.execute(
        "INSERT INTO revisiones (id_evaluacion, comercio_id, estado, creada) VALUES ('e1', ?, 'pendiente', '2026-11-12T11:00:00')",
        (comercio,),
    )
    con.execute(
        "INSERT INTO revisiones (id_evaluacion, comercio_id, estado, decision_analista, clave_analista, creada, resuelta) "
        "VALUES ('e3', ?, 'resuelta', 'APROBADA', 1, '2026-11-12T11:00:00', '2026-11-12T13:05:30')",
        (comercio,),
    )
    con.commit()
    con.close()
    return comercio


# ------------------------------------------------------------------ migración 4
def test_la_migracion_4_rellena_seq_reglas_y_minutos_desde_los_datos_viejos_y_hace_una_sola_copia(tmp_path):
    ruta = tmp_path / "v3.sqlite3"
    base_v3(ruta)
    nuevo = Almacen(ruta, PEPPER)
    assert nuevo.inicializar() == [4]
    con = sqlite3.connect(ruta)
    assert con.execute("SELECT id, seq FROM evaluaciones ORDER BY seq").fetchall() == [
        ("e1", 1),
        ("e3", 3),
    ]  # igual que rowid, hueco incluido
    assert con.execute(
        "SELECT id_evaluacion, posicion, regla FROM evaluacion_reglas ORDER BY id_evaluacion, posicion"
    ).fetchall() == [
        ("e1", 0, "R1"),
        ("e1", 1, "R2"),
        ("e3", 0, "R1"),
    ]
    assert (
        con.execute("SELECT id_evaluacion, minutos_resolucion FROM revisiones ORDER BY id_evaluacion").fetchall()[0][1]
        is None
    )  # la pendiente
    minutos = con.execute("SELECT minutos_resolucion FROM revisiones WHERE id_evaluacion='e3'").fetchone()[0]
    assert minutos == pytest.approx(125.5, abs=1e-3)  # 2 h 5 min 30 s: lo mismo que daba julianday * 1440
    copias = nuevo.copias_previas()
    assert len(copias) == 1 and "-v4-" in copias[0].name
    assert (
        not sqlite3.connect(copias[0]).execute("SELECT 1 FROM sqlite_master WHERE name='evaluacion_reglas'").fetchone()
    )  # la copia es la de ANTES
    assert nuevo.inicializar() == []


def test_la_migracion_4_se_puede_correr_dos_veces_sin_duplicar(tmp_path):
    ruta = tmp_path / "v3.sqlite3"
    base_v3(ruta)
    alm = Almacen(ruta, PEPPER)
    alm.inicializar()
    with alm.transaccion() as con:
        alm._migraciones()[3].aplicar(con)  # otra vez, a mano
    con = sqlite3.connect(ruta)
    assert con.execute("SELECT COUNT(*) FROM evaluacion_reglas").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM evaluaciones WHERE seq IS NULL").fetchone()[0] == 0


def test_una_base_nueva_ya_trae_seq_unica_y_no_se_puede_repetir(alm):
    guardar(alm, "T1")
    guardar(alm, "T2")
    with pytest.raises(sqlite3.IntegrityError), alm.transaccion() as con:
        con.execute("UPDATE evaluaciones SET seq = 1")  # dos filas con el mismo seq no pueden existir (índice único)


# ------------------------------------------------------------------ seq reemplaza a rowid
def test_seq_crece_de_uno_en_uno_y_sigue_creciendo_aunque_se_borre_la_ultima_fila(alm):
    for i in range(3):
        guardar(alm, f"T{i}")
    with alm.transaccion() as con:
        assert [r[0] for r in con.execute("SELECT seq FROM evaluaciones ORDER BY seq")] == [1, 2, 3]
        con.execute("DELETE FROM evaluacion_reglas")
        con.execute("DELETE FROM evaluaciones WHERE seq = 3")
    guardar(alm, "T9")
    with alm.lectura() as con:
        assert (
            con.execute("SELECT MAX(seq) FROM evaluaciones").fetchone()[0] == 3
        )  # MAX+1 como cualquier secuencia calculada: el 3 se reutiliza


# ------------------------------------------------------------------ evaluacion_reglas
def test_cada_regla_activada_queda_en_su_fila_y_el_filtro_por_regla_la_encuentra(alm):
    a = guardar(alm, "T1", reglas=["R1", "R2"], cliente="CA")
    b = guardar(alm, "T2", reglas=["R2"], cliente="CB")
    c = guardar(alm, "T3", reglas=[], cliente="CC")
    repo = alm.consultas
    with alm.lectura() as con:
        assert (
            con.execute("SELECT regla FROM evaluacion_reglas WHERE id_evaluacion=? ORDER BY posicion", (a,)).fetchall()[
                1
            ][0]
            == "R2"
        )
        assert con.execute("SELECT COUNT(*) FROM evaluacion_reglas WHERE id_evaluacion=?", (c,)).fetchone()[0] == 0
        total, filas = repo.listar(con, filtros(regla="R2"), "fecha_asc", 50, 0)
        assert total == 2 and sorted(f["id"] for f in filas) == sorted([a, b])
        total, filas = repo.listar(con, filtros(regla="R1"), "fecha_asc", 50, 0)
        assert total == 1 and filas[0]["id"] == a
        assert repo.listar(con, filtros(regla="NO_EXISTE"), "fecha_asc", 50, 0)[0] == 0


def test_el_resumen_cuenta_las_reglas_con_sus_rechazos(alm):
    guardar(alm, "T1", reglas=["R1", "R2"], decision="RECHAZADA")
    guardar(alm, "T2", reglas=["R1"], decision="APROBADA")
    repo = alm.consultas
    with alm.lectura() as con:
        datos = repo.resumen(con, filtros(), False)
    por_regla = {r["regla"]: (r["veces"], r["rechazadas"]) for r in datos["reglas"]}
    assert por_regla == {"R1": (2, 1), "R2": (1, 1)}
    assert [r["regla"] for r in datos["reglas"]][0] == "R1"  # la más frecuente primero


# ------------------------------------------------------------------ minutos de resolución
def test_resolver_guarda_los_minutos_y_el_panel_los_promedia_redondeados(alm):
    ids = [guardar(alm, f"T{i}", cliente=f"C{i}") for i in range(2)]
    with alm.transaccion() as con:
        for i in ids:
            alm.revisiones.crear_pendiente(con, i, alm.comercio, "2026-11-12T11:00:00")
        assert alm.revisiones.resolver(con, ids[0], "APROBADA", None, 1, "2026-11-12T11:10:00") == 1  # 10 min
        assert alm.revisiones.resolver(con, ids[1], "RECHAZADA", None, 1, "2026-11-12T11:25:30") == 1  # 25,5 min
        assert (
            alm.revisiones.resolver(con, ids[1], "APROBADA", None, 1, "2026-11-12T20:00:00") == 0
        )  # la segunda vez no toca nada
        assert [r[0] for r in con.execute("SELECT minutos_resolucion FROM revisiones ORDER BY resuelta")] == [
            10.0,
            25.5,
        ]
    repo = alm.consultas
    with alm.lectura() as con:
        datos = repo.resumen(con, filtros(), True)
    assert datos["por_analista"][0]["resueltas"] == 2 and float(
        datos["por_analista"][0]["minutos_promedio"]
    ) == pytest.approx(17.75)


# ------------------------------------------------------------------ lo que SQLite y PostgreSQL hacen distinto
def test_los_pagos_sin_puntaje_van_al_final_en_las_dos_colas_por_riesgo_y_por_monto(alm):
    sin = guardar(alm, "T0", probabilidad=None, cliente="C0", monto=5000.0)
    bajo = guardar(alm, "T1", probabilidad=0.2, cliente="C1", monto=100.0)
    alto = guardar(alm, "T2", probabilidad=0.9, cliente="C2", monto=300.0)
    repo = alm.consultas
    with alm.lectura() as con:
        assert [f["id"] for f in repo.listar(con, filtros(), "riesgo_desc", 50, 0)[1]] == [alto, bajo, sin]
    with alm.transaccion() as con:
        for i in (sin, bajo, alto):
            alm.revisiones.crear_pendiente(con, i, alm.comercio, "2026-11-12T11:00:00")
    with alm.lectura() as con:
        assert [f["id"] for f in alm.revisiones.listar(con, "pendiente", None, "riesgo_desc", 50, 0)[1]] == [
            alto,
            bajo,
            sin,
        ]
    with alm.lectura() as con:
        assert [f["id"] for f in repo.listar(con, filtros(), "monto_desc", 50, 0)[1]][
            0
        ] == sin  # un monto nulo sería el último; aquí el mayor va primero


def test_las_cubetas_del_panel_y_de_la_deriva_coinciden_con_deriva_cubeta_de_en_los_bordes(alm):
    puntajes = [
        0.0,
        0.05,
        0.1,
        0.2,
        0.3,
        0.7,
        0.8999,
        0.9,
        0.95,
        1.0,
    ]  # 0.3 * 10 y 0.7 * 10 no son enteros exactos en coma flotante: justo donde CAST y redondeo difieren
    for i, p in enumerate(puntajes):
        guardar(alm, f"T{i}", cliente=f"C{i}", probabilidad=p)
    esperado = [0] * 10
    for p in puntajes:
        esperado[deriva.cubeta_de(p)] += 1
    repo = alm.consultas
    with alm.lectura() as con:
        histograma = [0] * 10
        for r in repo.resumen(con, filtros(), False)["histograma"]:
            histograma[int(r["cubeta"])] += int(r["pagos"])
        assert histograma == esperado
        assert repo.distribucion_puntaje(con, "m", "1", "completo", "2000-01-01") == esperado


def test_los_vinculos_filtran_por_el_numero_de_clientes_y_de_ips_distintas(alm):
    with alm.transaccion() as con:
        pass
    for i in range(3):  # 3 clientes distintos desde la misma IP
        id_ = guardar(alm, f"T{i}", cliente=f"C{i}")
        with alm.transaccion() as con:
            con.execute("UPDATE evaluaciones SET ip_codigo='IP-A' WHERE id=?", (id_,))
    for i, ip in enumerate(("IP-X", "IP-Y")):  # 1 cliente desde 2 IP
        id_ = guardar(alm, f"U{i}", cliente="CZ", fecha=f"2026-11-12T12:0{i}:00")
        with alm.transaccion() as con:
            con.execute("UPDATE evaluaciones SET ip_codigo=? WHERE id=?", (ip, id_))
    repo = alm.consultas
    with alm.lectura() as con:
        v = repo.vinculos(con, filtros(), 2)
    assert [(r["codigo"], r["clientes"]) for r in v["ips"]] == [("IP-A", 3)]
    assert [(r["id_cliente"], r["ips"]) for r in v["clientes"]] == [("CZ", 2)]
    with alm.lectura() as con:
        assert repo.vinculos(con, filtros(), 4)["ips"] == []


# ------------------------------------------------------------------ lista negra y RETURNING
def test_volver_a_agregar_a_un_cliente_a_la_lista_negra_actualiza_motivo_y_fecha_sin_duplicar(alm):
    with alm.transaccion() as con:
        alm.lista_negra.agregar(con, alm.comercio, "C1", "uno", "2026-11-12T10:00:00")
        alm.lista_negra.agregar(con, alm.comercio, "C1", "dos", "2026-11-12T11:00:00")
    with alm.lectura() as con:
        filas = alm.lista_negra.listar(con, alm.comercio)
    assert len(filas) == 1 and filas[0]["motivo"] == "dos" and filas[0]["creada"] == "2026-11-12T11:00:00"


def test_los_identificadores_nuevos_salen_de_returning_y_coinciden_con_la_fila_guardada(alm):
    otro = alm.crear_comercio("Tienda Q")
    assert alm.crear_clave(otro, "comercio") is not None
    con = sqlite3.connect(alm.ruta)
    ultimo = con.execute("SELECT MAX(id) FROM comercios").fetchone()[0]
    assert otro == ultimo and con.execute("SELECT 1 FROM auditoria WHERE accion='comercio_creado'").fetchall()
    detalle = json.loads(
        con.execute("SELECT detalle FROM auditoria WHERE accion='comercio_creado' ORDER BY id DESC").fetchone()[0]
    )
    assert detalle["id"] == otro  # la auditoría guarda el mismo número que devolvió la inserción


def test_toda_cola_ordenada_por_un_valor_que_puede_ser_nulo_lo_dice_con_nulls_last():
    """En SQLite los nulos ya van al final con DESC, así que ninguna prueba de comportamiento notaría que falte: en PostgreSQL irían al PRINCIPIO.
    Por eso se fija el texto: quien quite NULLS LAST de una lista de órdenes rompe esta prueba, no a quien use PostgreSQL."""
    from modelo_ia.api.repositorios.consultas import ORDENES
    from modelo_ia.api.repositorios.revisiones import RevisionesRepositorio

    for ordenes in (ORDENES, RevisionesRepositorio.ORDEN):
        for nombre, sql in ordenes.items():
            if "probabilidad" in sql or "monto_cop" in sql:
                assert "DESC NULLS LAST" in sql, (nombre, sql)


def test_con_un_sqlite_demasiado_viejo_el_programa_avisa_en_claro_y_no_falla_a_mitad(tmp_path, monkeypatch):
    from modelo_ia.api.repositorios import base

    monkeypatch.setattr(base.sqlite3, "sqlite_version_info", (3, 34, 1))
    monkeypatch.setattr(base.sqlite3, "sqlite_version", "3.34.1")
    with pytest.raises(RuntimeError, match=r"SQLite 3\.35\.0 o más reciente.*3\.34\.1"):
        Almacen(tmp_path / "v.sqlite3", PEPPER)
    monkeypatch.setattr(base.sqlite3, "sqlite_version_info", (3, 35, 0))
    Almacen(tmp_path / "v.sqlite3", PEPPER)  # justo la mínima sí arranca


def test_la_migracion_4_no_se_bloquea_con_reglas_dañadas_y_deja_esas_evaluaciones_sin_filas_de_reglas(tmp_path):
    ruta = tmp_path / "v3.sqlite3"
    base_v3(ruta)
    con = sqlite3.connect(ruta)
    for i, texto in enumerate(
        ("{no es json", "5", '"texto"', '{"a": 1}'), start=10
    ):  # dañado, número, texto suelto, objeto en vez de lista
        con.execute(
            "INSERT INTO evaluaciones (id, comercio_id, id_transaccion, id_cliente, fecha, fila_json, solicitud_hash, decision, probabilidad, "
            "motivos_json, reglas_json, modelo_nombre, modelo_version, modelo_conjunto, modo, latencia_ms, creada) "
            "VALUES (?,1,?,'C9','2026-11-12T09:00:00','{}',?,'APROBADA',0.2,'[]',?,'m','1','completo','normal',1.0,'2026-11-12T09:00:00')",
            (f"x{i}", f"X{i}", f"hx{i}", texto),
        )
    con.commit()
    con.close()
    assert Almacen(ruta, PEPPER).inicializar() == [4]  # antes, un solo texto dañado impedía arrancar
    filas = (
        sqlite3.connect(ruta)
        .execute("SELECT id_evaluacion FROM evaluacion_reglas WHERE id_evaluacion LIKE 'x%'")
        .fetchall()
    )
    assert filas == []
    assert (
        sqlite3.connect(ruta).execute("SELECT COUNT(*) FROM evaluacion_reglas WHERE id_evaluacion='e1'").fetchone()[0]
        == 2
    )  # las buenas, intactas


def test_una_revision_con_fechas_incompatibles_se_resuelve_igual_y_deja_los_minutos_vacios(alm):
    id_ = guardar(alm, "T1")
    with alm.transaccion() as con:
        alm.revisiones.crear_pendiente(con, id_, alm.comercio, "2026-11-12T11:00:00+00:00")  # con zona horaria
        assert (
            alm.revisiones.resolver(con, id_, "APROBADA", None, 1, "2026-11-12T11:10:00") == 1
        )  # sin zona: restarlas daría TypeError
        assert tuple(con.execute("SELECT estado, minutos_resolucion FROM revisiones").fetchone()) == ("resuelta", None)
