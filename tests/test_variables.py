import pandas as pd
import pytest

from modelo_ia import config, variables


def _cliente(montos_y_ciudades_y_minutos):
    """Un cliente con compras en los minutos indicados (desde 2026-03-01 10:00)."""
    base = pd.Timestamp("2026-03-01 10:00:00")
    filas = []
    for monto, ciudad, minuto, dispositivo in montos_y_ciudades_y_minutos:
        filas.append({"id_cliente": "CLI-1", "fecha": base + pd.Timedelta(minutes=minuto),
                      "localizacion": ciudad, "monto_cop": monto, "dispositivo": dispositivo})
    return pd.DataFrame(filas)


COMPRAS = [(100_000, "Bogotá", 0, "iPhone"),
           (300_000, "Cali", 10, "Android"),
           (3_000_000, "Cali", 500, "Android")]


def test_historial_calculado_a_mano():
    d = variables.agregar_historial(_cliente(COMPRAS))
    assert list(d["n_compras_previas"]) == [0, 1, 2]
    assert list(d["ciudad_distinta_anterior"]) == [0, 1, 0]
    assert list(d["dispositivo_nuevo_cliente"]) == [0, 1, 0]
    assert d["minutos_desde_anterior"].iloc[1] == pytest.approx(10)
    assert d["minutos_desde_anterior"].iloc[0] == config.TOPE_MINUTOS_HISTORIAL
    assert d["monto_vs_promedio"].iloc[1] == pytest.approx(3.0)             # 300k frente a 100k
    assert d["monto_vs_promedio"].iloc[2] == pytest.approx(3_000_000 / 200_000)   # promedio de 100k y 300k


def test_monto_vs_promedio_tiene_tope_de_20():
    d = variables.agregar_historial(_cliente([(100_000, "Bogotá", 0, "iPhone"),
                                              (50_000_000, "Bogotá", 5, "iPhone")]))
    assert d["monto_vs_promedio"].iloc[1] == 20


def test_historial_no_usa_el_futuro():
    """Si cambia una compra POSTERIOR, las filas anteriores no pueden cambiar."""
    original = variables.agregar_historial(_cliente(COMPRAS))
    alterada = _cliente(COMPRAS[:2] + [(999_999_999, "Medellín", 500, "Windows PC")])
    alterada = variables.agregar_historial(alterada)
    columnas = config.NUMERICAS_HISTORIAL
    pd.testing.assert_frame_equal(original[columnas].iloc[:2], alterada[columnas].iloc[:2])


def test_historial_separa_a_los_clientes():
    a = _cliente(COMPRAS)
    b = _cliente([(50_000, "Bogotá", 0, "iPhone")]).assign(id_cliente="CLI-2")
    d = variables.agregar_historial(pd.concat([a, b], ignore_index=True))
    assert d.loc[d["id_cliente"] == "CLI-2", "n_compras_previas"].tolist() == [0]


def test_historial_conserva_el_orden_de_entrada():
    d = _cliente(COMPRAS).iloc[::-1]                    # llega desordenado
    r = variables.agregar_historial(d)
    assert list(r.index) == list(d.index)
    assert r.loc[0, "n_compras_previas"] == 0           # la compra de las 10:00 sigue siendo la primera


def test_columnas_de_conjunto_desconocido():
    with pytest.raises(ValueError, match="desconocido"):
        variables.columnas_de("inventado")


def test_columnas_prohibidas_no_pueden_ser_entrada(monkeypatch):
    monkeypatch.setattr(config, "CATEGORICAS", config.CATEGORICAS + ["nivel_riesgo"])
    with pytest.raises(ValueError, match="no pueden ser variables de entrada"):
        variables.columnas_de("base")


@pytest.mark.parametrize("conjunto", list(config.CONJUNTOS_VARIABLES))
def test_ningun_conjunto_usa_columnas_prohibidas(conjunto):
    num, cat = variables.columnas_de(conjunto)
    assert not set(num + cat) & set(config.COLUMNAS_PROHIBIDAS)
