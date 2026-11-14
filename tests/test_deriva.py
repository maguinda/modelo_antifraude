"""La matemática de la deriva (deriva.py) con respuestas conocidas calculadas a mano."""

import math

import pytest

from modelo_ia import deriva


def test_cada_puntaje_cae_en_su_cubeta_y_los_extremos_se_pegan_a_los_bordes():
    casos = {0.0: 0, 0.0999: 0, 0.1: 1, 0.5: 5, 0.8999: 8, 0.9: 9, 0.9999: 9, 1.0: 9, 1.7: 9, -0.3: 0}
    for p, esperada in casos.items():
        assert deriva.cubeta_de(p) == esperada, p


def test_el_histograma_cuenta_e_ignora_vacios_y_nan():
    assert deriva.histograma([0.05, 0.05, 0.15, 0.95, 1.0, None, float("nan")]) == [2, 1, 0, 0, 0, 0, 0, 0, 0, 2]
    assert deriva.histograma([]) == [0] * 10


def test_las_proporciones_suman_uno_y_sin_datos_es_un_error_claro():
    p = deriva.proporciones([2, 1, 0, 0, 0, 0, 0, 0, 0, 2])
    assert p[0] == 0.4 and p[1] == 0.2 and math.isclose(sum(p), 1.0)
    with pytest.raises(ValueError, match="No hay puntajes"):
        deriva.proporciones([0] * 10)


def test_el_psi_de_dos_distribuciones_iguales_es_cero():
    d = [0.5, 0.2, 0.1, 0.05, 0.05, 0.03, 0.03, 0.02, 0.01, 0.01]
    assert deriva.psi(d, d) == pytest.approx(0.0, abs=1e-12)


def test_el_psi_coincide_con_un_calculo_hecho_a_mano():
    # referencia 50/50 en dos cubetas; observada 70/30:  (0,7−0,5)·ln(0,7/0,5) + (0,3−0,5)·ln(0,3/0,5) = 0,2·0,336472 + 0,2·0,510826 = 0,169459
    ref = [0.5, 0.5] + [0.0] * 8
    obs = [0.7, 0.3] + [0.0] * 8
    assert deriva.psi(ref, obs) == pytest.approx(0.2 * math.log(1.4) + 0.2 * math.log(5 / 3), abs=1e-9)
    assert deriva.psi(ref, obs) == pytest.approx(0.169459, abs=1e-6)


def test_el_psi_es_simetrico_y_nunca_negativo_con_distribuciones_al_azar():
    import random

    azar = random.Random(7)
    for _ in range(200):
        a = [azar.random() for _ in range(10)]
        b = [azar.random() for _ in range(10)]
        a, b = [x / sum(a) for x in a], [x / sum(b) for x in b]
        assert deriva.psi(a, b) >= -1e-12 and deriva.psi(a, b) == pytest.approx(deriva.psi(b, a), abs=1e-9)


def test_una_cubeta_vacia_no_rompe_el_logaritmo_y_pesa_mucho():
    ref = [0.1] * 10
    obs = [0.0] + [1 / 9] * 9  # la cubeta 0 quedó vacía
    valor = deriva.psi(ref, obs)
    assert math.isfinite(valor) and valor > 0.1


def test_el_psi_exige_las_mismas_cubetas():
    for a, b in (([0.5, 0.5], [1.0]), ([], [])):
        with pytest.raises(ValueError):
            deriva.psi(a, b)


def test_el_estado_depende_del_umbral_y_de_la_muestra_minima():
    assert deriva.estado(None, 0) == "sin_pagos" and deriva.estado(None, 900) == "sin_referencia"
    assert (
        deriva.estado(0.9, 499) == "muestra_insuficiente"
    )  # con pocos pagos NO se da alerta, aunque el PSI sea enorme
    assert [deriva.estado(v, 500) for v in (0.0, 0.0999, 0.10, 0.2499, 0.25, 3.0)] == [
        "estable",
        "estable",
        "atencion",
        "atencion",
        "alerta",
        "alerta",
    ]
    assert deriva.estado(0.3, 50, minimo=50) == "alerta"
