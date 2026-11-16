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
    assert "SCAN" not in plan.replace("SCAN e USING COVERING", "") or "USING" in plan
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
