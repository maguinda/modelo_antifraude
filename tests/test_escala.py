"""Cambio de escala (medido con 3 millones de pagos): índices de búsqueda, estadísticas de SQLite y el filtro por regla sin EXISTS correlacionado."""

import sqlite3
from types import SimpleNamespace

import pytest

from modelo_ia.api.almacen import Almacen
from modelo_ia.api.repositorios.esquema import Esquema
from modelo_ia.api.repositorios.filtros_sql import donde_de

pytestmark = pytest.mark.solo_sqlite


@pytest.fixture
def con(tmp_path):
    a = Almacen(tmp_path / "e.sqlite3", "p" * 40)
    a.inicializar()
    c = sqlite3.connect(tmp_path / "e.sqlite3")
    yield c, a
    c.close()


def _plan(c, sql, par):
    return " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, par))


def test_la_migracion_6_crea_los_dos_indices_de_busqueda(con):
    c, _ = con
    nombres = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_eval_cliente_global", "ix_eval_transaccion_global"} <= nombres


def test_buscar_por_cliente_o_transaccion_sin_comercio_usa_indices_y_no_recorre_la_tabla(con):
    c, _ = con
    f = SimpleNamespace(
        **{
            k: None
            for k in (
                "comercio_id",
                "desde",
                "hasta",
                "decision",
                "modo",
                "monto_min",
                "monto_max",
                "id_cliente",
                "id_transaccion",
                "id_seguimiento",
                "regla",
                "riesgo_min",
                "riesgo_max",
                "q",
                "ip",
                "dispositivo",
            )
        }
    )
    f.id_cliente = "C1"
    donde, par = donde_de(f)
    plan = _plan(c, f"SELECT COUNT(*) FROM evaluaciones e {donde}", par)
    assert "ix_eval_cliente_global" in plan


def test_el_filtro_por_regla_no_es_correlacionado(con):
    c, _ = con
    f = SimpleNamespace(
        **{
            k: None
            for k in (
                "comercio_id",
                "desde",
                "hasta",
                "decision",
                "modo",
                "monto_min",
                "monto_max",
                "id_cliente",
                "id_transaccion",
                "id_seguimiento",
                "regla",
                "riesgo_min",
                "riesgo_max",
                "q",
                "ip",
                "dispositivo",
            )
        }
    )
    f.regla = "velocidad"
    donde, par = donde_de(f)
    assert "CORRELATED" not in _plan(c, f"SELECT COUNT(*) FROM evaluaciones e {donde}", par)


def test_analizar_solo_cuando_la_tabla_es_grande_y_no_hay_estadisticas_o_cambio_mucho(con):
    c, a = con
    assert Esquema.analizar_si_hace_falta(c, minimo_filas=3) is False  # tabla vacía: nada que analizar
    for i in range(1, 5):
        c.execute(
            "INSERT INTO evaluaciones (id, seq, comercio_id, id_transaccion, id_cliente, fecha, fila_json, solicitud_hash, decision, motivos_json, reglas_json, modelo_nombre, "
            "modelo_version, modelo_conjunto, modo, latencia_ms, creada) VALUES (?, ?, 1, ?, 'C', '2026-01-01T00:00:00', '{}', 'h', 'APROBADA', '[]', '[]', 'm', '1', 'c', 'normal', 1, '2026-01-01T00:00:00')",
            (f"id{i}", i, f"T{i}"),
        )
    c.commit()
    assert Esquema.analizar_si_hace_falta(c, minimo_filas=3) is True
    assert c.execute("SELECT COUNT(*) FROM sqlite_stat1").fetchone()[0] > 0
    assert Esquema.analizar_si_hace_falta(c, minimo_filas=3) is False  # ya tiene estadísticas de un tamaño parecido
    assert Esquema.analizar_si_hace_falta(c, minimo_filas=10_000) is False  # tabla pequeña


def _guardar(c, i, cliente, comercio=1, reglas=()):
    c.execute(
        "INSERT INTO evaluaciones (id, seq, comercio_id, id_transaccion, id_cliente, fecha, fila_json, solicitud_hash, decision, motivos_json, reglas_json, modelo_nombre, "
        "modelo_version, modelo_conjunto, modo, latencia_ms, creada) VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00', '{}', 'h', 'APROBADA', '[]', '[]', 'm', '1', 'c', 'normal', 1, '2026-01-01T00:00:00')",
        (f"id{i}", i, comercio, f"T{i}", cliente),
    )
    for pos, r in enumerate(reglas):
        c.execute("INSERT INTO evaluacion_reglas (id_evaluacion, posicion, regla) VALUES (?, ?, ?)", (f"id{i}", pos, r))


def test_el_filtro_por_regla_da_las_mismas_filas_que_el_exists_antiguo_incluso_con_la_regla_repetida(con):
    c, _ = con
    _guardar(c, 1, "A", reglas=("velocidad", "velocidad", "monto"))  # la misma regla dos veces en un pago
    _guardar(c, 2, "B", reglas=("monto",))
    _guardar(c, 3, "C")
    nuevo = "SELECT e.id FROM evaluaciones e WHERE e.id IN (SELECT id_evaluacion FROM evaluacion_reglas WHERE regla = ?) ORDER BY e.id"
    viejo = "SELECT e.id FROM evaluaciones e WHERE EXISTS (SELECT 1 FROM evaluacion_reglas er WHERE er.id_evaluacion = e.id AND er.regla = ?) ORDER BY e.id"
    for regla, esperado in (("velocidad", ["id1"]), ("monto", ["id1", "id2"]), ("no_existe", [])):
        assert [r[0] for r in c.execute(nuevo, (regla,))] == [r[0] for r in c.execute(viejo, (regla,))] == esperado


def test_el_conteo_por_comercio_da_cero_al_comercio_sin_pagos(con):
    c, a = con
    vacio = a.crear_comercio("Sin pagos")
    _guardar(c, 1, "A", comercio=a.crear_comercio("Con pagos"))
    c.commit()
    filas = dict(
        c.execute(
            "SELECT c.id, COUNT(e.comercio_id) FROM comercios c LEFT JOIN evaluaciones e ON e.comercio_id = c.id GROUP BY c.id"
        ).fetchall()
    )
    assert filas[vacio] == 0 and sorted(filas.values())[-1] == 1


def test_analizar_otra_vez_si_la_tabla_crece_o_se_achica_a_mas_del_doble_o_si_se_fuerza(con):
    c, _ = con
    for i in range(1, 5):
        _guardar(c, i, "A")
    c.commit()
    assert Esquema.analizar_si_hace_falta(c, minimo_filas=3) is True
    for i in range(5, 10):  # 9 filas > el doble de las 4 analizadas
        _guardar(c, i, "A")
    c.commit()
    assert Esquema.analizar_si_hace_falta(c, minimo_filas=3) is True
    assert Esquema.analizar_si_hace_falta(c, minimo_filas=3) is False
    assert Esquema.analizar_si_hace_falta(c, minimo_filas=3, forzar=True) is True  # índices nuevos: se fuerza
    assert (
        Esquema.analizar_si_hace_falta(c, minimo_filas=100, forzar=True) is False
    )  # pero una tabla pequeña no se analiza
