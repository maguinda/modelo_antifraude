"""Pruebas de validacion_publica con un conjunto pequeño inventado (no descargan nada)."""
import numpy as np
import pandas as pd

from modelo_ia import validacion_publica as vp


def conjunto(n=3000, semilla=0):
    rng = np.random.default_rng(semilla)
    df = pd.DataFrame({c: rng.normal(size=n) for c in vp.COLUMNAS_V})
    df["V3"] = -np.linspace(0, 3, n) + rng.normal(scale=0.5, size=n)         # V3 baja con el orden, como en el archivo real
    df["monto"] = rng.gamma(2.0, 40.0, size=n)
    p = 1 / (1 + np.exp(-(df["V1"] * 2 + df["V2"] - 6)))
    df["es_fraude"] = (rng.random(n) < p).astype(int)
    return df


def test_partir_es_por_orden_y_sin_traslape():
    p = vp.partir(1000)
    assert (p["entrenamiento"].start, p["entrenamiento"].stop, p["validacion"].stop, p["prueba"].stop) == (0, 600, 800, 1000)
    assert p["entrenamiento"].stop == p["validacion"].start and p["validacion"].stop == p["prueba"].start


def test_el_orden_se_reconoce_cronologico_y_uno_mezclado_no():
    df = conjunto()
    assert vp.ordenado_cronologicamente(df)["plausible"] is True
    mezclado = df.sample(frac=1, random_state=1).reset_index(drop=True)
    assert vp.ordenado_cronologicamente(mezclado)["plausible"] is False


def test_el_umbral_se_elige_con_validacion_y_minimiza_el_costo():
    y = np.array([1, 1, 0, 0, 0, 0]); monto = np.array([100.0, 100, 1, 1, 1, 1]); puntaje = np.array([.9, .6, .5, .3, .2, .1])
    u = vp.elegir_umbral(puntaje, y, monto, falsa_alarma=1.0)
    assert vp.costo(puntaje, y, monto, u, 1.0) == 0.0 and 0.5 < u <= 0.6
    assert vp.costo(puntaje, y, monto, 0.99, 1.0) == 200.0            # sin alertar: se escapan los dos fraudes


def test_evaluar_usa_solo_el_pasado_para_entrenar(monkeypatch):
    df = conjunto(); vistos = {}
    original = vp.modelos.crear_modelo
    def espia(nombre, num, cat):
        m = original(nombre, num, cat); f = m.fit
        m.fit = lambda X, y, f=f: (vistos.update(filas=len(X), ultima=X.index.max()), f(X, y))[1]
        return m
    monkeypatch.setattr(vp.modelos, "crear_modelo", espia)
    r = vp.evaluar(df, "logistica", {k: np.arange(len(df))[s] for k, s in vp.partir(len(df)).items()})
    assert vistos == {"filas": 1800, "ultima": 1799}                    # entrena solo con el primer 60 %
    assert 0 <= r["ranking_ap"] <= 1 and set(r["por_costo_de_falsa_alarma_eur"]) == {"1.0", "5.0", "25.0"}


def test_ejecutar_de_punta_a_punta_con_un_csv(tmp_path, monkeypatch):
    ruta = tmp_path / "pub.csv"
    d = conjunto().rename(columns={"monto": "Amount", "es_fraude": "Class"}); d.to_csv(ruta, index=False)
    r = vp.ejecutar(ruta, ["logistica"])
    assert r["filas"] == 3000 and r["verificacion_del_orden"]["plausible"] and len(r["corte_por_tiempo"]) == len(r["corte_al_azar_comparacion"]) == 1


def test_el_umbral_nunca_se_elige_con_la_parte_de_prueba(monkeypatch):
    df = conjunto(); tamanos = []
    original = vp.elegir_umbral
    monkeypatch.setattr(vp, "elegir_umbral", lambda p, y, m, fa: (tamanos.append(len(y)), original(p, y, m, fa))[1])
    idx = np.arange(len(df))
    vp.evaluar(df, "logistica", {"entrenamiento": idx[:1500], "validacion": idx[1500:2000], "prueba": idx[2000:]})   # validación 500, prueba 1000
    assert set(tamanos) == {500}
