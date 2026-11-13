import numpy as np
import pandas as pd
import pytest

from modelo_ia import evaluacion


def test_costo_total_calculado_a_mano():
    puntaje = [0.9, 0.2, 0.8, 0.1]
    reales = [1, 1, 0, 0]
    montos = [1_000_000, 500_000, 200_000, 100_000]
    # umbral 0,5: alerta en la 1 (fraude detectado) y en la 3 (falsa alarma); se escapa la 2 (500.000)
    assert evaluacion.costo_total(puntaje, reales, montos, 0.5, costo_falsa_alarma=15_000) == 500_000 + 15_000


def test_elegir_umbral_toma_el_de_menor_costo():
    puntaje = np.array([0.95, 0.85, 0.40, 0.30, 0.20, 0.10])
    reales = np.array([1, 1, 1, 0, 0, 0])
    montos = np.array([1_000_000] * 6)
    umbral, costo = evaluacion.elegir_umbral(puntaje, reales, montos, umbrales=[0.25, 0.5, 0.9])
    assert umbral == 0.25 and costo == 15_000      # atrapa los 3 fraudes y falla en 1 compra buena


def test_metricas_cuenta_bien():
    m = evaluacion.metricas([0.9, 0.2, 0.8, 0.1], [1, 1, 0, 0], [1, 1, 1, 1], 0.5)
    assert m["recall"] == 0.5 and m["precision"] == 0.5
    assert m["fraudes_que_escapan"] == 1 and m["falsas_alarmas"] == 1


def test_recall_por_patron():
    df = pd.DataFrame({"es_fraude": [1, 1, 1, 0], "patron": ["a", "a", "b", "normal"]})
    r = evaluacion.recall_por_patron(df, [0.9, 0.1, 0.9, 0.9], 0.5)
    assert r["a"] == 0.5 and r["b"] == 1.0 and "normal" not in r.index
