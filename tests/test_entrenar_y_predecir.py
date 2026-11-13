import json

import pytest

from modelo_ia import config, entrenar, experimentos, modelos, predecir
from modelo_ia.tests.conftest import hacer_datos


@pytest.fixture
def preparado(ruta_csv):
    return entrenar.preparar_datos(ruta_csv, "historial")


def test_umbral_de_bloqueo_siempre_mayor_que_el_de_revision():
    for u in (0.05, 0.35, 0.7, 0.95):
        revisar, bloquear = entrenar.definir_umbrales(u)
        assert bloquear > revisar and bloquear <= 0.99


def test_entrenar_logistica_mejora_al_azar(preparado):
    df, m = preparado
    r = entrenar.ajustar_y_evaluar(df, m, "logistica", "historial")
    base = df.loc[m["prueba"], "es_fraude"].mean()
    assert r["metricas_prueba"]["precision"] > base * 2          # mejor que alertar al azar
    assert r["metricas_prueba"]["recall"] > 0.5
    assert r["metricas_prueba"]["costo_cop"] < r["costo_prueba_sin_modelo_cop"]


def test_el_umbral_no_depende_de_la_prueba(preparado):
    """Si se cambian las etiquetas de la prueba, el umbral elegido no puede cambiar."""
    df, m = preparado
    antes = entrenar.ajustar_y_evaluar(df, m, "logistica", "historial")
    otro = df.copy()
    idx = otro.index[m["prueba"]]
    otro.loc[idx, "es_fraude"] = otro.loc[idx, "es_fraude"].sample(frac=1, random_state=1).values
    despues = entrenar.ajustar_y_evaluar(otro, m, "logistica", "historial")
    assert antes["umbral_revisar"] == despues["umbral_revisar"]
    assert antes["metricas_validacion"] == despues["metricas_validacion"]


def test_el_modelo_no_ve_columnas_prohibidas(preparado):
    df, m = preparado
    r = entrenar.ajustar_y_evaluar(df, m, "logistica", "historial")
    usadas = set(r["numericas"] + r["categoricas"])
    assert not usadas & set(config.COLUMNAS_PROHIBIDAS)
    nombres = r["pipeline"].named_steps["preparacion"].get_feature_names_out()
    assert not any("patron" in n or "nivel_riesgo" in n or "id_cliente" in n for n in nombres)


@pytest.mark.parametrize("nombre", ["arbol", "gradient_boosting"])
def test_otros_modelos_se_pueden_entrenar(preparado, nombre):
    df, m = preparado
    r = entrenar.ajustar_y_evaluar(df, m, nombre, "historial")
    assert 0 <= r["metricas_prueba"]["recall"] <= 1


def test_modelo_desconocido():
    with pytest.raises(ValueError, match="desconocido"):
        modelos.crear_modelo("inventado", ["monto_cop"], ["tipo_ip"])


def test_guardar_cargar_y_predecir(preparado, ruta_csv, carpeta_temporal):
    df, m = preparado
    r = entrenar.ajustar_y_evaluar(df, m, "logistica", "historial")
    ruta = entrenar.guardar(r, ruta_csv, como_actual=True)
    assert ruta.exists() and ruta.with_suffix(".json").exists()
    meta = json.loads(ruta.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["datos_sinteticos"] is True and meta["version"] == config.VERSION

    artefacto = predecir.cargar_artefacto()                 # toma modelo_actual.joblib
    sospechoso = dict(predecir.EJEMPLO_VIAJE_IMPOSIBLE, tipo_ip="Proxy", estado_dispositivo="No reconocido",
                      localizacion="Bogotá, Colombia", dispositivo="iPhone", metodo_pago="Visa",
                      categoria="Ropa")
    normal = dict(predecir.EJEMPLO_NORMAL, localizacion="Bogotá, Colombia", metodo_pago="Visa",
                  tipo_ip="Residencial", categoria="Ropa")
    a, b = predecir.analizar_transaccion(sospechoso, artefacto), predecir.analizar_transaccion(normal, artefacto)
    assert a["decision"] in {"APROBAR", "REVISAR", "BLOQUEAR"}
    assert a["probabilidad_fraude"] > b["probabilidad_fraude"]
    assert isinstance(a["motivos"], list)


def test_predecir_pide_los_campos_que_faltan(preparado, ruta_csv, carpeta_temporal):
    df, m = preparado
    entrenar.guardar(entrenar.ajustar_y_evaluar(df, m, "logistica", "historial"), ruta_csv)
    artefacto = predecir.cargar_artefacto()
    pago = dict(predecir.EJEMPLO_NORMAL)
    del pago["tipo_ip"]
    with pytest.raises(ValueError, match="faltan campos"):
        predecir.analizar_transaccion(pago, artefacto)


def test_cargar_artefacto_sin_modelos(carpeta_temporal):
    with pytest.raises(FileNotFoundError, match="No hay modelos guardados"):
        predecir.cargar_artefacto()


def test_la_bitacora_acumula_filas(preparado, carpeta_temporal):
    df, m = preparado
    r = entrenar.ajustar_y_evaluar(df, m, "logistica", "historial")
    entrenar.registrar_experimento(r, "primero")
    entrenar.registrar_experimento(r, "segundo")
    tabla = experimentos.leer()
    assert len(tabla) == 2 and list(tabla["nota"]) == ["primero", "segundo"]


def test_main_de_punta_a_punta(ruta_csv, carpeta_temporal, capsys):
    entrenar.main(["--datos", str(ruta_csv), "--como-actual", "--nota", "prueba"])
    salida = capsys.readouterr().out
    assert "Modelo: logistica" in salida and "Modelo guardado" in salida
    assert (config.CARPETA_MODELOS / "modelo_actual.joblib").exists()
