import numpy as np
import pandas as pd
import pytest

from modelo_ia import comparar_h1, config, entrenar, evaluacion, predecir, variables
from modelo_ia.reglas import MotorReglas, Regla
from modelo_ia.reglas import reglas_base
from modelo_ia.reglas.seguridad import construir_reglas_seguridad


def pago(**cambios):
    """Un pago normal; cada prueba cambia solo lo que necesita."""
    base = {"monto_cop": 200_000, "transacciones_ultimas_24h": 1, "hora": 15, "es_madrugada": 0,
            "es_fin_semana": 0, "minutos_desde_anterior": 9000, "ciudad_distinta_anterior": 0,
            "n_compras_previas": 10, "monto_vs_promedio": 1.0, "dispositivo_nuevo_cliente": 0,
            "dispositivo": "iPhone", "localizacion": "Bogotá, Colombia", "metodo_pago": "Nequi",
            "canal_pago": "App móvil", "tipo_ip": "Móvil", "estado_dispositivo": "Conocido",
            "categoria": "Ropa", "id_cliente": "CLI-1"}
    base.update(cambios)
    return base


def decision_ingenua(**cambios):
    return reglas_base.motor_ingenuo().explicar(pago(**cambios))["decision"]


# ---------------------------------------------------------------- motor
def test_pago_normal_se_aprueba_con_las_reglas_ingenuas():
    assert decision_ingenua() == "APROBAR"


@pytest.mark.parametrize("cambios,esperada", [
    ({"monto_cop": 1_500_000}, "REVISAR"),
    ({"monto_cop": 1_499_999}, "APROBAR"),
    ({"transacciones_ultimas_24h": 5}, "REVISAR"),
    ({"transacciones_ultimas_24h": 4}, "APROBAR"),
    ({"tipo_ip": "VPN"}, "REVISAR"),
    ({"tipo_ip": "Proxy"}, "REVISAR"),
    ({"tipo_ip": "Residencial"}, "APROBAR"),
    ({"estado_dispositivo": "No reconocido"}, "BLOQUEAR"),
    ({"estado_dispositivo": "Nuevo"}, "APROBAR"),
    ({"ciudad_distinta_anterior": 1, "minutos_desde_anterior": 60}, "BLOQUEAR"),
    ({"ciudad_distinta_anterior": 1, "minutos_desde_anterior": 61}, "APROBAR"),
    ({"n_compras_previas": 0, "monto_cop": 1_000_000}, "REVISAR"),
    ({"hora": 3, "monto_cop": 1_000_000}, "REVISAR"),
    ({"hora": 3, "monto_cop": 999_999}, "APROBAR"),
])
def test_cada_regla_ingenua_se_activa_en_su_umbral(cambios, esperada):
    assert decision_ingenua(**cambios) == esperada


def test_gana_la_regla_mas_severa():
    assert decision_ingenua(monto_cop=2_000_000, estado_dispositivo="No reconocido") == "BLOQUEAR"


def test_el_motor_dice_que_reglas_se_activaron():
    r = reglas_base.motor_ingenuo().explicar(pago(monto_cop=2_000_000, tipo_ip="VPN"))
    assert {x["regla"] for x in r["reglas_activadas"]} == {"monto_alto", "ip_anonima"}
    assert all(x["motivo"] for x in r["reglas_activadas"])


def test_regla_sin_su_columna_no_se_aplica_y_no_falla():
    p = pago()
    del p["tipo_ip"]
    assert reglas_base.motor_ingenuo().explicar(p)["decision"] == "APROBAR"


def test_regla_con_accion_invalida():
    with pytest.raises(ValueError):
        Regla("x", (), lambda d: d.index == 0, "ELIMINAR", "")


def test_reglas_con_nombre_repetido():
    r = Regla("x", ("monto_cop",), lambda d: d["monto_cop"] > 0, "REVISAR", "")
    with pytest.raises(ValueError, match="mismo nombre"):
        MotorReglas([r, r])


def test_severidad_vectorizada_coincide_con_pago_a_pago():
    filas = [pago(), pago(monto_cop=3_000_000), pago(estado_dispositivo="No reconocido"), pago(tipo_ip="Proxy")]
    motor = reglas_base.motor_ingenuo()
    en_bloque = list(motor.decidir(pd.DataFrame(filas)))
    uno_a_uno = [motor.explicar(f)["decision"] for f in filas]
    assert en_bloque == uno_a_uno == ["APROBAR", "REVISAR", "BLOQUEAR", "REVISAR"]


def test_las_reglas_de_la_linea_base_no_usan_columnas_prohibidas():
    columnas = {c for r in reglas_base.construir_reglas(reglas_base.PARAMS_INGENUOS) for c in r.requiere}
    assert not columnas & set(config.COLUMNAS_PROHIBIDAS)


def test_valor_none_apaga_la_regla():
    params = dict(reglas_base.PARAMS_INGENUOS, monto_alto=None, accion_ip_anonima="NADA")
    nombres = {r.nombre for r in reglas_base.construir_reglas(params)}
    assert "monto_alto" not in nombres and "ip_anonima" not in nombres


# ---------------------------------------------------------------- ajuste
@pytest.fixture
def partes(ruta_csv):
    df, m = entrenar.preparar_datos(ruta_csv, "historial")
    return df, m


def test_ajustar_nunca_queda_peor_que_las_ingenuas_en_validacion(partes):
    df, m = partes
    _, inicial, final = reglas_base.ajustar(df.loc[m["validacion"]])
    assert final <= inicial


def test_ajustar_no_depende_de_la_prueba(partes):
    df, m = partes
    v = df.loc[m["validacion"]]
    antes, _, _ = reglas_base.ajustar(v)
    otro = df.copy()
    idx = otro.index[m["prueba"]]
    otro.loc[idx, "es_fraude"] = otro.loc[idx, "es_fraude"].sample(frac=1, random_state=3).values
    despues, _, _ = reglas_base.ajustar(otro.loc[m["validacion"]])
    assert antes == despues


def test_ajustar_baja_el_costo_de_verdad_con_umbrales_malos(partes):
    df, m = partes
    malos = dict(reglas_base.PARAMS_INGENUOS, monto_alto=500_000, compras_24h=3)      # alertan casi todo
    _, inicial, final = reglas_base.ajustar(df.loc[m["validacion"]], inicio=malos)
    assert final < inicial


# ---------------------------------------------------------------- red de seguridad
def decision_seguridad(lista_negra=(), **cambios):
    return MotorReglas(construir_reglas_seguridad(lista_negra)).explicar(pago(**cambios))["decision"]


def test_seguridad_pago_normal_pasa():
    assert decision_seguridad() == "APROBAR"


def test_seguridad_lista_negra_bloquea():
    assert decision_seguridad(lista_negra=["CLI-1"]) == "BLOQUEAR"
    assert decision_seguridad(lista_negra=["CLI-2"]) == "APROBAR"


def test_seguridad_dispositivo_no_reconocido_con_ip_anonima_bloquea():
    assert decision_seguridad(estado_dispositivo="No reconocido", tipo_ip="VPN") == "BLOQUEAR"
    assert decision_seguridad(estado_dispositivo="No reconocido", tipo_ip="Residencial") == "APROBAR"


def test_seguridad_monto_muy_alto_nunca_se_aprueba_solo():
    assert decision_seguridad(monto_cop=10_000_000) == "REVISAR"


def test_seguridad_rafaga_y_viaje_imposible_bloquean():
    assert decision_seguridad(transacciones_ultimas_24h=15) == "BLOQUEAR"
    assert decision_seguridad(ciudad_distinta_anterior=1, minutos_desde_anterior=20) == "BLOQUEAR"
    assert decision_seguridad(ciudad_distinta_anterior=1, minutos_desde_anterior=200) == "APROBAR"


# ---------------------------------------------------------------- decisión del servicio
@pytest.fixture
def artefacto(partes, ruta_csv, carpeta_temporal):
    df, m = partes
    r = entrenar.ajustar_y_evaluar(df, m, "logistica", "historial")
    entrenar.guardar(r, ruta_csv, como_actual=True)
    return predecir.cargar_artefacto()


def test_la_red_de_seguridad_sube_la_decision_del_modelo(artefacto):
    """Un pago que el modelo aprueba pero la política exige revisar: gana la política."""
    p = pago(monto_cop=12_000_000, tipo_ip="Empresarial")
    sin = predecir.analizar_transaccion(p, artefacto, usar_reglas_seguridad=False)
    con = predecir.analizar_transaccion(p, artefacto)
    assert con["decision_modelo"] == sin["decision"]
    assert {"APROBAR": 0, "REVISAR": 1, "BLOQUEAR": 2}[con["decision"]] >= {"APROBAR": 0, "REVISAR": 1, "BLOQUEAR": 2}[sin["decision"]]
    assert [x["regla"] for x in con["reglas_activadas"]] == ["monto_muy_alto"]
    assert con["decision"] in ("REVISAR", "BLOQUEAR")


def test_la_lista_negra_bloquea_aunque_el_modelo_apruebe(artefacto):
    normal = pago()
    assert predecir.analizar_transaccion(normal, artefacto)["decision"] == "APROBAR"
    r = predecir.analizar_transaccion(normal, artefacto, lista_negra=["CLI-1"])
    assert r["decision"] == "BLOQUEAR" and r["decision_modelo"] == "APROBAR"


def test_la_red_nunca_baja_la_decision(artefacto):
    sospechoso = pago(tipo_ip="Proxy", estado_dispositivo="No reconocido", monto_cop=5_000_000,
                      transacciones_ultimas_24h=9, hora=3)
    r = predecir.analizar_transaccion(sospechoso, artefacto)
    assert r["decision"] == "BLOQUEAR"


# ---------------------------------------------------------------- bootstrap y H1
def test_bootstrap_con_metodos_iguales_incluye_el_cero():
    rng = np.random.default_rng(0)
    y = rng.random(4000) < 0.1
    alerta = (rng.random(4000) < 0.3) | y
    b = comparar_h1.bootstrap_diferencias(alerta, alerta, y, repeticiones=200)
    assert b["dif_recall"] == 0 and b["ic95_recall"] == [0.0, 0.0]


def test_bootstrap_detecta_un_metodo_claramente_mejor():
    rng = np.random.default_rng(1)
    y = rng.random(6000) < 0.1
    bueno = y.copy()                                        # detecta todo, sin falsas alarmas
    malo = rng.random(6000) < 0.1                           # al azar
    b = comparar_h1.bootstrap_diferencias(bueno, malo, y, repeticiones=300)
    assert b["ic95_recall"][0] > 0.5
    assert b["ic95_fpr"][1] <= 0
    assert "H1 apoyada" in comparar_h1.veredicto(b)


def test_veredicto_no_afirma_h1_si_el_intervalo_cruza_el_cero():
    b = {"ic95_recall": [-0.02, 0.05], "ic95_fpr": [-0.01, 0.01]}
    assert "no se puede afirmar" in comparar_h1.veredicto(b)


def test_umbral_igual_fpr_respeta_el_limite():
    rng = np.random.default_rng(2)
    y = rng.random(5000) < 0.1
    p = np.clip(rng.normal(0.3, 0.2, 5000) + y * 0.4, 0, 1)
    u = comparar_h1.umbral_igual_fpr(p, y, 0.05)
    assert comparar_h1.tasas(p >= u, y)["fpr"] <= 0.05


def test_h1_de_punta_a_punta_con_datos_pequenos(ruta_csv, carpeta_temporal, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVO_H1", carpeta_temporal / "resultados" / "h1.json")
    r = comparar_h1.ejecutar(ruta_csv, repeticiones=50)
    assert len(r["tabla"]) == 5 and config.ARCHIVO_H1.exists()
    assert set(r["salida"]["comparaciones"]) == {"modelo_igual_fpr_vs_reglas_ajustadas",
                                                 "modelo_igual_fpr_vs_reglas_ingenuas",
                                                 "modelo_costo_vs_reglas_ajustadas"}


def test_h1_ajusta_las_reglas_con_validacion_no_con_la_prueba(ruta_csv, carpeta_temporal, monkeypatch, tmp_path):
    """Si se barajan las etiquetas de la prueba, los umbrales ajustados no pueden cambiar."""
    monkeypatch.setattr(config, "ARCHIVO_H1", carpeta_temporal / "resultados" / "h1.json")
    original = comparar_h1.ejecutar(ruta_csv, repeticiones=20, registrar=False)["salida"]["parametros_reglas_ajustadas"]
    df = pd.read_csv(ruta_csv, encoding="utf-8-sig")
    en_prueba = pd.to_datetime(df["fecha_pago"]).dt.month >= 9
    df.loc[en_prueba, "es_fraude"] = df.loc[en_prueba, "es_fraude"].sample(frac=1, random_state=5).values
    otra = tmp_path / "barajado.csv"
    df.to_csv(otra, index=False, encoding="utf-8-sig")
    barajado = comparar_h1.ejecutar(otra, repeticiones=20, registrar=False)["salida"]["parametros_reglas_ajustadas"]
    assert original == barajado
