import numpy as np
import pandas as pd
import pytest

from modelo_ia import comparar_variables, config, datos, entrenar, etiquetas, reentrenamiento, variables


def _t(minutos):
    return pd.Timestamp("2026-03-01 10:00:00") + pd.Timedelta(minutes=minutos)


def _con_historial(filas):
    d = pd.DataFrame(filas)
    d["hora"] = d["fecha"].dt.hour
    for c, v in [("localizacion", "Bogotá, Colombia"), ("monto_cop", 100_000), ("dispositivo", "iPhone")]:
        if c not in d:
            d[c] = v
    return variables.agregar_historial(d)


# ---------------------------------------------------------------- perfil
PERFIL = [
    {"id_cliente": "C", "fecha": _t(0), "tipo_ip": "Móvil", "hora_forzada": 10},
    {"id_cliente": "C", "fecha": _t(10), "tipo_ip": "Móvil", "hora_forzada": 11},
    {"id_cliente": "C", "fecha": _t(20), "tipo_ip": "VPN", "hora_forzada": 3},
    {"id_cliente": "C", "fecha": _t(30), "tipo_ip": "Móvil", "hora_forzada": 10},
]


def _perfil(filas):
    d = pd.DataFrame(filas)
    d["localizacion"], d["monto_cop"], d["dispositivo"] = "Bogotá, Colombia", 100_000, "iPhone"
    d["hora"] = d.pop("hora_forzada")
    return variables.agregar_perfil(variables.agregar_historial(d))


def test_perfil_calculado_a_mano():
    d = _perfil(PERFIL)
    assert list(d["ip_frecuencia_previa"].round(3)) == [0.5, 1.0, 0.0, 0.667]
    assert list(d["franja_frecuencia_previa"].round(3)) == [0.5, 1.0, 0.0, 0.667]


def test_perfil_no_usa_el_futuro():
    original = _perfil(PERFIL)
    alterado = _perfil(PERFIL[:3] + [dict(PERFIL[3], tipo_ip="Proxy", hora_forzada=23)])
    cols = config.NUMERICAS_PERFIL
    pd.testing.assert_frame_equal(original[cols].iloc[:3], alterado[cols].iloc[:3])


# ---------------------------------------------------------------- huella
def _huella(filas):
    d = pd.DataFrame(filas)
    d["fecha"] = [_t(i) for i in range(len(d))]
    return variables.agregar_huella(d)


HUELLAS = [
    {"huella_dispositivo": "F1", "id_cliente": "A", "dispositivo": "iPhone", "sistema_operativo": "iOS", "navegador": "Safari", "zona_horaria_dispositivo": "America/Bogota", "es_emulador": 0},
    {"huella_dispositivo": "F1", "id_cliente": "A", "dispositivo": "iPhone", "sistema_operativo": "iOS", "navegador": "Safari", "zona_horaria_dispositivo": "America/Bogota", "es_emulador": 0},
    {"huella_dispositivo": "F1", "id_cliente": "B", "dispositivo": "Tablet Android", "sistema_operativo": "Android", "navegador": "Chrome", "zona_horaria_dispositivo": "America/Bogota", "es_emulador": 1},
    {"huella_dispositivo": "F1", "id_cliente": "C", "dispositivo": "iPhone", "sistema_operativo": "Linux", "navegador": "Chrome headless", "zona_horaria_dispositivo": "Europe/Moscow", "es_emulador": 0},
    {"huella_dispositivo": "F2", "id_cliente": "A", "dispositivo": "Windows PC", "sistema_operativo": "Windows", "navegador": "Edge", "zona_horaria_dispositivo": "America/Bogota", "es_emulador": 0},
]


def test_huella_cuenta_clientes_distintos_a_mano():
    d = _huella(HUELLAS)
    assert list(d["huella_clientes_distintos"]) == [1, 1, 2, 3, 1]
    assert list(d["huella_compras_previas"]) == [0, 1, 2, 3, 0]


def test_huella_banderas_a_mano():
    d = _huella(HUELLAS)
    assert list(d["so_distinto_del_dispositivo"]) == [0, 0, 0, 1, 0]      # Tablet Android/Android coincide
    assert list(d["navegador_headless"]) == [0, 0, 0, 1, 0]
    assert list(d["zona_horaria_no_colombia"]) == [0, 0, 0, 1, 0]
    assert list(d["es_emulador"]) == [0, 0, 1, 0, 0]


def test_huella_no_usa_el_futuro():
    original = _huella(HUELLAS)
    alterada = _huella(HUELLAS[:3] + [dict(HUELLAS[3], id_cliente="Z"), dict(HUELLAS[3], id_cliente="Y")])
    cols = ["huella_clientes_distintos", "huella_compras_previas"]
    pd.testing.assert_frame_equal(original[cols].iloc[:3], alterada[cols].iloc[:3])


# ---------------------------------------------------------------- geo / identidad / comportamiento
def test_geo_ip_a_mano():
    d = pd.DataFrame({"ip_pais": ["Colombia", "Colombia", "Estados Unidos", "Estados Unidos", "Desconocido"],
                      "zona_horaria_dispositivo": ["America/Bogota", "Europe/Moscow", "America/New_York", "America/Bogota", "Europe/Moscow"],
                      "distancia_ip_ciudad_km": [5, 5, 4000, 4000, np.nan], "ip_es_datacenter": [0, 0, 0, 0, 1], "ip_reputacion": [90] * 5})
    r = variables.agregar_geo_ip(d)
    assert list(r["zona_horaria_no_coincide_ip"]) == [0, 1, 0, 1, 0]
    assert list(r["ip_fuera_de_colombia"]) == [0, 0, 1, 1, 0]
    assert list(r["ip_sin_ubicacion"]) == [0, 0, 0, 0, 1]
    assert r["distancia_ip_ciudad_km"].iloc[4] == 0


def test_identidad_cuenta_nueva_hasta_7_dias():
    d = pd.DataFrame({"edad_cuenta_dias": [3, 7, 8], "cambio_credenciales_7d": [0, 1, 0]})
    assert list(variables.agregar_identidad(d)["cuenta_nueva"]) == [1, 1, 0]


def test_comportamiento_rellena_con_neutro_y_marca_lo_no_medido():
    d = pd.DataFrame({"tiempo_formulario_seg": [30.0, np.nan], "velocidad_tecleo_ms": [100.0, np.nan],
                      "pego_numero_tarjeta": [1.0, np.nan], "mouse_natural": [0.1, np.nan]})
    r = variables.agregar_comportamiento(d)
    assert list(r["sin_medicion_comportamiento"]) == [0, 1] and list(r["sin_medicion_mouse"]) == [0, 1]
    assert r["tiempo_formulario_seg"].iloc[1] == 45.0 and r["mouse_natural"].iloc[1] == 0.8
    assert r["tiempo_formulario_seg"].iloc[0] == 30.0 and not r.isna().any().any()


# ---------------------------------------------------------------- conjuntos
def test_construir_avisa_si_faltan_columnas(df_crudo):
    d = datos.agregar_columnas_basicas(df_crudo)
    with pytest.raises(ValueError, match="necesita columnas que este archivo no trae"):
        variables.construir(d, "huella")


def test_los_datasets_antiguos_siguen_sirviendo_con_los_conjuntos_viejos(df_crudo):
    d = variables.construir(datos.agregar_columnas_basicas(df_crudo), "historial")
    assert set(config.NUMERICAS_HISTORIAL) <= set(d.columns)


def test_construir_completo_trae_todas_las_variables(df_completo):
    d = variables.construir(datos.agregar_columnas_basicas(df_completo), "completo")
    num, cat = variables.columnas_de("completo")
    assert set(num + cat) <= set(d.columns)
    assert len(num) + len(cat) > 40
    assert not d[num].isna().any().any()                     # nada sin rellenar


def test_completo_no_incluye_columnas_prohibidas():
    num, cat = variables.columnas_de("completo")
    assert not set(num + cat) & set(config.COLUMNAS_PROHIBIDAS)
    assert "huella_dispositivo" not in cat and "id_comercio" not in cat


def test_registrar_conjunto_valida(monkeypatch):
    monkeypatch.setattr(config, "CONJUNTOS_GRUPOS", dict(config.CONJUNTOS_GRUPOS))
    with pytest.raises(ValueError, match="necesita el grupo 'historial'"):
        variables.registrar_conjunto("malo", ["perfil"])
    with pytest.raises(ValueError, match="desconocidos"):
        variables.registrar_conjunto("malo", ["inventado"])
    variables.registrar_conjunto("bueno", ["hora", "huella"])
    assert "bueno" in config.CONJUNTOS_GRUPOS


# ---------------------------------------------------------------- etiquetas tardías
def test_etiqueta_conocida_respeta_la_fecha_de_corte():
    df = pd.DataFrame({"es_fraude": [1, 1, 1, 0],
                       "fecha_confirmacion_fraude": ["2026-03-01 00:00:00", "2026-06-01 00:00:00", None, "2026-03-01 00:00:00"]})
    r = etiquetas.etiqueta_conocida(df, "2026-04-01")
    assert list(r) == [1, 0, 0, 0]          # el 2.º se confirma después del corte, el 3.º nunca, el 4.º no es fraude


def test_etiqueta_conocida_sin_columna_usa_la_completa(df_crudo):
    assert etiquetas.etiqueta_conocida(df_crudo, "2026-04-01").sum() == df_crudo["es_fraude"].sum()


def test_entrenar_con_etiquetas_conocidas_ve_menos_fraudes(ruta_csv_completo):
    df, m = entrenar.preparar_datos(ruta_csv_completo, "completo")
    completo = entrenar.ajustar_y_evaluar(df, m, "logistica", "completo", etiquetas_conocidas=False)
    tardio = entrenar.ajustar_y_evaluar(df, m, "logistica", "completo", etiquetas_conocidas=True)
    assert tardio["positivos_entrenamiento"] < completo["positivos_entrenamiento"]
    assert tardio["filas"] == completo["filas"]


def test_la_validacion_y_la_prueba_se_miden_con_la_verdad_completa(ruta_csv_completo):
    df, m = entrenar.preparar_datos(ruta_csv_completo, "completo")
    r = entrenar.ajustar_y_evaluar(df, m, "logistica", "completo", etiquetas_conocidas=True)
    assert r["fraudes_prueba"] == df.loc[m["prueba"], "es_fraude"].sum()
    assert r["fraudes_validacion"] == df.loc[m["validacion"], "es_fraude"].sum()      # verdad completa, no la tardía


# ---------------------------------------------------------------- scripts
def test_comparar_variables_de_punta_a_punta(ruta_csv_completo, monkeypatch):
    monkeypatch.setattr(config, "CONJUNTOS_GRUPOS", dict(config.CONJUNTOS_GRUPOS))
    r = comparar_variables.ejecutar(ruta_csv_completo, modelos=("logistica",), registrar=False)
    t = r["tabla"]
    esperados = {"base", "historial", "perfil", "huella", "geo_ip", "identidad", "comportamiento", "completo",
                 "completo_sin_perfil", "completo_sin_huella", "completo_sin_geo_ip", "completo_sin_identidad",
                 "completo_sin_comportamiento"}
    assert set(t["conjunto"]) == esperados
    h = t.set_index("conjunto")
    assert h.loc["historial", "dif_ap"] == 0 and h.loc["completo", "n_variables"] > h.loc["historial", "n_variables"]
    assert h.loc["completo", "ranking_ap"] >= h.loc["historial", "ranking_ap"] - 0.02     # con señal real, no empeora


def test_recall_a_fpr_con_puntajes_perfectos_y_aleatorios():
    y = np.array([1] * 50 + [0] * 950)
    perfecto = np.where(y == 1, 0.9, 0.1)
    assert reentrenamiento.recall_a_fpr(perfecto, y, 0.03) == 1.0
    rng = np.random.default_rng(0)
    azar = rng.random(20000)
    y2 = (rng.random(20000) < 0.05).astype(int)
    assert abs(reentrenamiento.recall_a_fpr(azar, y2, 0.03) - 0.03) < 0.03


def test_simular_reentrenamiento_da_una_fila_por_mes_de_prueba(ruta_csv_completo):
    df, m = entrenar.preparar_datos(ruta_csv_completo, "completo")
    t = reentrenamiento.simular(df, m, "logistica", "completo")
    assert list(t["mes"]) == ["2026-09", "2026-10", "2026-11"]
    assert {"ap_estatico", "ap_reentrenado", "recall_a_3pct_estatico", "recall_a_3pct_reentrenado"} <= set(t.columns)
    assert t.drop(columns="mes").notna().all().all()


def test_main_de_comparar_variables_imprime_sin_errores(ruta_csv_completo, monkeypatch, capsys):
    monkeypatch.setattr(config, "CONJUNTOS_GRUPOS", dict(config.CONJUNTOS_GRUPOS))
    comparar_variables.main(["--datos", str(ruta_csv_completo), "--modelos", "logistica", "--sin-guardar"])
    salida = capsys.readouterr().out
    assert "completo_sin_huella" in salida and "Recall por tipo de fraude" in salida


def test_main_de_reentrenamiento_imprime_sin_errores(ruta_csv_completo, capsys):
    reentrenamiento.main(["--datos", str(ruta_csv_completo), "--modelo", "logistica"])
    salida = capsys.readouterr().out
    assert "2026-09" in salida and "Promedio" in salida and "\\n" not in salida


# ---------------------------------------------------------------- alcance del historial (comercio / plataforma)
def _dos_comercios():
    base = pd.Timestamp("2026-03-01 10:00:00")
    filas = []
    for k, (comercio, ciudad, minuto) in enumerate([("A", "Bogotá", 0), ("B", "Cali", 5), ("A", "Bogotá", 20), ("B", "Cali", 30)]):
        filas.append({"id_comercio": comercio, "id_cliente": "C1", "fecha": base + pd.Timedelta(minutes=minuto), "localizacion": ciudad,
                      "monto_cop": 100_000, "dispositivo": "iPhone", "huella_dispositivo": "F1", "tipo_ip": "Móvil", "hora": 10,
                      "sistema_operativo": "iOS", "navegador": "Safari", "zona_horaria_dispositivo": "America/Bogota", "es_emulador": 0})
    return pd.DataFrame(filas)


def test_con_alcance_plataforma_el_historial_cruza_comercios_y_con_comercio_no():
    d = _dos_comercios()
    plataforma = variables.agregar_historial(d, "plataforma")
    comercio = variables.agregar_historial(d, "comercio")
    assert list(plataforma["n_compras_previas"]) == [0, 1, 2, 3]
    assert list(comercio["n_compras_previas"]) == [0, 0, 1, 1]                    # cada comercio cuenta lo suyo
    assert list(plataforma["ciudad_distinta_anterior"]) == [0, 1, 1, 1]            # Bogotá y Cali, una tras otra: viaje imposible
    assert list(comercio["ciudad_distinta_anterior"]) == [0, 0, 0, 0]              # cada comercio solo ve su ciudad: no lo puede notar


def test_la_huella_compartida_solo_se_ve_con_alcance_plataforma():
    filas = []
    base = pd.Timestamp("2026-03-01 10:00:00")
    for k, (comercio, cliente) in enumerate([("A", "C1"), ("B", "C2"), ("C", "C3")]):
        filas.append({"id_comercio": comercio, "id_cliente": cliente, "fecha": base + pd.Timedelta(minutes=k), "huella_dispositivo": "GRANJA",
                      "dispositivo": "iPhone", "sistema_operativo": "iOS", "navegador": "Safari", "zona_horaria_dispositivo": "America/Bogota", "es_emulador": 0})
    d = pd.DataFrame(filas)
    assert list(variables.agregar_huella(d, "plataforma")["huella_clientes_distintos"]) == [1, 2, 3]
    assert list(variables.agregar_huella(d, "comercio")["huella_clientes_distintos"]) == [1, 1, 1]       # cada comercio la ve una sola vez


def test_el_alcance_comercio_exige_la_columna_y_valida_el_nombre(df_crudo):
    d = datos.agregar_columnas_basicas(df_crudo).drop(columns=["patron"])
    with pytest.raises(ValueError, match="necesita la columna id_comercio"):
        variables.construir(d, "historial", "comercio")
    with pytest.raises(ValueError, match="Alcance desconocido"):
        variables.construir(d, "historial", "galaxia")


def test_el_alcance_por_defecto_no_cambia_nada_de_lo_anterior(df_completo):
    d = datos.agregar_columnas_basicas(df_completo)
    a = variables.construir(d, "completo")
    b = variables.construir(d, "completo", "plataforma")
    pd.testing.assert_frame_equal(a, b)


def test_comparar_alcance_de_punta_a_punta(ruta_csv_completo):
    from modelo_ia import comparar_alcance
    r = comparar_alcance.ejecutar(ruta_csv_completo, modelos=("logistica",), registrar=False)
    assert set(r["tabla"]["alcance"]) == {"plataforma", "comercio"} and len(r["tabla"]) == 2
    assert set(r["patrones"]) == {"logistica|plataforma", "logistica|comercio"}


def test_el_modelo_guardado_recuerda_con_que_alcance_se_entreno(ruta_csv_completo, carpeta_temporal):
    import json as _json
    df, m = entrenar.preparar_datos(ruta_csv_completo, "completo", "comercio")
    r = entrenar.ajustar_y_evaluar(df, m, "logistica", "completo")
    r["alcance_historial"] = "comercio"
    ruta = entrenar.guardar(r, ruta_csv_completo)
    assert _json.loads(ruta.with_suffix(".json").read_text(encoding="utf-8"))["alcance_historial"] == "comercio"
    r2 = entrenar.ajustar_y_evaluar(df, m, "logistica", "completo")                      # sin indicar alcance: se asume el de siempre
    assert _json.loads(entrenar.guardar(r2, ruta_csv_completo).with_suffix(".json").read_text(encoding="utf-8"))["alcance_historial"] == "plataforma"


# ---------------------------------------------------------------- estado del dispositivo derivado del historial
def _huellas(filas):
    base = pd.Timestamp("2026-03-01 10:00:00")
    return pd.DataFrame([{"id_comercio": c, "id_cliente": cl, "huella_dispositivo": h, "fecha": base + pd.Timedelta(minutes=i)}
                         for i, (c, cl, h) in enumerate(filas)])


def test_el_estado_derivado_a_mano():
    d = _huellas([("A", "C1", "H1"), ("A", "C1", "H1"), ("A", "C1", "H2"), ("A", "C1", "H1"), ("A", "C2", "H1")])
    assert list(variables.derivar_estado_dispositivo(d, "plataforma")) == ["Nuevo", "Conocido", "Nuevo", "Conocido", "Nuevo"]
    assert list(variables.derivar_estado_dispositivo(d, "plataforma", primer_pago="Conocido")) == ["Conocido", "Conocido", "Nuevo", "Conocido", "Conocido"]


def test_el_estado_derivado_respeta_el_alcance_y_no_usa_el_futuro():
    d = _huellas([("A", "C1", "H1"), ("B", "C1", "H1"), ("A", "C1", "H1")])
    assert list(variables.derivar_estado_dispositivo(d, "plataforma")) == ["Nuevo", "Conocido", "Conocido"]          # la red compartida lo reconoce
    assert list(variables.derivar_estado_dispositivo(d, "comercio")) == ["Nuevo", "Nuevo", "Conocido"]              # cada comercio, lo suyo
    alterado = d.copy()
    alterado.loc[2, "huella_dispositivo"] = "OTRA"                                                               # cambia algo POSTERIOR
    assert list(variables.derivar_estado_dispositivo(alterado, "comercio"))[:2] == ["Nuevo", "Nuevo"]
    assert variables.derivar_estado_dispositivo(d, "comercio").index.equals(d.index)


def test_comparar_estado_de_punta_a_punta(ruta_csv_completo):
    from modelo_ia import comparar_estado
    r = comparar_estado.ejecutar(ruta_csv_completo, modelo="logistica", alcance="comercio")
    assert list(r["tabla"]["estado"]) == ["original", "derivado (primer pago = Nuevo)", "derivado (primer pago = Conocido)",
                                          "sin la variable", "sin reentrenar: modelo original + derivado"]
    assert set(r["reparto"].columns) == {"original", "derivado (primer pago = Nuevo)"} and abs(r["reparto"].sum().sum() - 2) < 1e-9
