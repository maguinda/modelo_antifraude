"""Las señales que más pesaron: reparto exacto, frases que no mienten, privacidad hacia los comercios y que nunca rompan una decisión."""
import json
import sqlite3

import joblib
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from modelo_ia import config, explicacion_local as el, modelos, predecir
from modelo_ia.api.ajustes import Ajustes
from modelo_ia.api.app import crear_app
from modelo_ia.api.ejemplos import EJEMPLOS_SOLICITUD

ACTUAL = config.CARPETA_MODELOS / "modelo_actual.joblib"
pytestmark = pytest.mark.skipif(not ACTUAL.exists() or "referencia" not in joblib.load(ACTUAL)["meta"], reason="el modelo actual no trae referencia (correr explicacion_local agregar-referencia)")


@pytest.fixture(scope="module")
def modelo():
    return joblib.load(ACTUAL)


def _pago_riesgoso(modelo, **cambios):
    """El pago TÍPICO del modelo con cambios (así se prueba una señal a la vez)."""
    p = dict(modelo["meta"]["referencia"])
    p.update(cambios)
    return p


# ------------------------------------------------------------------ el reparto
def test_los_puntos_suman_exactamente_la_diferencia_con_un_pago_tipico(modelo):
    p = _pago_riesgoso(modelo, tipo_ip="VPN", ip_es_datacenter=1, ip_reputacion=35, n_compras_previas=0, estado_dispositivo="No reconocido", intentos_fallidos_24h=3)
    c = el.contribuciones(modelo["pipeline"], p, modelo["meta"])
    assert abs(sum(c["pesos"].values()) - 100 * (c["puntaje"] - c["tipico"])) < 1e-6                      # propiedad de los valores de Shapley
    assert c["puntaje"] > c["tipico"] + 0.3 and max(c["pesos"], key=c["pesos"].get) in ("red", "dispositivo", "acceso")


def test_es_determinista_y_un_pago_tipico_no_tiene_nada_que_explicar(modelo):
    p = _pago_riesgoso(modelo, tipo_ip="VPN", ip_reputacion=30)
    a, b = el.explicar(modelo["pipeline"], p, modelo["meta"]), el.explicar(modelo["pipeline"], p, modelo["meta"])
    assert a == b and a["suben"]
    assert el.explicar(modelo["pipeline"], _pago_riesgoso(modelo), modelo["meta"]) == {"suben": [], "bajan": [], "puntaje_tipico": None}


def test_la_senal_que_se_cambia_es_la_que_aparece_arriba(modelo):
    casos = {"red": dict(tipo_ip="PROXY", ip_es_datacenter=1, ip_reputacion=25),
             "primera_compra": dict(n_compras_previas=0, monto_cop=900000, monto_vs_promedio=1.0)}
    for esperada, cambios in casos.items():
        s = el.explicar(modelo["pipeline"], _pago_riesgoso(modelo, **cambios), modelo["meta"])
        assert s["suben"] and esperada in [x["clave"] for x in s["suben"]], (esperada, s)                 # exacta, no «alguna de varias»
    s = el.explicar(modelo["pipeline"], _pago_riesgoso(modelo, tipo_ip="PROXY", ip_es_datacenter=1, ip_reputacion=25), modelo["meta"])
    assert s["suben"][0]["clave"] == "red" and "PROXY" in s["suben"][0]["texto"] and s["suben"][0]["puntos"] > 5
    # una señal que el modelo casi no usa por sí sola NO debe inventarse un peso
    s = el.explicar(modelo["pipeline"], _pago_riesgoso(modelo, estado_dispositivo="No reconocido", dispositivo_nuevo_cliente=1), modelo["meta"])
    assert all(x["clave"] != "dispositivo" or abs(x["puntos"]) >= el.MINIMO_PUNTOS for x in s["suben"] + s["bajan"])


def test_solo_se_muestran_cambios_que_valen_la_pena_y_con_tope(modelo):
    s = el.explicar(modelo["pipeline"], _pago_riesgoso(modelo, tipo_ip="VPN", ip_es_datacenter=1, ip_reputacion=20, n_compras_previas=0, estado_dispositivo="No reconocido",
                                                          intentos_fallidos_24h=4, tiempo_formulario_seg=5, edad_cuenta_dias=1, cuenta_nueva=1), modelo["meta"])
    assert 0 < len(s["suben"]) <= el.TOPE_SUBEN and len(s["bajan"]) <= el.TOPE_BAJAN
    assert all(abs(x["puntos"]) >= el.MINIMO_PUNTOS for x in s["suben"] + s["bajan"]) and [x["puntos"] for x in s["suben"]] == sorted((x["puntos"] for x in s["suben"]), reverse=True)


# ------------------------------------------------------------------ las frases no pueden mentir
PAGO = {"monto_cop": 80000, "monto_vs_promedio": 1.0, "n_compras_previas": 5, "transacciones_ultimas_24h": 1, "minutos_desde_anterior": 5000, "ciudad_distinta_anterior": 0,
        "localizacion": "Bogotá", "dispositivo_nuevo_cliente": 0, "estado_dispositivo": "Conocido", "hora": 15, "es_madrugada": 0, "ip_frecuencia_previa": 3, "tipo_ip": "RESIDENCIAL",
        "ip_es_datacenter": 0, "ip_reputacion": 90, "ip_fuera_de_colombia": 0, "distancia_ip_ciudad_km": 5, "zona_horaria_no_coincide_ip": 0, "ip_pais": "Colombia", "es_emulador": 0,
        "navegador_headless": 0, "huella_clientes_distintos": 1, "edad_cuenta_dias": 700, "kyc_nivel": "Completo", "autenticacion_2fa": "No exigida", "intentos_fallidos_24h": 0,
        "cambio_credenciales_7d": 0, "tiempo_formulario_seg": 40, "pego_numero_tarjeta": 0, "categoria": "Ropa", "metodo_pago": "Nequi", "canal_pago": "App móvil"}
REF = {"monto_cop": 150000}


def frase(clave, sube, **cambios):
    return el.SENALES[clave][1](dict(PAGO, **cambios), sube, REF)


def test_ninguna_frase_a_favor_afirma_algo_que_los_datos_contradicen():
    """El error real que se encontró: «La IP está en Colombia» para una IP de Rusia, y «Cuenta antigua (2 días)»."""
    assert frase("geo", False, ip_fuera_de_colombia=1, ip_pais="Rusia", distancia_ip_ciudad_km=10700) is None
    assert frase("geo", False, distancia_ip_ciudad_km=900) is None and "Colombia" in frase("geo", False)
    assert frase("cuenta", False, edad_cuenta_dias=2) is None and frase("cuenta", False, edad_cuenta_dias=20) is None and "700" in frase("cuenta", False)
    assert frase("red", False, tipo_ip="VPN") is None and frase("red", False, tipo_ip="RESIDENCIAL", ip_es_datacenter=1) is None
    assert frase("dispositivo", False, estado_dispositivo="No reconocido") is None and frase("dispositivo", False, dispositivo_nuevo_cliente=1) is None
    assert frase("acceso", False, intentos_fallidos_24h=3) is None and frase("acceso", False, cambio_credenciales_7d=1) is None
    assert frase("verificacion", False, kyc_nivel="Básico") is None and frase("equipo", False, es_emulador=1) is None
    assert frase("ciudad", False, ciudad_distinta_anterior=1) is None and frase("primera_compra", False, n_compras_previas=1) is None
    assert frase("ritmo", False, transacciones_ultimas_24h=6) is None and frase("hora", False, hora=3) is None and frase("formulario", False, tiempo_formulario_seg=4) is None
    assert frase("monto", False, monto_cop=900000) is None and frase("ip_habitual", False, ip_frecuencia_previa=0) is None


def test_las_frases_que_dicen_que_subio_el_riesgo_tienen_un_hecho_que_las_respalda():
    assert "primera compra" in frase("primera_compra", True, n_compras_previas=0) and "pocas compras" in frase("primera_compra", True, n_compras_previas=2)
    assert "Varios pagos" not in frase("ritmo", True) and "Varios pagos" in frase("ritmo", True, transacciones_ultimas_24h=4)
    assert "poco habitual" not in frase("hora", True) and "madrugada" in frase("hora", True, hora=3, es_madrugada=1)
    assert "Rusia" in frase("geo", True, ip_fuera_de_colombia=1, ip_pais="Rusia") and "1200 km" in frase("geo", True, distancia_ip_ciudad_km=1200).replace(".", "").replace(" ", " ")
    assert "VPN" in frase("red", True, tipo_ip="VPN") and "residencial" in frase("red", True, tipo_ip="RESIDENCIAL") and "habituales" not in frase("red", True, tipo_ip="RESIDENCIAL")
    assert "emulador" in frase("equipo", True, es_emulador=1) and "Cuenta nueva (2 días)" == frase("cuenta", True, edad_cuenta_dias=2)
    assert "veces el monto típico" in frase("monto", True, monto_cop=900000, n_compras_previas=0) and "veces el promedio de este cliente" in frase("monto", True, monto_vs_promedio=4.2)
    assert frase("pegado", False, pego_numero_tarjeta=1) is None and "Pegó" in frase("pegado", True, pego_numero_tarjeta=1)
    assert "doble factor" in frase("verificacion", True, autenticacion_2fa="Exigida y fallida")


def test_la_conexion_y_la_verificacion_comparan_con_lo_habitual_en_lugar_de_decir_solo_que_pesan():
    t = el.SENALES["red"][1](dict(PAGO, tipo_ip="MOVIL"), True, {"tipo_ip": "RESIDENCIAL"})
    assert "movil" in t and "residencial" in t and "pesa en el puntaje" not in t
    v = el.SENALES["verificacion"][1](dict(PAGO, kyc_nivel="Parcial"), True, {"kyc_nivel": "Completo"})
    assert "parcial" in v and "completo" in v and "pesa en el puntaje" not in v


def test_todas_las_senales_del_modelo_tienen_frase_y_variables_que_existen(modelo):
    columnas = set(modelo["meta"]["numericas"] + modelo["meta"]["categoricas"])
    assert set(el.SENALES) >= {"monto", "red", "dispositivo", "cuenta", "formulario", "pago"}
    cubiertas = {v for variables, _ in el.SENALES.values() for v in variables}
    assert cubiertas <= (columnas | set(config.NUMERICAS_BASE) | set(sum((list(g[0]) + list(g[1]) for g in config.GRUPOS_VARIABLES.values()), [])) | set(config.CATEGORICAS))
    assert len(columnas - cubiertas) <= 1, columnas - cubiertas                                              # casi toda variable del modelo pertenece a alguna señal


# ------------------------------------------------------------------ nunca rompe una decisión
def test_sin_referencia_o_con_datos_incompletos_no_se_muestra_nada_y_no_falla(modelo):
    sin = {"pipeline": modelo["pipeline"], "meta": {k: v for k, v in modelo["meta"].items() if k != "referencia"}}
    vacio = {"suben": [], "bajan": [], "puntaje_tipico": None}
    assert el.explicar(sin["pipeline"], _pago_riesgoso(modelo, tipo_ip="VPN"), sin["meta"]) == vacio
    incompleto = _pago_riesgoso(modelo, tipo_ip="VPN"); del incompleto["monto_cop"]
    assert el.explicar(modelo["pipeline"], incompleto, modelo["meta"]) == vacio
    assert el.explicar(object(), _pago_riesgoso(modelo, tipo_ip="VPN"), modelo["meta"]) == vacio


def test_la_referencia_sale_de_los_pagos_legitimos_del_entrenamiento():
    df = pd.DataFrame({"x": [1, 2, 3, 100, 200], "c": ["a", "a", "b", "z", "z"], "es_fraude": [0, 0, 0, 1, 1]})
    ref = el.calcular_referencia(df, pd.Series([True, True, True, True, False]), "es_fraude", ["x"], ["c"])
    assert ref == {"x": 2, "c": "a"}                                                                          # sin el fraude (100) ni lo que quedó fuera del entrenamiento (200)


def test_agregar_referencia_a_modelos_viejos_no_cambia_sus_predicciones(tmp_path, ruta_csv_completo):
    import shutil
    from modelo_ia import entrenar
    df, m = entrenar.preparar_datos(ruta_csv_completo, "completo", "comercio")
    r = entrenar.ajustar_y_evaluar(df, m, "logistica", "completo")
    meta = {"modelo": "logistica", "conjunto": "completo", "numericas": r["numericas"], "categoricas": r["categoricas"], "umbral_revisar": 0.3, "umbral_bloquear": 0.7}
    joblib.dump({"pipeline": r["pipeline"], "meta": meta}, tmp_path / "viejo.joblib")
    antes = modelos.puntajes(r["pipeline"], df[r["numericas"] + r["categoricas"]].head(300))
    assert el.agregar_referencia(ruta_csv_completo, tmp_path) == ["viejo.joblib"] and el.agregar_referencia(ruta_csv_completo, tmp_path) == []
    nuevo = joblib.load(tmp_path / "viejo.joblib")
    assert set(nuevo["meta"]["referencia"]) == set(r["numericas"] + r["categoricas"])
    assert np.array_equal(antes, modelos.puntajes(nuevo["pipeline"], df[r["numericas"] + r["categoricas"]].head(300)))
    assert "referencia" in r and set(r["referencia"]) == set(r["numericas"] + r["categoricas"])            # y los modelos nuevos ya la traen al entrenarse


# ------------------------------------------------------------------ en la API real
@pytest.fixture
def servicio(tmp_path):
    app = crear_app(Ajustes(ruta_bd=tmp_path / "a.sqlite3", pepper="p" * 40, limite_peticiones_por_minuto=10 ** 6))
    a = app.state.almacen
    k = {"comercio": a.crear_clave(a.crear_comercio("T"), "comercio"), "analista": a.crear_usuario("analista", "Ana Pérez")[1]}
    with TestClient(app) as c:
        yield c, k, tmp_path / "a.sqlite3"


def _h(k, quien):
    return {"X-API-Key": k[quien]}


def test_un_pago_dudoso_guarda_sus_senales_y_el_personal_las_ve_pero_el_comercio_no(servicio):
    c, k, bd = servicio
    r = c.post("/v1/evaluaciones", json=EJEMPLOS_SOLICITUD["03_en_revision_vpn_cliente_nuevo"]["value"], headers=_h(k, "comercio")).json()
    assert r["decision"] == "EN_REVISION" and "senales" not in json.dumps(r)                                 # la respuesta al comercio no cambia
    d = c.get(f"/v1/panel/transacciones/{r['id_seguimiento']}", headers=_h(k, "analista")).json()
    assert d["senales"]["suben"] and d["senales"]["suben"][0]["puntos"] > 0 and d["senales"]["puntaje_tipico"] is not None
    assert any(p["paso"] == "Señales que más pesaron" for p in d["como_se_decidio"])
    assert "senales" not in c.get(f"/v1/evaluaciones/{r['id_evaluacion']}", headers=_h(k, "comercio")).text
    propio = c.get(f"/v1/panel/transacciones/{r['id_seguimiento']}", headers=_h(k, "comercio")).json()
    assert propio["senales"] is None and not any(p["paso"] == "Señales que más pesaron" for p in propio["como_se_decidio"])
    cola = c.get("/v1/revisiones", headers=_h(k, "analista")).json()["revisiones"][0]
    assert cola["senales"]["suben"] == d["senales"]["suben"]


def test_un_pago_aprobado_no_calcula_ni_guarda_senales(servicio):
    c, k, bd = servicio
    r = c.post("/v1/evaluaciones", json=EJEMPLOS_SOLICITUD["01_aprobada_pago_normal"]["value"], headers=_h(k, "comercio")).json()
    assert r["decision"] == "APROBADA"
    assert sqlite3.connect(bd).execute("SELECT senales_json FROM evaluaciones").fetchone()[0] is None
    d = c.get(f"/v1/panel/transacciones/{r['id_seguimiento']}", headers=_h(k, "analista")).json()
    assert d["senales"] == {"suben": [], "bajan": []} and not any(p["paso"] == "Señales que más pesaron" for p in d["como_se_decidio"])


def test_las_senales_quedan_como_se_calcularon_al_decidir(servicio):
    c, k, bd = servicio
    r = c.post("/v1/evaluaciones", json=EJEMPLOS_SOLICITUD["04_rechazada_pago_sospechoso"]["value"], headers=_h(k, "comercio")).json()
    guardadas = json.loads(sqlite3.connect(bd).execute("SELECT senales_json FROM evaluaciones").fetchone()[0])
    de_nuevo = c.post("/v1/evaluaciones", json=EJEMPLOS_SOLICITUD["04_rechazada_pago_sospechoso"]["value"], headers=_h(k, "comercio"))
    assert de_nuevo.status_code == 200 and de_nuevo.json()["repetida"] is True                               # la repetición no recalcula nada
    assert c.get(f"/v1/panel/transacciones/{r['id_seguimiento']}", headers=_h(k, "analista")).json()["senales"]["suben"] == guardadas["suben"]


def test_una_base_vieja_sin_la_columna_de_senales_se_actualiza(tmp_path):
    from modelo_ia.api.almacen import Almacen
    ruta = tmp_path / "v.sqlite3"
    Almacen(ruta, "p" * 40).inicializar()
    con = sqlite3.connect(ruta); con.execute("ALTER TABLE evaluaciones DROP COLUMN senales_json"); con.commit(); con.close()
    Almacen(ruta, "p" * 40).inicializar()
    assert "senales_json" in {r[1] for r in sqlite3.connect(ruta).execute("PRAGMA table_info(evaluaciones)")}


def test_si_calcular_las_senales_falla_el_pago_igual_se_decide(servicio, monkeypatch):
    c, k, bd = servicio
    monkeypatch.setattr(el, "contribuciones", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("falla")))
    r = c.post("/v1/evaluaciones", json=EJEMPLOS_SOLICITUD["03_en_revision_vpn_cliente_nuevo"]["value"], headers=_h(k, "comercio"))
    assert r.status_code == 201 and r.json()["decision"] == "EN_REVISION" and r.json()["modo"] == "normal"
    assert sqlite3.connect(bd).execute("SELECT senales_json FROM evaluaciones").fetchone()[0] is None


def test_no_cuesta_casi_nada_de_tiempo(servicio):
    import time
    c, k, bd = servicio
    p = EJEMPLOS_SOLICITUD["04_rechazada_pago_sospechoso"]["value"]
    c.post("/v1/evaluaciones", json=dict(p, id_transaccion="CALENTAR"), headers=_h(k, "comercio"))
    t = time.perf_counter(); r = c.post("/v1/evaluaciones", json=dict(p, id_transaccion="MEDIR"), headers=_h(k, "comercio")); ms = (time.perf_counter() - t) * 1000
    assert r.status_code == 201 and ms < 400, ms                                                              # holgado: en una máquina normal son ~40 ms


# ------------------------------------------------------------------ pantallas
def test_las_pantallas_usan_la_pieza_compartida_y_nunca_insertan_html():
    from modelo_ia.api import documentacion
    base = documentacion.ESTATICOS
    js = (base / "comun" / "senales.js").read_text(encoding="utf-8")
    for peligroso in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "localStorage", "XMLHttpRequest", "fetch("):
        assert peligroso not in js, peligroso
    assert "textContent" in js and "window.AntifraudeSenales" in js
    for pantalla in ("panel", "dashboard"):
        html = (base / pantalla / "index.html").read_text(encoding="utf-8")
        assert html.index("/estaticos/comun/senales.js") < html.index(f"/estaticos/{pantalla}/{pantalla}.js")   # la pieza compartida se carga antes
        assert "AntifraudeSenales.pintar" in (base / pantalla / f"{pantalla}.js").read_text(encoding="utf-8")
    css = (base / "comun" / "diseno.css").read_text(encoding="utf-8")
    assert ".senal.sube" in css and ".senal.baja" in css and ".barra-senal" in css
