"""Los repositorios de evaluaciones, revisiones, resultados y lista negra contra una base SQLite real, uno por uno.

Cubren lo que las pruebas del servicio (con falsos) no pueden: que cada consulta devuelva lo correcto. Cada prueba nace de un daño provocado que sobrevivió a todo lo demás."""

import json
import sqlite3
from itertools import count
from types import SimpleNamespace

import pytest

from modelo_ia.api.almacen import Almacen

SEQ = count(1)


@pytest.fixture
def alm(tmp_path):
    a = Almacen(tmp_path / "r.sqlite3", "p" * 40)
    a.inicializar()
    a.comercio = a.crear_comercio("Tienda R")
    a.otro = a.crear_comercio("Tienda O")
    return a


def guardar(alm, tx, cliente, huella, fecha, comercio=None):
    """Una evaluación completa (los valores no importan; importan el cliente, la huella y la fecha)."""
    n = next(SEQ)
    with alm.transaccion() as con:
        alm.evaluaciones.insertar(
            con,
            id=f"{n:08d}-0000-4000-8000-000000000000",
            comercio_id=comercio or alm.comercio,
            id_transaccion=tx,
            id_cliente=cliente,
            fecha=fecha,
            huella=huella,
            fila_json=json.dumps({"id_cliente": cliente, "fecha": fecha, "marca": tx}),
            solicitud_hash=f"h{n}",
            decision="APROBADA",
            decision_modelo="APROBADA",
            probabilidad=0.1,
            motivos_json="[]",
            reglas_json="[]",
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
            monto_cop=1000,
            no_enviados_json="[]",
            senales_json=None,
        )


def lectura(alm, f):
    with alm.lectura() as con:
        return f(con)


# ------------------------------------------------------------------ las marcas de carrera
def test_las_marcas_cambian_cuando_llega_un_pago_del_mismo_cliente_y_cuando_llega_uno_del_mismo_dispositivo(alm):
    guardar(alm, "T0", "C1", "FP-A", "2026-11-12T10:00:00")
    m0 = lectura(alm, lambda con: alm.evaluaciones.marcas(con, alm.comercio, "C1", "FP-A"))
    assert m0[0] > 0 and m0[1] > 0
    guardar(
        alm, "T1", "C2", "FP-A", "2026-11-12T10:01:00"
    )  # OTRO cliente, MISMO dispositivo: la marca del cliente no cambia, la de la huella sí
    m1 = lectura(alm, lambda con: alm.evaluaciones.marcas(con, alm.comercio, "C1", "FP-A"))
    assert m1[0] == m0[0] and m1[1] > m0[1]
    guardar(
        alm, "T2", "C1", "FP-B", "2026-11-12T10:02:00"
    )  # MISMO cliente, otro dispositivo: cambia la del cliente, no la de la huella
    m2 = lectura(alm, lambda con: alm.evaluaciones.marcas(con, alm.comercio, "C1", "FP-A"))
    assert m2[0] > m1[0] and m2[1] == m1[1]
    guardar(alm, "T3", "C9", "FP-Z", "2026-11-12T10:03:00")  # nada que ver con ninguno: no cambia nada
    assert lectura(alm, lambda con: alm.evaluaciones.marcas(con, alm.comercio, "C1", "FP-A")) == m2


def test_las_marcas_sin_huella_o_de_otro_comercio_no_se_mezclan(alm):
    guardar(alm, "T0", "C1", "FP-A", "2026-11-12T10:00:00")
    guardar(alm, "T1", "C1", "FP-A", "2026-11-12T10:01:00", comercio=alm.otro)
    assert (
        lectura(alm, lambda con: alm.evaluaciones.marcas(con, alm.comercio, "C1", None))[1] == 0
    )  # sin huella, esa marca es 0
    solo_mio = lectura(alm, lambda con: alm.evaluaciones.marcas(con, alm.comercio, "C1", "FP-A"))
    otro = lectura(alm, lambda con: alm.evaluaciones.marcas(con, alm.otro, "C1", "FP-A"))
    assert solo_mio != otro and lectura(
        alm, lambda con: alm.evaluaciones.marcas(con, alm.comercio, "NADIE", "FP-NADA")
    ) == (0, 0)


# ------------------------------------------------------------------ el historial
def marcas_de(filas):
    return [f["marca"] for f in filas]


def test_el_historial_del_cliente_trae_las_compras_mas_nuevas_hasta_el_limite_y_de_la_mas_vieja_a_la_mas_nueva(alm):
    for i in range(6):
        guardar(alm, f"T{i}", "C1", None, f"2026-11-12T10:0{i}:00")
    guardar(alm, "OTRO", "C2", None, "2026-11-12T10:30:00")  # de otro cliente: no entra
    guardar(alm, "AJENO", "C1", None, "2026-11-12T10:31:00", comercio=alm.otro)  # de otro comercio: no entra
    h = lectura(alm, lambda con: alm.evaluaciones.previos_cliente(con, alm.comercio, "C1", "2026-11-12T10:04:00", 3))
    assert marcas_de(h) == [
        "T2",
        "T3",
        "T4",
    ]  # las 3 más nuevas hasta ese momento (T5 es posterior), de la más vieja a la más nueva
    assert marcas_de(
        lectura(alm, lambda con: alm.evaluaciones.previos_cliente(con, alm.comercio, "C1", "2026-11-12T10:59:00", 100))
    ) == ["T0", "T1", "T2", "T3", "T4", "T5"]
    assert (
        lectura(alm, lambda con: alm.evaluaciones.previos_cliente(con, alm.comercio, "C1", "2026-11-12T09:00:00", 5))
        == []
    )


def test_el_historial_del_dispositivo_excluye_al_propio_cliente_y_respeta_el_limite(alm):
    for i, cli in enumerate(("C1", "C2", "C3", "C4", "C2")):
        guardar(alm, f"T{i}", cli, "FP-X", f"2026-11-12T10:0{i}:00")
    guardar(alm, "OTRA-HUELLA", "C5", "FP-Y", "2026-11-12T10:20:00")
    h = lectura(
        alm, lambda con: alm.evaluaciones.previos_huella(con, alm.comercio, "FP-X", "C1", "2026-11-12T10:59:00", 100)
    )
    assert marcas_de(h) == ["T1", "T2", "T3", "T4"]  # sin las de C1 (el propio cliente) ni las de otra huella
    assert marcas_de(
        lectura(
            alm, lambda con: alm.evaluaciones.previos_huella(con, alm.comercio, "FP-X", "C1", "2026-11-12T10:59:00", 2)
        )
    ) == ["T3", "T4"]  # las 2 más nuevas


def test_a_igual_fecha_el_historial_desempata_por_orden_de_llegada(alm):
    for i in range(4):
        guardar(alm, f"T{i}", "C1", None, "2026-11-12T10:00:00")
    assert marcas_de(
        lectura(alm, lambda con: alm.evaluaciones.previos_cliente(con, alm.comercio, "C1", "2026-11-12T10:00:00", 2))
    ) == ["T2", "T3"]


# ------------------------------------------------------------------ lista negra
def test_volver_a_agregar_a_un_cliente_actualiza_su_motivo_y_no_lo_duplica(alm):
    with alm.transaccion() as con:
        alm.lista_negra.agregar(con, alm.comercio, "C-MALO", "contracargos", "2026-11-12T10:00:00")
    with alm.transaccion() as con:
        alm.lista_negra.agregar(con, alm.comercio, "C-MALO", "fraude confirmado", "2026-11-12T11:00:00")
        alm.lista_negra.agregar(con, alm.otro, "C-MALO", "otro comercio", "2026-11-12T12:00:00")
    filas = lectura(
        alm,
        lambda con: [(r["id_cliente"], r["motivo"], r["creada"]) for r in alm.lista_negra.listar(con, alm.comercio)],
    )
    assert filas == [
        ("C-MALO", "fraude confirmado", "2026-11-12T11:00:00")
    ]  # una sola fila, con el motivo nuevo y solo la de ESTE comercio
    assert lectura(alm, lambda con: alm.lista_negra.ids_de_cliente(con, alm.comercio, "C-MALO")) == ["C-MALO"]
    assert lectura(alm, lambda con: alm.lista_negra.ids_de_cliente(con, alm.comercio, "C-BUENO")) == []
    with alm.transaccion() as con:
        assert (
            alm.lista_negra.quitar(con, alm.comercio, "C-MALO") == 1
            and alm.lista_negra.quitar(con, alm.comercio, "C-MALO") == 0
        )
    assert lectura(alm, lambda con: alm.lista_negra.ids_de_cliente(con, alm.otro, "C-MALO")) == [
        "C-MALO"
    ]  # y la del otro comercio sigue


# ------------------------------------------------------------------ resultados
def test_un_resultado_guarda_cada_dato_en_su_columna(alm):
    guardar(alm, "T0", "C1", None, "2026-11-12T10:00:00")
    idev = lectura(alm, lambda con: alm.evaluaciones.por_transaccion(con, alm.comercio, "T0")["id"])
    with alm.transaccion() as con:
        alm.resultados.registrar(
            con, idev, alm.comercio, "CONTRACARGO", "2026-11-13T09:00:00", "nota del comercio", "2026-11-13T09:00:05"
        )
    fila = (
        sqlite3.connect(alm.ruta)
        .execute(
            "SELECT resultado, fecha_confirmacion, nota, recibido, comercio_id FROM resultados WHERE id_evaluacion=?",
            (idev,),
        )
        .fetchone()
    )
    assert fila == ("CONTRACARGO", "2026-11-13T09:00:00", "nota del comercio", "2026-11-13T09:00:05", alm.comercio)
    assert lectura(alm, lambda con: alm.resultados.por_evaluacion(con, idev)["resultado"]) == "CONTRACARGO"
    assert lectura(alm, lambda con: alm.resultados.por_evaluacion(con, "no-existe")) is None


FILTROS_VACIOS = dict(
    comercio_id=None,
    desde=None,
    hasta=None,
    decision=None,
    modo=None,
    monto_min=None,
    monto_max=None,
    id_cliente=None,
    id_transaccion=None,
    id_seguimiento=None,
    regla=None,
    ip=None,
    dispositivo=None,
    riesgo_min=None,
    riesgo_max=None,
    q=None,
)


# ------------------------------------------------------------------ revisiones
def test_una_revision_se_resuelve_una_sola_vez_y_la_cola_se_filtra_y_ordena(alm):
    for i, monto in enumerate((100, 300, 200)):
        guardar(alm, f"T{i}", f"C{i}", None, f"2026-11-12T10:0{i}:00")
    with alm.transaccion() as con:
        ids = [alm.evaluaciones.por_transaccion(con, alm.comercio, f"T{i}")["id"] for i in range(3)]
        for i, idev in enumerate(ids):
            alm.revisiones.crear_pendiente(con, idev, alm.comercio, f"2026-11-12T11:0{i}:00")
        assert alm.revisiones.resolver(con, ids[0], "APROBADA", "ok", 1, "2026-11-12T12:00:00") == 1
        assert (
            alm.revisiones.resolver(con, ids[0], "RECHAZADA", None, 1, "2026-11-12T12:05:00") == 0
        )  # la segunda vez no cambia nada
    assert lectura(alm, lambda con: alm.revisiones.existe(con, ids[0])) and not lectura(
        alm, lambda con: alm.revisiones.existe(con, "nada")
    )
    pendientes = lectura(alm, lambda con: alm.revisiones.listar(con, "pendiente", None, "antiguos", 50, 0))
    assert (
        pendientes[0][0] == 2 and [f["id"] for f in pendientes[1]] == ids[1:]
    )  # 2 pendientes, de la más antigua a la más nueva
    resueltas = lectura(alm, lambda con: alm.revisiones.listar(con, "resuelta", None, "recientes", 50, 0))
    assert resueltas[0][0] == 1 and resueltas[1][0]["rev_estado"] == "resuelta" and resueltas[1][0]["id"] == ids[0]
    paginada = lectura(
        alm,
        lambda con: alm.revisiones.listar(
            con, "pendiente", SimpleNamespace(**dict(FILTROS_VACIOS, id_cliente="C2")), "antiguos", 50, 0
        ),
    )
    assert paginada[0][0] == 1 and paginada[1][0]["id"] == ids[2]
    assert lectura(alm, lambda con: alm.revisiones.por_evaluacion(con, ids[0]))["decision_analista"] == "APROBADA"
    with pytest.raises(KeyError):
        lectura(
            alm, lambda con: alm.revisiones.listar(con, "pendiente", None, "orden_inventado", 50, 0)
        )  # el orden sale de una lista fija
