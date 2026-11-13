"""Pruebas del API: contrato, seguridad, aislamiento entre comercios, datos raros, modo degradado y concurrencia."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from modelo_ia import config, datos, entrenar, predecir, variables
from modelo_ia.api import esquemas, exportar_openapi, traduccion
from modelo_ia.api.ajustes import Ajustes
from modelo_ia.api.app import crear_app
from modelo_ia.api.seguridad import LimitadorTasa
from modelo_ia.tests.conftest import hacer_datos_completos

RELOJ = datetime(2026, 11, 12, 12, 0, 0)
FECHA = "2026-11-12T11:55:00"
DECISIONES = {"APROBADA", "RECHAZADA", "EN_REVISION"}


# ------------------------------------------------------------------ preparación
@pytest.fixture(scope="session")
def artefacto(tmp_path_factory):
    """Un modelo pequeño (regresión logística con todas las variables) entrenado una sola vez."""
    ruta = tmp_path_factory.mktemp("datos") / "completo.csv"
    hacer_datos_completos().to_csv(ruta, index=False, encoding="utf-8-sig")
    df, m = entrenar.preparar_datos(ruta, "completo")
    r = entrenar.ajustar_y_evaluar(df, m, "logistica", "completo")
    meta = {"modelo": "logistica", "version": config.VERSION, "conjunto": "completo", "numericas": r["numericas"],
            "categoricas": r["categoricas"], "umbral_revisar": r["umbral_revisar"], "umbral_bloquear": r["umbral_bloquear"]}
    return {"pipeline": r["pipeline"], "meta": meta}


@pytest.fixture(scope="session")
def artefacto_sin_estado(tmp_path_factory):
    """El modelo para pagos donde el comercio no manda el estado del dispositivo (entrenado sin esa variable)."""
    ruta = tmp_path_factory.mktemp("datos_sin_estado") / "completo.csv"
    hacer_datos_completos().to_csv(ruta, index=False, encoding="utf-8-sig")
    df, m = entrenar.preparar_datos(ruta, "completo")
    r = entrenar.ajustar_y_evaluar(df, m, "logistica", "completo", sin_estado=True)
    meta = {"modelo": "logistica", "version": config.VERSION, "conjunto": "completo", "numericas": r["numericas"], "categoricas": r["categoricas"],
            "umbral_revisar": r["umbral_revisar"], "umbral_bloquear": r["umbral_bloquear"], "sin_estado": True}
    return {"pipeline": r["pipeline"], "meta": meta}


class Contexto:
    def __init__(self, app, claves):
        self.app, self.claves = app, claves
        self.cliente = TestClient(app)

    def h(self, quien):
        return {"X-API-Key": self.claves[quien]}

    def post(self, ruta, cuerpo, quien="A", **kw):
        return self.cliente.post(ruta, json=cuerpo, headers=self.h(quien), **kw)

    def evaluar(self, cuerpo, quien="A"):
        return self.post("/v1/evaluaciones", cuerpo, quien)

    @property
    def bd(self):
        return self.app.state.ajustes.ruta_bd


def construir_contexto(tmp_path, artefacto, art_sin_estado=None, **ajustes_extra):
    tmp_path.mkdir(parents=True, exist_ok=True)
    ajustes = Ajustes(ruta_bd=tmp_path / "api.sqlite3", pepper="pepper-de-prueba", **ajustes_extra)
    app = crear_app(ajustes, artefacto, reloj=lambda: RELOJ, artefacto_sin_estado=art_sin_estado)
    alm = app.state.almacen
    a, b = alm.crear_comercio("Tienda A"), alm.crear_comercio("Tienda B")
    claves = {"A": alm.crear_clave(a, "comercio"), "B": alm.crear_clave(b, "comercio"),
              "analista": alm.crear_clave(1, "analista"), "admin": alm.crear_clave(1, "admin")}
    ctx = Contexto(app, claves)
    ctx.comercios = {"A": a, "B": b}
    return ctx


@pytest.fixture
def ctx(tmp_path, artefacto):
    return construir_contexto(tmp_path, artefacto)


def pago(tx="T1", cliente="C1", sin=(), **cambios):
    """Un pago normal y completo; cada prueba cambia solo lo que necesita."""
    base = {"id_transaccion": tx, "id_cliente": cliente, "fecha_pago": FECHA, "monto_cop": 200000, "metodo_pago": "VISA",
            "canal_pago": "WEB", "categoria": "ROPA", "ciudad": "Bogotá",
            "dispositivo": {"tipo": "IPHONE", "estado": "CONOCIDO", "huella": f"FP-{cliente}", "sistema_operativo": "iOS",
                            "navegador": "Safari", "resolucion_pantalla": "390x844", "zona_horaria": "America/Bogota", "es_emulador": False},
            "red": {"tipo_ip": "RESIDENCIAL", "pais": "Colombia", "ciudad_estimada": "Bogotá", "distancia_a_ciudad_km": 5,
                    "es_datacenter": False, "reputacion": 90},
            "identidad": {"kyc_nivel": "COMPLETO", "edad_cuenta_dias": 500, "autenticacion_2fa": "NO_EXIGIDA",
                          "intentos_fallidos_24h": 0, "cambio_credenciales_7d": False},
            "comportamiento": {"tiempo_formulario_seg": 40, "velocidad_tecleo_ms": 170, "pego_numero_tarjeta": False, "mouse_natural": 0.8}}
    for k, v in cambios.items():
        base[k] = {**base[k], **v} if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    for k in sin:
        base.pop(k, None)
    return base


def pago_minimo(tx, cliente="C1", **extra):
    """Solo los campos obligatorios: al modelo (que usa todas las variables) le faltan datos."""
    p = pago(tx, cliente)
    p.pop("identidad"), p.pop("comportamiento")
    p["dispositivo"] = {"tipo": "IPHONE", "estado": "CONOCIDO"}
    p["red"] = {"tipo_ip": "RESIDENCIAL"}
    for k, v in extra.items():
        p[k] = {**p[k], **v} if isinstance(v, dict) and isinstance(p.get(k), dict) else v
    return p


# ------------------------------------------------------------------ contrato y estado
def test_salud_no_pide_clave_y_avisa_si_el_secreto_es_el_de_desarrollo(tmp_path, artefacto):
    c = TestClient(crear_app(Ajustes(ruta_bd=tmp_path / "x.sqlite3"), artefacto))
    r = c.get("/v1/salud")
    assert r.status_code == 200 and r.json()["estado"] == "ok" and r.json()["configuracion_insegura"] is True
    seguro = TestClient(crear_app(Ajustes(ruta_bd=tmp_path / "y.sqlite3", pepper="otro"), artefacto))
    assert seguro.get("/v1/salud").json()["configuracion_insegura"] is False


def test_swagger_y_openapi_estan_disponibles(ctx):
    assert ctx.cliente.get("/docs").status_code == 200
    assert ctx.cliente.get("/redoc").status_code == 200
    spec = ctx.cliente.get("/openapi.json").json()
    assert spec["openapi"].startswith("3.") and "/v1/evaluaciones" in spec["paths"]
    assert "X-API-Key" in json.dumps(spec["components"]["securitySchemes"])
    ops = [o["operationId"] for p in spec["paths"].values() for o in p.values()]
    assert len(ops) == len(set(ops))                                           # sin nombres repetidos


def test_el_contrato_publicado_coincide_con_el_codigo():
    """Si cambia la API hay que volver a exportar openapi.json (python -m modelo_ia.api.exportar_openapi)."""
    publicado = json.loads(exportar_openapi.RUTA.read_text(encoding="utf-8"))
    actual = json.loads(json.dumps(exportar_openapi.generar(), sort_keys=True, ensure_ascii=False))
    assert publicado == actual


def test_el_contrato_documenta_errores_y_ejemplos():
    spec = exportar_openapi.generar()
    post = spec["paths"]["/v1/evaluaciones"]["post"]
    assert {"401", "403", "409", "413", "422", "429", "503"} <= set(post["responses"])
    ejemplos = post["requestBody"]["content"]["application/json"]["examples"]
    assert {"01_aprobada_pago_normal", "04_rechazada_pago_sospechoso", "07_degradado_solo_campos_obligatorios"} <= set(ejemplos)
    assert "application/problem+json" in json.dumps(spec["paths"]["/v1/evaluaciones"]["post"]["responses"]["401"])


def test_swagger_usa_el_mismo_servidor_desde_el_que_se_abre(ctx):
    """Si el contrato fija una dirección (p. ej. localhost:8000), abrir la página por 127.0.0.1 falla por CORS."""
    assert "servers" not in ctx.cliente.get("/openapi.json").json()


def test_modelo_en_uso(ctx):
    r = ctx.cliente.get("/v1/modelo", headers=ctx.h("A"))
    assert r.status_code == 200 and r.json()["conjunto_variables"] == "completo"


# ------------------------------------------------------------------ cabeceras de seguridad
def test_todas_las_respuestas_llevan_cabeceras_de_seguridad(ctx):
    respuestas = [ctx.cliente.get("/v1/salud"), ctx.cliente.get("/docs"), ctx.cliente.get("/openapi.json"),
                  ctx.cliente.post("/v1/evaluaciones", json=pago()),                                # 401
                  ctx.evaluar(pago("CAB1")), ctx.evaluar(pago(monto_cop=-1)),                       # 201 y 422
                  ctx.cliente.get("/v1/no-existe", headers=ctx.h("A"))]                             # 404
    for r in respuestas:
        assert r.headers["X-Content-Type-Options"] == "nosniff", r.url
        assert r.headers["X-Frame-Options"] == "DENY" and r.headers["Referrer-Policy"] == "no-referrer", r.url
    for r in respuestas:
        if str(r.url).split("testserver")[1].startswith("/v1/"):
            assert r.headers["Cache-Control"] == "no-store" and "default-src 'none'" in r.headers["Content-Security-Policy"], r.url


def test_el_error_de_tamano_tambien_lleva_cabeceras(tmp_path, artefacto):
    c = construir_contexto(tmp_path, artefacto, tamano_maximo_bytes=500)
    r = c.cliente.post("/v1/evaluaciones", content="x" * 1000, headers={**c.h("A"), "Content-Type": "application/json"})
    assert r.status_code == 413 and r.headers["X-Content-Type-Options"] == "nosniff"


# ------------------------------------------------------------------ autenticación y permisos
def test_sin_clave_o_con_clave_falsa_da_401_con_formato_estandar(ctx):
    for cab in ({}, {"X-API-Key": "mia_comercio_inventada"}, {"X-API-Key": ""}):
        r = ctx.cliente.post("/v1/evaluaciones", json=pago(), headers=cab)
        assert r.status_code == 401
        assert r.headers["content-type"].startswith("application/problem+json")
        assert {"title", "status", "detail", "instance"} <= set(r.json())


def test_cada_rol_solo_puede_lo_suyo(ctx):
    assert ctx.post("/v1/evaluaciones", pago(), "analista").status_code == 403         # el analista no evalúa
    assert ctx.post("/v1/evaluaciones", pago(), "admin").status_code == 403
    assert ctx.cliente.get("/v1/revisiones", headers=ctx.h("A")).status_code == 403    # el comercio no ve la cola
    assert ctx.post("/v1/comercios", {"nombre": "Nueva"}, "A").status_code == 403
    assert ctx.post("/v1/comercios", {"nombre": "Nueva"}, "analista").status_code == 403
    assert ctx.cliente.get("/v1/lista-negra", headers=ctx.h("analista")).status_code == 403


def test_clave_desactivada_deja_de_servir(ctx):
    alm = ctx.app.state.almacen
    clave = alm.crear_clave(ctx.comercios["A"], "comercio")
    assert ctx.cliente.get("/v1/modelo", headers={"X-API-Key": clave}).status_code == 200
    p = alm.autenticar(clave)
    alm.desactivar_clave(p.clave_id)
    assert ctx.cliente.get("/v1/modelo", headers={"X-API-Key": clave}).status_code == 401


def test_las_claves_no_se_guardan_en_claro(ctx):
    contenido = b"".join(f.read_bytes() for f in ctx.bd.parent.glob(ctx.bd.name + "*"))
    for clave in ctx.claves.values():
        assert clave.encode() not in contenido


def test_el_admin_crea_comercios_y_su_clave_funciona(ctx):
    r = ctx.post("/v1/comercios", {"nombre": "Tienda Nueva"}, "admin")
    assert r.status_code == 201 and r.json()["clave_api"].startswith("mia_comercio_")
    nueva = {"X-API-Key": r.json()["clave_api"]}
    assert ctx.cliente.post("/v1/evaluaciones", json=pago("N1"), headers=nueva).status_code == 201


# ------------------------------------------------------------------ evaluar
def test_evaluar_un_pago_normal(ctx):
    r = ctx.evaluar(pago())
    assert r.status_code == 201
    j = r.json()
    assert j["decision"] in DECISIONES and j["modo"] == "normal" and 0 <= j["puntaje_riesgo"] <= 1
    assert j["modelo"]["conjunto_variables"] == "completo" and j["latencia_ms"] > 0 and j["repetida"] is False
    assert j["id_transaccion"] == "T1" and len(j["id_evaluacion"]) == 36


def test_un_reintento_devuelve_lo_mismo_y_no_se_cuenta_dos_veces(ctx):
    a, b = ctx.evaluar(pago()), ctx.evaluar(pago())
    assert (a.status_code, b.status_code) == (201, 200)
    assert b.json()["repetida"] is True and a.json()["id_evaluacion"] == b.json()["id_evaluacion"]
    con = sqlite3.connect(ctx.bd)
    assert con.execute("SELECT COUNT(*) FROM evaluaciones").fetchone()[0] == 1


def test_reintento_sin_fecha_tambien_es_el_mismo(ctx):
    p = pago(sin=["fecha_pago"])
    assert ctx.evaluar(p).status_code == 201 and ctx.evaluar(p).status_code == 200


def test_mismo_id_con_datos_distintos_es_conflicto(ctx):
    ctx.evaluar(pago())
    r = ctx.evaluar(pago(monto_cop=999999))
    assert r.status_code == 409 and r.headers["content-type"].startswith("application/problem+json")


def test_el_mismo_id_en_otro_comercio_es_otra_transaccion(ctx):
    assert ctx.evaluar(pago(), "A").status_code == 201 and ctx.evaluar(pago(), "B").status_code == 201


def test_un_pago_muy_sospechoso_no_se_aprueba(ctx):
    p = pago("S1", "C9", monto_cop=4_800_000, categoria="ELECTRONICA",
             dispositivo={"estado": "NO_RECONOCIDO", "zona_horaria": "Europe/Moscow"},
             red={"tipo_ip": "VPN", "pais": "Rusia", "distancia_a_ciudad_km": 10700, "es_datacenter": True, "reputacion": 30},
             identidad={"autenticacion_2fa": "EXIGIDA_FALLIDA", "intentos_fallidos_24h": 4, "edad_cuenta_dias": 1, "cambio_credenciales_7d": True},
             comportamiento={"tiempo_formulario_seg": 3, "pego_numero_tarjeta": True, "mouse_natural": 0.05})
    r = ctx.evaluar(p).json()
    assert r["decision"] == "RECHAZADA" and r["decision_final"] == "RECHAZADA"
    assert any(x["regla"] == "dispositivo_no_reconocido_con_ip_anonima" for x in r["reglas_activadas"])


# ------------------------------------------------------------------ validación y datos de tarjeta
@pytest.mark.parametrize("cambios", [
    {"monto_cop": -5}, {"monto_cop": 0}, {"monto_cop": "mucho"}, {"monto_cop": 10 ** 12}, {"moneda": "USD"},
    {"metodo_pago": "CHEQUE"}, {"canal_pago": "FAX"}, {"id_transaccion": "con espacios"}, {"id_transaccion": ""},
    {"id_cliente": "x" * 65}, {"id_cliente": "a'; DROP TABLE evaluaciones;--"}, {"ciudad": "B"}, {"ciudad": "z" * 61},
    {"fecha_pago": "ayer"}, {"fecha_pago": "2026-11-13T11:55:00"}, {"fecha_pago": "2026-01-01T00:00:00"},
    {"dispositivo": {"resolucion_pantalla": "grande"}}, {"red": {"reputacion": 101}}, {"red": {"distancia_a_ciudad_km": -1}},
    {"comportamiento": {"mouse_natural": 1.5}}, {"identidad": {"edad_cuenta_dias": -3}},
])
def test_datos_invalidos_dan_422_con_formato_estandar(ctx, cambios):
    r = ctx.evaluar(pago(**cambios))
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")


def test_campos_desconocidos_se_rechazan(ctx):
    p = pago()
    p["campo_inventado"] = 1
    assert ctx.evaluar(p).status_code == 422


@pytest.mark.parametrize("campo,valor", [("numero_tarjeta", "4111111111111111"), ("cvv", "987"), ("card_number", "SECRETO-ABC"), ("pan", "SECRETO-DEF"),
                                          ("vencimiento", "12/29")])
def test_los_campos_de_tarjeta_se_rechazan_y_no_se_repiten_en_la_respuesta(ctx, campo, valor):
    p = pago()
    p[campo] = valor
    r = ctx.evaluar(p)
    assert r.status_code == 422 and valor not in r.text and "datos de tarjeta" in r.text


def test_un_numero_de_tarjeta_escondido_en_un_texto_se_rechaza_sin_repetirlo(ctx):
    pan = "4111 1111 1111 1111"
    for campo in ("id_cliente", "ciudad"):
        p = pago()
        p[campo] = pan.replace(" ", "-") if campo == "id_cliente" else f"Bogotá {pan}"
        r = ctx.evaluar(p)
        assert r.status_code == 422 and "4111" not in r.text and "1111" not in r.text, campo


def test_pego_numero_tarjeta_es_un_campo_permitido(ctx):
    assert ctx.evaluar(pago(comportamiento={"pego_numero_tarjeta": True})).status_code == 201


def test_solo_se_rechazan_los_numeros_que_parecen_tarjeta_de_verdad(ctx):
    """Un código largo se acepta salvo que pase la verificación de tarjetas (Luhn)."""
    assert ctx.evaluar(pago("T1", "4111111111111111")).status_code == 422             # número de tarjeta de prueba, válido
    assert ctx.evaluar(pago("T2", "4111111111111112")).status_code == 201             # mismo largo, no es tarjeta
    assert ctx.evaluar(pago("T3", "CLI-4111")).status_code == 201


def test_peticion_demasiado_grande_da_413(tmp_path, artefacto):
    limite = len(json.dumps(pago()).encode()) + 500
    c = construir_contexto(tmp_path, artefacto, tamano_maximo_bytes=limite)
    assert c.evaluar(pago()).status_code == 201                                         # una normal sí pasa
    grande = json.dumps(pago("T2")) + " " * (limite + 1)
    r = c.cliente.post("/v1/evaluaciones", content=grande, headers={**c.h("A"), "Content-Type": "application/json"})
    assert r.status_code == 413 and r.headers["content-type"].startswith("application/problem+json")


def test_texto_raro_no_rompe_nada_ni_inyecta_sql(ctx):
    r = ctx.evaluar(pago(ciudad="'; DROP TABLE evaluaciones;--"))
    assert r.status_code == 201
    r2 = ctx.evaluar(pago("T2", ciudad="Bogotá 🚀 ñandú \u0000"))
    assert r2.status_code in (201, 422)
    con = sqlite3.connect(ctx.bd)
    assert con.execute("SELECT COUNT(*) FROM evaluaciones").fetchone()[0] >= 1


def test_json_roto_y_metodos_no_permitidos(ctx):
    r = ctx.cliente.post("/v1/evaluaciones", content="{no es json", headers={**ctx.h("A"), "Content-Type": "application/json"})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")
    assert ctx.cliente.delete("/v1/evaluaciones", headers=ctx.h("A")).status_code == 405
    assert ctx.cliente.get("/v1/no-existe", headers=ctx.h("A")).status_code == 404


def test_id_con_formato_invalido_en_la_ruta(ctx):
    assert ctx.cliente.get("/v1/evaluaciones/no-es-un-uuid", headers=ctx.h("A")).status_code == 422


# ------------------------------------------------------------------ modo degradado
def test_faltan_datos_del_modelo_queda_en_revision_sin_aprobar_a_ciegas(ctx):
    r = ctx.evaluar(pago_minimo("M1")).json()
    assert r["modo"] == "degradado" and r["decision"] == "EN_REVISION" and r["decision_final"] is None
    assert r["puntaje_riesgo"] is None and "faltan datos" in r["motivos"][0]
    assert r["revision"]["estado"] == "pendiente"


def test_en_modo_degradado_una_regla_de_seguridad_puede_rechazar(ctx):
    p = pago_minimo("M2", dispositivo={"estado": "NO_RECONOCIDO"}, red={"tipo_ip": "VPN"})
    r = ctx.evaluar(p).json()
    assert r["modo"] == "degradado" and r["decision"] == "RECHAZADA"


def test_si_el_modelo_falla_no_se_cae_el_servicio(ctx, monkeypatch):
    def roto(*a, **k):
        raise RuntimeError("el modelo se rompió")
    monkeypatch.setattr(predecir, "analizar_transaccion", roto)
    r = ctx.evaluar(pago("F1"))
    assert r.status_code == 201 and r.json()["modo"] == "degradado" and r.json()["decision"] == "EN_REVISION"
    assert "falla interna" in r.json()["motivos"][0] and "se rompió" not in r.text           # no se filtran detalles


def test_un_error_inesperado_da_500_estandar_sin_detalles(ctx, monkeypatch):
    def roto(*a, **k):
        raise RuntimeError("secreto interno")
    monkeypatch.setattr(ctx.app.state.servicio, "obtener", roto)
    c = TestClient(ctx.app, raise_server_exceptions=False)
    r = c.get("/v1/evaluaciones/" + "0" * 8 + "-0000-0000-0000-" + "0" * 12, headers=ctx.h("A"))
    assert r.status_code == 500 and "secreto interno" not in r.text
    assert r.headers["content-type"].startswith("application/problem+json")


# ------------------------------------------------------------------ aislamiento entre comercios
def test_un_comercio_no_ve_ni_toca_lo_de_otro(ctx):
    id_a = ctx.evaluar(pago("A1"), "A").json()["id_evaluacion"]
    assert ctx.cliente.get(f"/v1/evaluaciones/{id_a}", headers=ctx.h("A")).status_code == 200
    assert ctx.cliente.get(f"/v1/evaluaciones/{id_a}", headers=ctx.h("B")).status_code == 404       # 404, no 403: no revela que existe
    assert ctx.post(f"/v1/evaluaciones/{id_a}/resultado", {"resultado": "LEGITIMA"}, "B").status_code == 404
    assert ctx.cliente.get(f"/v1/evaluaciones/{id_a}", headers=ctx.h("analista")).status_code == 200
    assert ctx.cliente.get(f"/v1/evaluaciones/{id_a}", headers=ctx.h("admin")).status_code == 200


def test_el_historial_de_un_comercio_no_afecta_a_otro(ctx):
    """El mismo id_cliente en dos comercios son clientes distintos: el B no hereda el historial del A."""
    for i in range(5):
        ctx.evaluar(pago(f"H{i}", "CLIENTE-X", monto_cop=100000), "A")
    con = sqlite3.connect(ctx.bd)
    ctx.evaluar(pago("H9", "CLIENTE-X"), "B")
    n_b = con.execute("SELECT COUNT(*) FROM evaluaciones WHERE comercio_id=? AND id_cliente='CLIENTE-X'", (ctx.comercios["B"],)).fetchone()[0]
    assert n_b == 1


def test_la_lista_negra_es_de_cada_comercio(ctx):
    assert ctx.cliente.put("/v1/lista-negra/MALO-1", json={"motivo": "contracargos"}, headers=ctx.h("A")).status_code == 200
    assert [x["id_cliente"] for x in ctx.cliente.get("/v1/lista-negra", headers=ctx.h("A")).json()] == ["MALO-1"]
    assert ctx.cliente.get("/v1/lista-negra", headers=ctx.h("B")).json() == []
    ra = ctx.evaluar(pago("L1", "MALO-1"), "A").json()
    rb = ctx.evaluar(pago("L1", "MALO-1"), "B").json()
    assert ra["decision"] == "RECHAZADA" and any(x["regla"] == "lista_negra_cliente" for x in ra["reglas_activadas"])
    assert not any(x["regla"] == "lista_negra_cliente" for x in rb["reglas_activadas"])


def test_quitar_de_la_lista_negra(ctx):
    ctx.cliente.put("/v1/lista-negra/MALO-2", headers=ctx.h("A"))                      # sin cuerpo también sirve
    assert ctx.cliente.delete("/v1/lista-negra/MALO-2", headers=ctx.h("A")).status_code == 204
    assert ctx.cliente.delete("/v1/lista-negra/MALO-2", headers=ctx.h("A")).status_code == 404
    r = ctx.evaluar(pago("L2", "MALO-2")).json()
    assert not any(x["regla"] == "lista_negra_cliente" for x in r["reglas_activadas"])


# ------------------------------------------------------------------ revisiones y resultados
def _en_revision(ctx, tx="R1", quien="A"):
    r = ctx.evaluar(pago_minimo(tx), quien).json()
    assert r["decision"] == "EN_REVISION"
    return r["id_evaluacion"]


def test_flujo_completo_de_revision(ctx):
    id_eval = _en_revision(ctx)
    cola = ctx.cliente.get("/v1/revisiones", headers=ctx.h("analista")).json()
    assert cola["total"] == 1 and cola["revisiones"][0]["id_evaluacion"] == id_eval
    assert ctx.cliente.get(f"/v1/evaluaciones/{id_eval}", headers=ctx.h("A")).json()["decision_final"] is None
    r = ctx.post(f"/v1/revisiones/{id_eval}/resolver", {"decision": "APROBADA", "nota": "cliente confirmó por teléfono"}, "analista")
    assert r.status_code == 200 and r.json()["decision_final"] == "APROBADA" and r.json()["decision"] == "EN_REVISION"
    visto = ctx.cliente.get(f"/v1/evaluaciones/{id_eval}", headers=ctx.h("A")).json()
    assert visto["decision_final"] == "APROBADA" and visto["revision"]["estado"] == "resuelta"
    assert ctx.cliente.get("/v1/revisiones", headers=ctx.h("analista")).json()["total"] == 0
    assert ctx.post(f"/v1/revisiones/{id_eval}/resolver", {"decision": "RECHAZADA"}, "analista").status_code == 409


def test_resolver_exige_permiso_y_existencia(ctx):
    id_eval = _en_revision(ctx)
    assert ctx.post(f"/v1/revisiones/{id_eval}/resolver", {"decision": "APROBADA"}, "A").status_code == 403
    assert ctx.post("/v1/revisiones/" + "0" * 8 + "-0000-0000-0000-" + "0" * 12 + "/resolver", {"decision": "APROBADA"}, "analista").status_code == 404
    assert ctx.post(f"/v1/revisiones/{id_eval}/resolver", {"decision": "TAL_VEZ"}, "analista").status_code == 422
    sin_revision = ctx.evaluar(pago("N1")).json()
    if sin_revision["decision"] != "EN_REVISION":
        assert ctx.post(f"/v1/revisiones/{sin_revision['id_evaluacion']}/resolver", {"decision": "APROBADA"}, "analista").status_code == 404


def test_informar_resultado(ctx):
    id_eval = ctx.evaluar(pago("Z1")).json()["id_evaluacion"]
    url = f"/v1/evaluaciones/{id_eval}/resultado"
    assert ctx.post(url, {"resultado": "FRAUDE_CONFIRMADO", "nota": "contracargo 123"}).status_code == 200
    assert ctx.post(url, {"resultado": "FRAUDE_CONFIRMADO"}).status_code == 200                # repetir lo mismo es seguro
    assert ctx.post(url, {"resultado": "LEGITIMA"}).status_code == 409                           # cambiarlo no
    assert ctx.cliente.get(f"/v1/evaluaciones/{id_eval}", headers=ctx.h("A")).json()["resultado_informado"] == "FRAUDE_CONFIRMADO"
    assert ctx.post(url, {"resultado": "NI_IDEA"}).status_code == 422
    assert ctx.post(url, {"resultado": "LEGITIMA", "nota": "4111 1111 1111 1111"}).status_code == 422


# ------------------------------------------------------------------ límite de peticiones
def test_limite_de_peticiones_da_429_con_retry_after(tmp_path, artefacto):
    c = construir_contexto(tmp_path, artefacto, limite_peticiones_por_minuto=5)
    codigos = [c.cliente.get("/v1/modelo", headers=c.h("A")) for _ in range(7)]
    assert [r.status_code for r in codigos] == [200] * 5 + [429, 429]
    assert int(codigos[-1].headers["Retry-After"]) >= 1
    assert c.cliente.get("/v1/modelo", headers=c.h("B")).status_code == 200            # el límite es por clave


def test_limitador_olvida_las_peticiones_viejas():
    lim = LimitadorTasa(2)
    assert lim.permitir(1, 0)[0] and lim.permitir(1, 1)[0] and not lim.permitir(1, 2)[0]
    assert lim.permitir(1, 61)[0]


# ------------------------------------------------------------------ auditoría
def test_la_auditoria_registra_y_no_se_puede_editar_ni_borrar(ctx):
    ctx.evaluar(pago("AU1"))
    con = sqlite3.connect(ctx.bd)
    acciones = [r[0] for r in con.execute("SELECT accion FROM auditoria")]
    assert "evaluacion" in acciones and "clave_creada" in acciones
    for sentencia in ("UPDATE auditoria SET accion='x'", "DELETE FROM auditoria"):
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(sentencia)
    detalle = " ".join(r[0] or "" for r in con.execute("SELECT detalle FROM auditoria"))
    assert all(clave not in detalle for clave in ctx.claves.values())


# ------------------------------------------------------------------ las variables en línea = las del entrenamiento
def _a_solicitud(fila: pd.Series, id_tx: str) -> dict:
    inv = lambda tabla, v: next(k for k, x in tabla.items() if x == v)
    ip = lambda x: None if pd.isna(x) else x
    return {
        "id_transaccion": id_tx, "id_cliente": fila["id_cliente"], "fecha_pago": fila["fecha_pago"].replace(" ", "T"),
        "monto_cop": int(fila["monto_cop"]), "metodo_pago": inv(traduccion.METODO_PAGO, fila["metodo_pago"]),
        "canal_pago": inv(traduccion.CANAL, fila["canal_pago"]), "categoria": inv(traduccion.CATEGORIA, fila["detalle_pago"].split(" - ")[0]),
        "ciudad": fila["localizacion"].split(",")[0],
        "dispositivo": {"tipo": inv(traduccion.TIPO_DISPOSITIVO, fila["dispositivo"]),
                        "estado": inv(traduccion.ESTADO_DISPOSITIVO, fila["estado_dispositivo"]), "huella": fila["huella_dispositivo"].replace(" ", "_"),
                        "sistema_operativo": fila["sistema_operativo"], "navegador": fila["navegador"],
                        "resolucion_pantalla": fila["resolucion_pantalla"], "zona_horaria": fila["zona_horaria_dispositivo"],
                        "es_emulador": bool(fila["es_emulador"])},
        "red": {"tipo_ip": inv(traduccion.TIPO_IP, fila["tipo_ip"]), "pais": fila["ip_pais"],
                "ciudad_estimada": fila["ip_ciudad_estimada"], "distancia_a_ciudad_km": ip(fila["distancia_ip_ciudad_km"]),
                "es_datacenter": bool(fila["ip_es_datacenter"]), "reputacion": int(fila["ip_reputacion"])},
        "identidad": {"kyc_nivel": inv(traduccion.NIVEL_KYC, fila["kyc_nivel"]), "edad_cuenta_dias": int(fila["edad_cuenta_dias"]),
                      "autenticacion_2fa": inv(traduccion.AUTENTICACION_2FA, fila["autenticacion_2fa"]),
                      "intentos_fallidos_24h": int(fila["intentos_fallidos_24h"]),
                      "cambio_credenciales_7d": bool(fila["cambio_credenciales_7d"])},
        "comportamiento": {"tiempo_formulario_seg": ip(fila["tiempo_formulario_seg"]), "velocidad_tecleo_ms": ip(fila["velocidad_tecleo_ms"]),
                           "pego_numero_tarjeta": None if pd.isna(fila["pego_numero_tarjeta"]) else bool(fila["pego_numero_tarjeta"]),
                           "mouse_natural": ip(fila["mouse_natural"])},
    }


def test_las_variables_calculadas_en_linea_son_iguales_a_las_del_entrenamiento(tmp_path, artefacto):
    """Si esto falla, el modelo vería en producción números distintos a los que vio al entrenar."""
    ctx = construir_contexto(tmp_path, artefacto, dias_pasado_maximo=400)
    ctx.app.state.servicio.reloj = lambda: datetime(2026, 11, 13)
    crudo = hacer_datos_completos(3000, semilla=3)
    top = crudo["id_cliente"].value_counts().head(8).index
    sub = crudo[crudo["id_cliente"].isin(top)].copy()
    sub = datos.agregar_columnas_basicas(sub).sort_values("fecha", kind="stable")
    sub["fecha_pago"] = sub["fecha"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    lote = variables.construir(sub.reset_index(drop=True), "completo")
    capturadas = {}
    original = ctx.app.state.servicio._variables

    def espiar(con, comercio_id, fila, id_eval):
        pago_calculado, actual = original(con, comercio_id, fila, id_eval)
        capturadas[fila["fecha"] + "|" + fila["id_cliente"]] = pago_calculado
        return pago_calculado, actual

    ctx.app.state.servicio._variables = espiar
    sub = sub.reset_index(drop=True)
    for i, fila in sub.iterrows():
        r = ctx.evaluar(_a_solicitud(fila, f"EQ-{i}"))
        assert r.status_code == 201, r.text
    meta = artefacto["meta"]
    comparadas = 0
    for i, fila in sub.iterrows():
        clave = fila["fecha"].isoformat() + "|" + fila["id_cliente"]
        en_linea, por_lote = capturadas[clave], lote.iloc[i]
        for col in meta["numericas"] + meta["categoricas"]:
            if col == "transacciones_ultimas_24h":
                continue                                                  # en el dataset de prueba es un número al azar
            a, b = en_linea[col], por_lote[col]
            if isinstance(b, str):
                assert a == b, (i, col, a, b)
            else:
                assert abs(float(a) - float(b)) < 1e-6, (i, col, a, b)
            comparadas += 1
    assert comparadas > 3000


def test_n24_en_linea_cuenta_las_compras_de_las_ultimas_24_horas_incluyendo_la_actual(ctx):
    base = pd.Timestamp(FECHA)
    for k, horas in enumerate([30, 20, 5, 0]):
        f = (base - pd.Timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%S")
        ctx.evaluar(pago(f"W{k}", "CW", fecha_pago=f))
    con = sqlite3.connect(ctx.bd)
    n24 = [json.loads(r[0])["transacciones_ultimas_24h"] for r in con.execute(
        "SELECT fila_json FROM evaluaciones WHERE id_cliente='CW' ORDER BY fecha")]
    assert n24 == [1, 2, 2, 3]            # la de hace 30 h cuenta para la de hace 20 h (10 h antes), pero no para las siguientes


# ------------------------------------------------------------------ concurrencia (muchas rondas)
RONDAS = 30
HILOS = 8


def _en_paralelo(app, funcion):
    with ThreadPoolExecutor(HILOS) as ex:
        futuros = [ex.submit(funcion, TestClient(app), i) for i in range(HILOS)]
        return [f.result() for f in futuros]


def test_concurrencia_el_mismo_pago_enviado_a_la_vez_se_evalua_una_sola_vez(ctx):
    for ronda in range(RONDAS):
        cuerpo = pago(f"CC-{ronda}", f"CLI-CC-{ronda}")
        r = _en_paralelo(ctx.app, lambda c, i: c.post("/v1/evaluaciones", json=cuerpo, headers=ctx.h("A")))
        codigos = sorted(x.status_code for x in r)
        assert codigos == [200] * (HILOS - 1) + [201], (ronda, codigos)
        assert len({x.json()["id_evaluacion"] for x in r}) == 1
    con = sqlite3.connect(ctx.bd)
    assert con.execute("SELECT COUNT(*) FROM evaluaciones").fetchone()[0] == RONDAS


def test_concurrencia_resolver_la_misma_revision_a_la_vez_solo_una_vez(ctx):
    for ronda in range(RONDAS):
        id_eval = _en_revision(ctx, f"CR-{ronda}")
        r = _en_paralelo(ctx.app, lambda c, i: c.post(f"/v1/revisiones/{id_eval}/resolver",
                                                      json={"decision": "APROBADA" if i % 2 else "RECHAZADA"}, headers=ctx.h("analista")))
        codigos = sorted(x.status_code for x in r)
        assert codigos == [200] + [409] * (HILOS - 1), (ronda, codigos)
        con = sqlite3.connect(ctx.bd)
        ganadora = [x.json()["decision_final"] for x in r if x.status_code == 200][0]
        guardada = con.execute("SELECT decision_analista FROM revisiones WHERE id_evaluacion=?", (id_eval,)).fetchone()[0]
        assert guardada == ganadora                          # lo que se guardó es lo que se respondió


def test_concurrencia_pagos_distintos_del_mismo_cliente_quedan_todos_en_el_historial(ctx):
    for ronda in range(RONDAS):
        cliente = f"CLI-MULTI-{ronda}"
        r = _en_paralelo(ctx.app, lambda c, i: c.post("/v1/evaluaciones", json=pago(f"MU-{ronda}-{i}", cliente), headers=ctx.h("A")))
        assert all(x.status_code == 201 for x in r), (ronda, [x.status_code for x in r])
        con = sqlite3.connect(ctx.bd)
        assert con.execute("SELECT COUNT(*) FROM evaluaciones WHERE id_cliente=?", (cliente,)).fetchone()[0] == HILOS
        siguiente = ctx.evaluar(pago(f"MU-{ronda}-ultimo", cliente, fecha_pago="2026-11-12T11:59:00")).json()
        assert siguiente["modo"] == "normal"
        n24 = json.loads(con.execute("SELECT fila_json FROM evaluaciones WHERE id_transaccion=?", (f"MU-{ronda}-ultimo",)).fetchone()[0])
        assert n24["transacciones_ultimas_24h"] == HILOS + 1        # vio las 8 anteriores + la actual


def test_concurrencia_resultados_contradictorios_a_la_vez_gana_uno_solo(ctx):
    for ronda in range(RONDAS):
        id_eval = ctx.evaluar(pago(f"RS-{ronda}", f"CLI-RS-{ronda}")).json()["id_evaluacion"]
        r = _en_paralelo(ctx.app, lambda c, i: c.post(f"/v1/evaluaciones/{id_eval}/resultado",
                                                      json={"resultado": "LEGITIMA" if i % 2 else "FRAUDE_CONFIRMADO"}, headers=ctx.h("A")))
        con = sqlite3.connect(ctx.bd)
        guardado = con.execute("SELECT resultado FROM resultados WHERE id_evaluacion=?", (id_eval,)).fetchall()
        assert len(guardado) == 1
        for i, x in enumerate(r):
            pedido = "LEGITIMA" if i % 2 else "FRAUDE_CONFIRMADO"
            assert x.status_code == (200 if pedido == guardado[0][0] else 409), (ronda, i, x.status_code)


# ------------------------------------------------------------------ ejemplos de Swagger y modo demo
from modelo_ia.api import demo as demo_mod                       # noqa: E402
from modelo_ia.api.ejemplos import EJEMPLOS_SOLICITUD, ESPERADOS  # noqa: E402


def test_los_ejemplos_de_swagger_son_validos_y_el_de_tarjeta_no():
    assert set(ESPERADOS) == set(EJEMPLOS_SOLICITUD)
    for nombre, ej in EJEMPLOS_SOLICITUD.items():
        if nombre.startswith("09"):
            with pytest.raises(Exception):
                esquemas.SolicitudEvaluacion.model_validate(ej["value"])
        else:
            esquemas.SolicitudEvaluacion.model_validate(ej["value"])
    ids = [e["value"]["id_transaccion"] for e in EJEMPLOS_SOLICITUD.values()]
    assert len(set(ids)) == len(ids)
    assert all("fecha_pago" not in e["value"] for e in EJEMPLOS_SOLICITUD.values())          # así no se vencen


def test_los_ejemplos_aparecen_en_el_contrato_publicado(ctx):
    spec = ctx.cliente.get("/openapi.json").json()
    publicados = spec["paths"]["/v1/evaluaciones"]["post"]["requestBody"]["content"]["application/json"]["examples"]
    assert set(publicados) == set(EJEMPLOS_SOLICITUD)
    assert all("→" in e["summary"] for e in publicados.values())               # cada ejemplo dice qué se espera


def test_los_ejemplos_dan_lo_esperado_con_el_modelo_actual(tmp_path):
    """Con el modelo que de verdad se sirve. Si cambias el modelo y esto falla, revisa los ejemplos (y ESPERADOS)."""
    if not (config.CARPETA_MODELOS / "modelo_actual.joblib").exists() or not (config.CARPETA_MODELOS / "modelo_actual_sin_estado.joblib").exists():
        pytest.skip("faltan modelo_actual.joblib o modelo_actual_sin_estado.joblib")
    app = crear_app(Ajustes(ruta_bd=tmp_path / "e.sqlite3", pepper="p", limite_peticiones_por_minuto=10 ** 6), predecir.cargar_artefacto(),
                    artefacto_sin_estado=predecir.cargar_artefacto_sin_estado())
    clave = app.state.almacen.crear_clave(app.state.almacen.crear_comercio("t"), "comercio")
    c, h = TestClient(app), {"X-API-Key": clave}
    for nombre, ej in EJEMPLOS_SOLICITUD.items():
        if nombre == "08_lista_negra":
            assert c.put("/v1/lista-negra/DEMO-CLIENTE-MALO", headers=h).status_code == 200
        r = c.post("/v1/evaluaciones", json=ej["value"], headers=h)
        obtenido = r.json()["decision"] if r.status_code == 201 else r.status_code
        assert obtenido == ESPERADOS[nombre], (nombre, obtenido, r.text[:200])


@pytest.fixture
def demo(tmp_path, monkeypatch, artefacto):
    carpeta = tmp_path / "demo"
    monkeypatch.setattr(demo_mod, "CARPETA", carpeta)
    monkeypatch.setattr(demo_mod, "BD", carpeta / "demo.sqlite3")
    monkeypatch.setattr(demo_mod, "CLAVES", carpeta / "claves_demo.txt")
    monkeypatch.setattr(demo_mod, "PEPPER", carpeta / "pepper_demo.txt")
    monkeypatch.setattr(predecir, "cargar_artefacto", lambda ruta=None: artefacto)
    return demo_mod


def _claves_de(archivo: Path):
    return dict(l.split("=", 1) for l in archivo.read_text().splitlines() if "=" in l)


def test_demo_cada_pagina_viene_autorizada_con_su_propia_clave(demo):
    app = demo.crear_app_demo()
    c = TestClient(app)
    claves = {k.lower(): v for k, v in _claves_de(demo.CLAVES).items()}
    for ruta, rol in (("/docs", "comercio"), ("/docs-analista", "analista"), ("/docs-admin", "admin")):
        html = c.get(ruta).text
        assert f"/init/{rol}.js" in html and not any(clave in html for clave in claves.values())     # la página no lleva claves
        js = c.get(f"/init/{rol}.js")
        assert f'preauthorizeApiKey("APIKeyHeader", "{claves[rol]}")' in js.text and js.headers["Cache-Control"] == "no-store"
        for otro, clave in claves.items():
            if otro != rol:
                assert clave not in js.text
    assert c.get("/", follow_redirects=False).headers["location"] == "/docs"


def test_demo_las_claves_son_privadas_y_no_salen_en_el_contrato(demo):
    app = demo.crear_app_demo()
    assert oct(demo.CLAVES.stat().st_mode & 0o777) == "0o600" and oct(demo.PEPPER.stat().st_mode & 0o777) == "0o600"
    assert oct(demo.CARPETA.stat().st_mode & 0o777) == "0o700"
    c = TestClient(app)
    contrato = c.get("/openapi.json").text
    assert all(clave not in contrato for clave in _claves_de(demo.CLAVES).values())


def test_demo_las_claves_funcionan_cada_una_en_su_rol(demo):
    app = demo.crear_app_demo()
    c = TestClient(app)
    k = {n.lower(): v for n, v in _claves_de(demo.CLAVES).items()}
    assert c.post("/v1/evaluaciones", json=EJEMPLOS_SOLICITUD["01_aprobada_pago_normal"]["value"], headers={"X-API-Key": k["comercio"]}).status_code == 201
    assert c.get("/v1/revisiones", headers={"X-API-Key": k["analista"]}).status_code == 200
    assert c.post("/v1/comercios", json={"nombre": "Otra"}, headers={"X-API-Key": k["admin"]}).status_code == 201
    assert c.get("/v1/revisiones", headers={"X-API-Key": k["comercio"]}).status_code == 403


def test_demo_reutiliza_las_claves_y_reiniciar_las_cambia_y_borra_la_base(demo):
    demo.crear_app_demo()
    primeras = _claves_de(demo.CLAVES)
    demo.crear_app_demo()
    assert _claves_de(demo.CLAVES) == primeras                       # sin reiniciar, todo sigue igual
    app = demo.crear_app_demo(reiniciar=True)
    nuevas = _claves_de(demo.CLAVES)
    assert set(nuevas.values()).isdisjoint(primeras.values())
    assert TestClient(app).get("/v1/modelo", headers={"X-API-Key": nuevas["COMERCIO"]}).status_code == 200


def test_demo_solo_escucha_en_tu_computador(demo, monkeypatch):
    llamadas = []
    monkeypatch.setattr(demo_mod.uvicorn, "run", lambda app, **kw: llamadas.append(kw))
    demo.main(["--port", "8123"])
    assert llamadas == [{"host": "127.0.0.1", "port": 8123}]
    with pytest.raises(SystemExit):
        demo.main(["--host", "0.0.0.0"])                             # no existe esa opción: no se puede exponer a la red


# ------------------------------------------------------------------ documentación local (sin CDN)
import hashlib                                                     # noqa: E402
from modelo_ia.api import documentacion                            # noqa: E402


def test_la_documentacion_no_depende_de_ninguna_direccion_externa(ctx):
    for ruta in ("/docs", "/redoc", "/init/base.js"):
        r = ctx.cliente.get(ruta)
        assert r.status_code == 200
        assert "http://" not in r.text.replace("http://www.w3.org", "") and "https://" not in r.text and "jsdelivr" not in r.text, ruta
    assert ctx.cliente.get("/estaticos/swagger-ui-bundle.js").status_code == 200
    assert ctx.cliente.get("/estaticos/redoc.standalone.js").status_code == 200


def test_los_archivos_de_terceros_no_estan_alterados():
    """Cada archivo debe coincidir con la huella del manifiesto."""
    lineas = [l.split("  ", 2) for l in (documentacion.ESTATICOS / "MANIFIESTO.txt").read_text().splitlines() if l and not l.startswith("#")]
    assert len(lineas) == 5
    for huella, archivo, _ in lineas:
        assert hashlib.sha256((documentacion.ESTATICOS / archivo).read_bytes()).hexdigest() == huella, archivo


def test_ningun_archivo_de_terceros_pide_recursos_a_internet():
    """ReDoc traía un logo de cdn.redoc.ly: se reemplazó por uno local (y se anotó en el manifiesto)."""
    redoc = (documentacion.ESTATICOS / "redoc.standalone.js").read_bytes()
    assert b"cdn.redoc.ly" not in redoc and b"/estaticos/logo-vacio.svg" in redoc
    assert "MODIFICADO" in (documentacion.ESTATICOS / "MANIFIESTO.txt").read_text()


def test_politica_de_contenido_de_la_documentacion_primero_observa_y_luego_bloquea(tmp_path, artefacto):
    observa = construir_contexto(tmp_path / "a", artefacto)
    r = observa.cliente.get("/docs")
    assert "Content-Security-Policy-Report-Only" in r.headers and "Content-Security-Policy" not in r.headers
    assert "script-src 'self'" in r.headers["Content-Security-Policy-Report-Only"] and "unsafe-inline" not in r.headers["Content-Security-Policy-Report-Only"].split("style-src")[0]
    bloquea = construir_contexto(tmp_path / "b", artefacto, csp_modo="enforce")
    csp = bloquea.cliente.get("/redoc").headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp                                          # al bloquear sí se incluye
    assert "frame-ancestors" not in r.headers["Content-Security-Policy-Report-Only"]  # al observar, el navegador lo rechaza


def test_la_pagina_base_no_trae_clave_ni_preautorizacion(ctx):
    js = ctx.cliente.get("/init/base.js").text
    assert "preauthorize" not in js and "SwaggerUIBundle" in js


# ------------------------------------------------------------------ pantalla de analistas
import re                                                          # noqa: E402
from modelo_ia.api import panel as panel_mod                       # noqa: E402


def test_la_cola_trae_un_resumen_del_pago_sin_huella_ni_tarjeta(ctx):
    _en_revision(ctx, "P1")
    fila = ctx.cliente.get("/v1/revisiones", headers=ctx.h("analista")).json()["revisiones"][0]
    r = fila["resumen_pago"]
    assert r["monto_cop"] == 200000 and r["metodo_pago"] == "Visa" and r["localizacion"] == "Bogotá, Colombia"
    assert not {"huella_dispositivo", "numero_tarjeta", "fila_json"} & set(r)


def test_el_panel_se_sirve_con_politica_de_contenido_y_sin_cache(ctx):
    r = ctx.cliente.get("/panel")
    assert r.status_code == 200 and "Revisión manual" in r.text
    assert "Content-Security-Policy-Report-Only" in r.headers and r.headers["Cache-Control"] == "no-store"
    assert "style-src 'self'" in r.headers["Content-Security-Policy-Report-Only"]            # sin estilos en línea
    assert ctx.cliente.get("/estaticos/panel/panel.js").status_code == 200
    assert ctx.cliente.get("/estaticos/panel/panel.css").status_code == 200


def test_el_panel_no_tiene_scripts_ni_estilos_en_linea():
    html = (documentacion.ESTATICOS / "panel" / "index.html").read_text(encoding="utf-8")
    assert not re.findall(r"<script(?![^>]*\bsrc=)", html) and "style=" not in html and "onclick" not in html.lower()
    assert "http://" not in html and "https://" not in html


def test_el_script_del_panel_nunca_inserta_datos_como_html():
    """Los textos (ciudad, motivos...) vienen de comercios: solo se pueden mostrar como texto."""
    js = (documentacion.ESTATICOS / "panel" / "panel.js").read_text(encoding="utf-8")
    for peligroso in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "localStorage"):
        assert peligroso not in js, peligroso
    assert "textContent" in js


def test_el_panel_normal_no_trae_clave_y_el_de_la_demo_trae_solo_la_del_analista(ctx, demo):
    assert ctx.cliente.get("/init/panel-config.js").text.strip() == "window.PANEL_CONFIG = {};"
    app = demo.crear_app_demo()
    claves = {k.lower(): v for k, v in _claves_de(demo.CLAVES).items()}
    js = TestClient(app).get("/init/panel-config.js")
    assert claves["analista"] in js.text and claves["comercio"] not in js.text and claves["admin"] not in js.text
    assert js.headers["Cache-Control"] == "no-store"
    assert claves["analista"] not in TestClient(app).get("/panel").text                    # la página no lleva la clave


# ------------------------------------------------------------------ SDK y tienda de prueba
from modelo_ia.api import tienda as tienda_mod                     # noqa: E402


def _cuerpo_tienda(**cambios):
    c = {"monto_cop": 80000, "categoria": "ROPA", "ciudad": "Bogotá", "metodo_pago": "VISA", "id_cliente": "cliente-demo-1",
         "simulacion": {"estado_dispositivo": "CONOCIDO", "ip_prueba": "192.0.2.10", "kyc_nivel": "COMPLETO",
                        "edad_cuenta_dias": 700, "autenticacion_2fa": "NO_EXIGIDA", "cambio_credenciales_7d": False},
         "sdk": {"dispositivo": {"tipo": "MACBOOK", "huella": "FP-0123456789abcdef", "sistema_operativo": "macOS", "navegador": "Safari",
                                 "resolucion_pantalla": "1440x900", "zona_horaria": "America/Bogota", "es_emulador": False},
                 "comportamiento": {"tiempo_formulario_seg": 41.5, "velocidad_tecleo_ms": 160, "pego_numero_tarjeta": False, "mouse_natural": 0.7}}}
    c.update(cambios)
    return c


def test_la_tienda_solo_existe_en_la_demo(ctx):
    assert ctx.cliente.get("/tienda").status_code == 404
    assert ctx.cliente.post("/demo/tienda/pagar", json=_cuerpo_tienda()).status_code == 404


def test_la_tienda_evalua_a_traves_del_servicio_sin_que_el_navegador_tenga_clave(demo):
    app = demo.crear_app_demo()
    c = TestClient(app)
    claves = _claves_de(demo.CLAVES).values()
    for ruta in ("/tienda", "/estaticos/tienda/tienda.js", "/estaticos/sdk/antifraude.js", "/estaticos/tienda/index.html"):
        r = c.get(ruta)
        assert r.status_code == 200 and not any(k in r.text for k in claves), ruta
    r = c.post("/demo/tienda/pagar", json=_cuerpo_tienda())
    assert r.status_code == 200 and r.json()["decision"] in DECISIONES and r.json()["modo"] == "normal"
    assert r.json()["id_transaccion"].startswith("TIENDA-")
    con = sqlite3.connect(demo.BD)
    assert con.execute("SELECT COUNT(*) FROM evaluaciones").fetchone()[0] == 1                         # quedó guardada y auditada
    assert con.execute("SELECT COUNT(*) FROM auditoria WHERE accion='evaluacion'").fetchone()[0] == 1


def test_la_tienda_sin_senales_del_sdk_queda_en_revision_y_no_se_cae(demo):
    c = TestClient(demo.crear_app_demo())
    r = c.post("/demo/tienda/pagar", json=_cuerpo_tienda(sdk={}))
    assert r.status_code == 200 and r.json()["modo"] == "degradado" and r.json()["decision"] in ("EN_REVISION", "RECHAZADA")


def test_la_tienda_rechaza_datos_de_tarjeta_sin_repetirlos(demo):
    c = TestClient(demo.crear_app_demo())
    cuerpo = _cuerpo_tienda()
    cuerpo["sdk"]["dispositivo"]["numero_tarjeta"] = "4111111111111111"
    r = c.post("/demo/tienda/pagar", json=cuerpo)
    assert r.status_code == 422 and "4111" not in r.text
    cuerpo2 = _cuerpo_tienda(id_cliente="4111-1111-1111-1111")
    r2 = c.post("/demo/tienda/pagar", json=cuerpo2)
    assert r2.status_code == 422 and "4111" not in r2.text


def test_los_proveedores_simulados_estan_marcados_y_son_deterministas():
    assert tienda_mod.proveedores_simulados({"autenticacion_2fa": "EXIGIDA_FALLIDA"}) == {"intentos_fallidos_24h": 2}
    assert tienda_mod.proveedores_simulados({"autenticacion_2fa": "NO_EXIGIDA"}) == {"intentos_fallidos_24h": 0}
    assert "SIMULADO" in tienda_mod.proveedores_simulados.__doc__
    assert "simulado" in (documentacion.ESTATICOS / "tienda" / "index.html").read_text(encoding="utf-8").lower()


def test_el_sdk_no_lee_lo_que_escribe_el_comprador_ni_envia_nada_por_su_cuenta():
    sdk = (documentacion.ESTATICOS / "sdk" / "antifraude.js").read_text(encoding="utf-8")
    comentarios_fuera = re.sub(r"/\*.*?\*/", "", sdk, flags=re.S)
    for prohibido in (".value", "localStorage", "sessionStorage", "document.cookie", "fetch(", "XMLHttpRequest", "sendBeacon", "innerHTML", "eval(", "canvas"):
        assert prohibido not in comentarios_fuera, prohibido
    assert "paste" in sdk                                              # solo detecta el hecho de pegar


def test_la_tienda_nunca_lee_el_campo_de_la_tarjeta_y_no_inserta_html():
    js = (documentacion.ESTATICOS / "tienda" / "tienda.js").read_text(encoding="utf-8")
    assert not re.search(r'\$\("tarjeta"\)\.(value|textContent|innerText)', js) and 'getElementById("tarjeta")' not in js
    assert js.count('"tarjeta"') == 1                                  # solo se le entrega al SDK para detectar si se pegó
    for peligroso in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "localStorage"):
        assert peligroso not in js, peligroso
    html = (documentacion.ESTATICOS / "tienda" / "index.html").read_text(encoding="utf-8")
    assert not re.findall(r"<script(?![^>]*\bsrc=)", html) and "style=" not in html


# ------------------------------------------------------------------ HTTPS local y varios procesos
def test_hsts_solo_sale_por_https_y_solo_si_esta_activado(tmp_path, artefacto):
    apagado = construir_contexto(tmp_path / "a", artefacto)
    assert "Strict-Transport-Security" not in apagado.cliente.get("/v1/salud").headers
    activado = construir_contexto(tmp_path / "b", artefacto, hsts_segundos=600)
    por_http = TestClient(activado.app, base_url="http://testserver").get("/v1/salud")
    por_https = TestClient(activado.app, base_url="https://testserver").get("/v1/salud")
    assert "Strict-Transport-Security" not in por_http.headers
    assert por_https.headers["Strict-Transport-Security"] == "max-age=600"


def test_el_certificado_de_prueba_se_crea_privado_y_se_reutiliza(tmp_path):
    from modelo_ia.api import certificados
    cert, llave = certificados.asegurar(tmp_path / "c")
    assert cert.read_text().startswith("-----BEGIN CERTIFICATE-----")
    assert oct(llave.stat().st_mode & 0o777) == "0o600" and oct((tmp_path / "c").stat().st_mode & 0o777) == "0o700"
    antes = cert.stat().st_mtime
    assert certificados.asegurar(tmp_path / "c")[0].stat().st_mtime == antes             # no se vuelve a crear


def test_el_servidor_solo_escucha_en_tu_computador_por_defecto_y_acepta_https_y_procesos(monkeypatch):
    from modelo_ia.api import servidor
    llamadas = []
    monkeypatch.setattr(servidor.uvicorn, "run", lambda *a, **kw: llamadas.append((a, kw)))
    monkeypatch.setattr(servidor, "crear_app", lambda: "app")
    servidor.main([])
    assert llamadas[-1][1] == {"host": "127.0.0.1", "port": 8000}
    servidor.main(["--workers", "3", "--port", "9000"])
    assert llamadas[-1][0] == ("modelo_ia.api.servidor:fabrica",) and llamadas[-1][1]["workers"] == 3 and llamadas[-1][1]["factory"] is True


def test_si_llega_otro_pago_del_cliente_mientras_se_calcula_se_recalcula_con_su_historial(ctx, monkeypatch):
    """El cálculo se hace sin bloquear la base; si en ese lapso entra otro pago del cliente, el resultado de este quedó viejo y se rehace."""
    servicio = ctx.app.state.servicio
    original = servicio._calcular
    estado = {"intruso": False, "llamadas": []}

    def calcular_con_intruso(con, comercio_id, fila, solicitud, id_eval):
        resultado = original(con, comercio_id, fila, solicitud, id_eval)
        estado["llamadas"].append(solicitud.id_transaccion)
        if solicitud.id_transaccion == "A" and not estado["intruso"]:
            estado["intruso"] = True
            assert ctx.evaluar(pago("INTRUSO", "CLI-X", fecha_pago="2026-11-12T11:50:00")).status_code == 201     # llega mientras A calculaba
        return resultado

    monkeypatch.setattr(servicio, "_calcular", calcular_con_intruso)
    assert ctx.evaluar(pago("A", "CLI-X")).status_code == 201
    assert estado["llamadas"] == ["A", "INTRUSO", "A"]                          # A se calculó dos veces
    con = sqlite3.connect(ctx.bd)
    n24 = json.loads(con.execute("SELECT fila_json FROM evaluaciones WHERE id_transaccion='A'").fetchone()[0])["transacciones_ultimas_24h"]
    assert n24 == 2                                                             # y la segunda vez ya vio al intruso


def test_tras_varios_conflictos_el_pago_igual_se_guarda(ctx, monkeypatch):
    """Aunque entren pagos del mismo cliente en cada intento, el último intento calcula dentro del bloqueo y siempre termina."""
    servicio = ctx.app.state.servicio
    original = servicio._calcular
    contador = {"n": 0}

    def siempre_con_intruso(con, comercio_id, fila, solicitud, id_eval):
        r = original(con, comercio_id, fila, solicitud, id_eval)
        if solicitud.id_transaccion == "B" and contador["n"] < 3:
            contador["n"] += 1
            ctx.evaluar(pago(f"INT-{contador['n']}", "CLI-Y", fecha_pago="2026-11-12T11:40:00"))
        return r

    monkeypatch.setattr(servicio, "_calcular", siempre_con_intruso)
    r = ctx.evaluar(pago("B", "CLI-Y"))
    assert r.status_code == 201 and r.json()["modo"] == "normal"
    con = sqlite3.connect(ctx.bd)
    assert con.execute("SELECT COUNT(*) FROM evaluaciones WHERE id_cliente='CLI-Y'").fetchone()[0] == 4


def test_la_durabilidad_se_configura_y_solo_acepta_valores_validos(tmp_path):
    from modelo_ia.api.almacen import Almacen
    a = Almacen(tmp_path / "d.sqlite3", "p", "FULL")
    a.inicializar()
    with a.lectura() as con:
        assert con.execute("PRAGMA synchronous").fetchone()[0] == 2                     # FULL
    with Almacen(tmp_path / "d.sqlite3", "p").lectura() as con:
        assert con.execute("PRAGMA synchronous").fetchone()[0] == 1                     # NORMAL por defecto
    with pytest.raises(ValueError):
        Almacen(tmp_path / "e.sqlite3", "p", "NADA")


# ------------------------------------------------------------------ alcance del historial: entrenamiento = servicio
def test_la_salud_avisa_si_el_modelo_se_entreno_con_otro_alcance_que_el_de_la_api(tmp_path, artefacto):
    solo_alcance = lambda ctx: [a for a in ctx.cliente.get("/v1/salud").json()["advertencias"] if "TODOS los comercios" in a]
    assert len(solo_alcance(construir_contexto(tmp_path / "a", artefacto))) == 1        # el modelo de las pruebas no dice con qué alcance se entrenó
    alineado = dict(artefacto, meta=dict(artefacto["meta"], alcance_historial="comercio"))
    assert solo_alcance(construir_contexto(tmp_path / "b", alineado)) == []
    plataforma = dict(artefacto, meta=dict(artefacto["meta"], alcance_historial="plataforma"))
    assert len(solo_alcance(construir_contexto(tmp_path / "c", plataforma))) == 1


def test_el_modelo_en_uso_esta_entrenado_con_el_alcance_que_calcula_la_api():
    if not (config.CARPETA_MODELOS / "modelo_actual.joblib").exists():
        pytest.skip("no hay modelo_actual.joblib")
    assert predecir.cargar_artefacto()["meta"].get("alcance_historial") == "comercio"


def test_las_variables_en_linea_con_dos_comercios_son_iguales_a_las_del_entrenamiento_por_comercio(tmp_path, artefacto):
    """El mismo cliente paga en dos comercios: cada uno solo ve lo suyo, igual que en el entrenamiento con alcance 'comercio'."""
    ctx = construir_contexto(tmp_path, artefacto, dias_pasado_maximo=400)
    ctx.app.state.servicio.reloj = lambda: datetime(2026, 11, 13)
    crudo = hacer_datos_completos(3000, semilla=5)
    top = crudo["id_cliente"].value_counts().head(6).index
    sub = datos.agregar_columnas_basicas(crudo[crudo["id_cliente"].isin(top)].copy()).sort_values("fecha", kind="stable").reset_index(drop=True)
    sub["fecha_pago"] = sub["fecha"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    sub["id_comercio"] = ["A" if i % 2 == 0 else "B" for i in range(len(sub))]               # el mismo cliente compra en los dos
    lote = variables.construir(sub, "completo", "comercio")
    capturadas, original = {}, ctx.app.state.servicio._variables

    def espiar(con, comercio_id, fila, id_eval):
        pago_calculado, actual = original(con, comercio_id, fila, id_eval)
        capturadas[(comercio_id, fila["fecha"], fila["id_cliente"])] = pago_calculado
        return pago_calculado, actual

    ctx.app.state.servicio._variables = espiar
    for i, fila in sub.iterrows():
        assert ctx.evaluar(_a_solicitud(fila, f"DC-{i}"), fila["id_comercio"]).status_code == 201
    meta, comparadas, con_historial_distinto = artefacto["meta"], 0, 0
    for i, fila in sub.iterrows():
        en_linea = capturadas[(ctx.comercios[fila["id_comercio"]], fila["fecha"].isoformat(), fila["id_cliente"])]
        for col in meta["numericas"] + meta["categoricas"]:
            if col == "transacciones_ultimas_24h":
                continue
            a, b = en_linea[col], lote.iloc[i][col]
            assert (a == b) if isinstance(b, str) else abs(float(a) - float(b)) < 1e-6, (i, col, a, b)
            comparadas += 1
    assert comparadas > 2000
    # y la prueba tiene dientes: con el alcance de plataforma, las variables de historial SÍ serían distintas
    plano = variables.construir(sub, "completo", "plataforma")
    assert (plano["n_compras_previas"].values != lote["n_compras_previas"].values).any()


# ------------------------------------------------------------------ IP: la API la ubica y NO la guarda
import logging                                                     # noqa: E402
from modelo_ia.api import proveedores_ip                           # noqa: E402
from modelo_ia.api.almacen import Almacen                          # noqa: E402

IP_BOGOTA, IP_MEDELLIN, IP_VPN_RUSIA, IP_PRIVADA = "192.0.2.10", "198.51.100.20", "203.0.113.200", "10.0.0.5"


def con_ip(tx="IP1", cliente="CIP", ip=IP_BOGOTA, **red_extra):
    """Un pago completo cuyo bloque `red` trae SOLO la ip (más lo que se pida)."""
    return {**pago(tx, cliente), "red": {"ip": ip, **red_extra}}


def _fila_guardada(ctx, tx):
    con = sqlite3.connect(ctx.bd)
    return json.loads(con.execute("SELECT fila_json FROM evaluaciones WHERE id_transaccion=?", (tx,)).fetchone()[0])


def _bytes_de_la_base(ctx):
    return b"".join(f.read_bytes() for f in ctx.bd.parent.glob(ctx.bd.name + "*"))


def test_con_solo_la_ip_la_api_completa_los_datos_de_red_y_lo_dice(ctx):
    r = ctx.evaluar(con_ip())
    assert r.status_code == 201 and r.json()["modo"] == "normal" and r.json()["red_origen"] == "proveedor:simulado"
    f = _fila_guardada(ctx, "IP1")
    assert (f["tipo_ip"], f["ip_pais"], f["ip_reputacion"], f["ip_es_datacenter"]) == ("Residencial", "Colombia", 88, 0)
    assert f["distancia_ip_ciudad_km"] == 0                                   # IP de Bogotá y compra en Bogotá


def test_la_ip_se_ubica_y_se_calcula_la_distancia_a_la_ciudad_declarada(ctx):
    ctx.evaluar(con_ip("IP2", "C2", IP_VPN_RUSIA))
    f = _fila_guardada(ctx, "IP2")
    assert (f["tipo_ip"], f["ip_pais"], f["ip_es_datacenter"], f["ip_reputacion"]) == ("VPN", "Rusia", 1, 35) and f["distancia_ip_ciudad_km"] > 9000
    ctx.evaluar({**con_ip("IP3", "C3", IP_MEDELLIN), "ciudad": "Bogotá"})
    assert 200 < _fila_guardada(ctx, "IP3")["distancia_ip_ciudad_km"] < 300      # Medellín - Bogotá
    ctx.evaluar(con_ip("IP4", "C4", IP_PRIVADA))
    f4 = _fila_guardada(ctx, "IP4")
    assert (f4["tipo_ip"], f4["ip_pais"]) == ("IP desconocida", "Desconocido") and f4["distancia_ip_ciudad_km"] is None


def test_lo_que_manda_el_comercio_manda_sobre_el_proveedor(ctx):
    ctx.evaluar(con_ip("IP5", "C5", IP_VPN_RUSIA, tipo_ip="RESIDENCIAL", pais="Colombia"))
    f = _fila_guardada(ctx, "IP5")
    assert f["tipo_ip"] == "Residencial" and f["ip_pais"] == "Colombia"          # lo del comercio
    assert f["ip_es_datacenter"] == 1 and f["ip_reputacion"] == 35               # lo que faltaba lo puso el proveedor


def test_la_ip_cruda_no_se_guarda_ni_se_devuelve_ni_se_registra(ctx, caplog):
    ip = "190.24.10.55"
    with caplog.at_level(logging.DEBUG):
        r = ctx.evaluar(con_ip("IP6", "C6", ip))
        ctx.cliente.get(f"/v1/evaluaciones/{r.json()['id_evaluacion']}", headers=ctx.h("A"))
        ctx.evaluar({**con_ip("IP6b", "C6b", ip), "monto_cop": -1})                # un error de validación tampoco la repite
    assert ip not in r.text and ip.encode() not in _bytes_de_la_base(ctx)
    assert ip not in caplog.text
    assert ctx.evaluar({**con_ip("IP6c", "C6c", ip), "monto_cop": -1}).text.count(ip) == 0
    f = _fila_guardada(ctx, "IP6")
    assert ip not in json.dumps(f) and re.fullmatch(r"[0-9a-f]{16}", f["ip_huella"])
    con = sqlite3.connect(ctx.bd)
    assert ip not in " ".join(str(x) for x in con.execute("SELECT detalle FROM auditoria").fetchall())
    r2 = ctx.cliente.get("/v1/revisiones", headers=ctx.h("analista"))
    assert ip not in r2.text


def test_la_huella_de_la_ip_es_estable_y_distinta_por_ip_y_por_servicio(tmp_path):
    a, b = Almacen(tmp_path / "a.sqlite3", "secreto-1"), Almacen(tmp_path / "b.sqlite3", "secreto-2")
    assert a.huella_ip("190.24.10.55") == a.huella_ip("190.24.10.55") != a.huella_ip("190.24.10.56")
    assert a.huella_ip("190.24.10.55") != b.huella_ip("190.24.10.55")                  # sin el secreto no se puede adivinar
    assert a.huella_ip("::1") == a.huella_ip("0:0:0:0:0:0:0:1")                        # IPv6 siempre en su forma canónica


def test_un_reintento_con_ip_es_el_mismo_y_con_otra_ip_es_conflicto(ctx):
    assert ctx.evaluar(con_ip("IP7", "C7", IP_BOGOTA)).status_code == 201
    assert ctx.evaluar(con_ip("IP7", "C7", IP_BOGOTA)).status_code == 200
    assert ctx.evaluar(con_ip("IP7", "C7", IP_MEDELLIN)).status_code == 409


@pytest.mark.parametrize("ip", ["999.1.1.1", "no es una ip", "190.24.10", "1.2.3.4.5", "", "190.24.10.55; DROP TABLE x", "2001:db8::zz"])
def test_una_ip_invalida_da_422(ctx, ip):
    r = ctx.evaluar(con_ip("IPX", "CX", ip))
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")


def test_la_red_necesita_la_ip_o_al_menos_el_tipo_de_ip(ctx):
    p = pago("IP8", "C8")
    p["red"] = {"reputacion": 50}
    r = ctx.evaluar(p)
    assert r.status_code == 422 and "ip" in r.text.lower()
    p["red"] = {"tipo_ip": "RESIDENCIAL", "pais": "Colombia", "es_datacenter": False, "reputacion": 90}
    assert ctx.evaluar(p).json()["red_origen"] == "comercio"                           # sin ip: como antes


def test_ipv6_se_acepta(ctx):
    r = ctx.evaluar(con_ip("IP9", "C9", "2001:db8::1"))
    assert r.status_code == 201 and r.json()["red_origen"] == "proveedor:simulado"


def test_si_el_proveedor_falla_no_se_cae_y_no_deja_la_ip_en_ningun_lado(tmp_path, artefacto, caplog):
    ip = "190.24.10.77"

    class Roto:
        nombre = "roto"

        def consultar(self, ip_):
            raise RuntimeError(f"fallo consultando {ip_}")

    ajustes = Ajustes(ruta_bd=tmp_path / "roto.sqlite3", pepper="p")
    app = crear_app(ajustes, artefacto, reloj=lambda: RELOJ, proveedor_ip=Roto())
    clave = app.state.almacen.crear_clave(app.state.almacen.crear_comercio("t"), "comercio")
    c = TestClient(app)
    with caplog.at_level(logging.DEBUG):
        r = c.post("/v1/evaluaciones", json=con_ip("R1", "CR", ip), headers={"X-API-Key": clave})
    assert r.status_code == 201 and r.json()["red_origen"] == "proveedor_fallo" and r.json()["modo"] == "degradado"
    assert r.json()["decision"] in ("EN_REVISION", "RECHAZADA") and "tipo de IP" in r.json()["motivos"][0]
    assert ip not in r.text and ip not in caplog.text and "fallo consultando" not in caplog.text
    r2 = c.post("/v1/evaluaciones", json={**con_ip("R2", "CR2", ip), "red": {"ip": ip, "tipo_ip": "RESIDENCIAL", "pais": "Colombia", "es_datacenter": False, "reputacion": 90}},
                headers={"X-API-Key": clave})
    assert r2.status_code == 201 and r2.json()["modo"] == "normal" and r2.json()["red_origen"] == "proveedor_fallo"   # el comercio mandó todo: sigue sirviendo


def test_se_puede_enchufar_otro_proveedor_y_la_salud_deja_de_decir_que_es_simulado(tmp_path, artefacto):
    class Otro:
        nombre = "mi_proveedor"

        def consultar(self, ip):
            return proveedores_ip.InfoIP("PROXY", "Alemania", "Fráncfort", True, 20, 50.11, 8.68)

    app = crear_app(Ajustes(ruta_bd=tmp_path / "o.sqlite3", pepper="p"), artefacto, reloj=lambda: RELOJ, proveedor_ip=Otro())
    clave = app.state.almacen.crear_clave(app.state.almacen.crear_comercio("t"), "comercio")
    c = TestClient(app)
    r = c.post("/v1/evaluaciones", json=con_ip("O1", "CO", "8.8.8.8"), headers={"X-API-Key": clave})
    assert r.status_code == 201 and r.json()["red_origen"] == "proveedor:mi_proveedor"
    assert not any("SIMULADA" in a for a in c.get("/v1/salud").json()["advertencias"])
    simulado = construir_contexto(tmp_path / "s", artefacto)
    assert any("SIMULADA" in a for a in simulado.cliente.get("/v1/salud").json()["advertencias"])


def test_el_proveedor_simulado_conoce_sus_rangos_y_solo_esos():
    p = proveedores_ip.crear_proveedor("simulado")
    esperado = {"192.0.2.1": ("RESIDENCIAL", "Colombia"), "198.51.100.9": ("MOVIL", "Colombia"), "203.0.113.5": ("EMPRESARIAL", "Colombia"),
                "203.0.113.100": ("VPN", "Estados Unidos"), "203.0.113.150": ("PROXY", "Países Bajos"), "203.0.113.250": ("VPN", "Rusia"),
                "127.0.0.1": ("DESCONOCIDA", "Desconocido"), "192.168.1.5": ("DESCONOCIDA", "Desconocido"), "8.8.8.8": ("DESCONOCIDA", "Desconocido"), "::1": ("DESCONOCIDA", "Desconocido")}
    for ip, (tipo, pais) in esperado.items():
        i = p.consultar(ip)
        assert (i.tipo_ip, i.pais) == (tipo, pais), ip
        assert i.tipo_ip in proveedores_ip.TIPOS and 0 <= i.reputacion <= 100
    with pytest.raises(ValueError, match="desconocido"):
        proveedores_ip.crear_proveedor("inventado")


def test_la_distancia_entre_la_ciudad_declarada_y_la_de_la_ip():
    medellin = proveedores_ip.InfoIP("MOVIL", "Colombia", "Medellín", False, 88, 6.244, -75.581)
    assert 200 < proveedores_ip.distancia_km("Bogotá", medellin) < 300
    assert proveedores_ip.distancia_km("BOGOTA", medellin) == proveedores_ip.distancia_km("Bogotá", medellin)        # sin tildes ni mayúsculas
    assert proveedores_ip.distancia_km("Ciudad Inventada", medellin) is None
    assert proveedores_ip.distancia_km("Bogotá", proveedores_ip.InfoIP("DESCONOCIDA", "Desconocido", None, False, 30)) is None


def test_una_base_creada_antes_de_esta_columna_se_actualiza_sola(tmp_path):
    ruta = tmp_path / "vieja.sqlite3"
    ddl_viejo = Almacen(tmp_path / "x.sqlite3", "p")
    ddl_viejo.inicializar()
    con = sqlite3.connect(tmp_path / "x.sqlite3")
    con.executescript("ALTER TABLE evaluaciones RENAME TO tmp; CREATE TABLE evaluaciones AS SELECT id, comercio_id, id_transaccion, id_cliente, fecha, huella, fila_json, "
                      "solicitud_hash, decision, decision_modelo, probabilidad, motivos_json, reglas_json, modelo_nombre, modelo_version, modelo_conjunto, modo, latencia_ms, creada FROM tmp; DROP TABLE tmp;")
    con.commit(); con.close()
    (tmp_path / "x.sqlite3").rename(ruta)
    assert "red_origen" not in {r[1] for r in sqlite3.connect(ruta).execute("PRAGMA table_info(evaluaciones)")}
    Almacen(ruta, "p").inicializar()
    assert "red_origen" in {r[1] for r in sqlite3.connect(ruta).execute("PRAGMA table_info(evaluaciones)")}


def test_la_tienda_manda_solo_la_ip_y_la_api_la_ubica(demo):
    c = TestClient(demo.crear_app_demo())
    r = c.post("/demo/tienda/pagar", json=_cuerpo_tienda())
    assert r.status_code == 200 and r.json()["red_origen"] == "proveedor:simulado" and r.json()["modo"] == "normal"
    assert "192.0.2.10" not in r.text                                                            # la IP no vuelve al navegador
    fila = json.loads(sqlite3.connect(demo.BD).execute("SELECT fila_json FROM evaluaciones").fetchone()[0])
    assert (fila["tipo_ip"], fila["ip_pais"]) == ("Residencial", "Colombia") and "192.0.2.10" not in json.dumps(fila)
    assert b"192.0.2.10" not in b"".join(f.read_bytes() for f in demo.BD.parent.glob(demo.BD.name + "*"))


def test_la_tienda_rechaza_una_conexion_que_no_esta_en_la_lista_y_usa_la_ip_real_si_se_pide(demo):
    c = TestClient(demo.crear_app_demo())
    malo = _cuerpo_tienda()
    malo["simulacion"]["ip_prueba"] = "190.24.10.55"
    assert c.post("/demo/tienda/pagar", json=malo).status_code == 422
    real = _cuerpo_tienda()
    real["simulacion"]["ip_prueba"] = "REAL"
    r = c.post("/demo/tienda/pagar", json=real)
    assert r.status_code == 200 and r.json()["red_origen"] == "proveedor:simulado"             # el cliente de pruebas no es una IP: cae en 127.0.0.1


# ------------------------------------------------------------------ estado del dispositivo opcional (modelo sin estado)
ESTADO_RELLENO_PRUEBA = "Conocido"


def sin_estado(tx, cliente="CSE", **cambios):
    p = pago(tx, cliente, **cambios)
    p["dispositivo"].pop("estado")
    return p


def test_sin_estado_se_usa_el_modelo_sin_estado_y_con_estado_el_normal(tmp_path, artefacto, artefacto_sin_estado, monkeypatch):
    c = construir_contexto(tmp_path, artefacto, artefacto_sin_estado)
    usados = []
    original = predecir.analizar_transaccion
    monkeypatch.setattr(predecir, "analizar_transaccion", lambda pago_, art, **kw: (usados.append(art), original(pago_, art, **kw))[1])
    r1, r2 = c.evaluar(pago("E1", "CE1")), c.evaluar(sin_estado("E2", "CE2"))
    assert (r1.status_code, r2.status_code) == (201, 201) and r1.json()["modo"] == r2.json()["modo"] == "normal"
    assert (r1.json()["estado_origen"], r2.json()["estado_origen"]) == ("comercio", "no_enviado")
    assert usados[0] is artefacto and usados[1] is artefacto_sin_estado                    # cada uno con su modelo
    con = sqlite3.connect(c.bd)
    assert [r[0] for r in con.execute("SELECT estado_origen FROM evaluaciones ORDER BY rowid")] == ["comercio", "no_enviado"]


def test_sin_modelo_sin_estado_el_pago_queda_degradado_y_lo_dice(ctx):
    r = ctx.evaluar(sin_estado("E3", "CE3")).json()
    assert r["modo"] == "degradado" and r["decision"] in ("EN_REVISION", "RECHAZADA") and "estado del dispositivo" in r["motivos"][0]
    assert r["estado_origen"] == "no_enviado" and r["puntaje_riesgo"] is None


def test_la_salud_avisa_si_falta_el_modelo_sin_estado(tmp_path, artefacto, artefacto_sin_estado):
    falta = lambda c: any("SIN `dispositivo.estado`" in a for a in c.cliente.get("/v1/salud").json()["advertencias"])
    assert falta(construir_contexto(tmp_path / "a", artefacto)) is True
    assert falta(construir_contexto(tmp_path / "b", artefacto, artefacto_sin_estado)) is False


def _categorias_de_estado(art):
    enc = art["pipeline"].named_steps["preparacion"].named_transformers_["textos"]
    return list(enc.categories_[art["meta"]["categoricas"].index("estado_dispositivo")])


def test_el_modelo_sin_estado_se_entreno_viendo_una_sola_categoria_y_el_normal_varias(artefacto, artefacto_sin_estado):
    """«Sin estado» significa que durante el entrenamiento la columna nunca varió: el modelo no pudo aprender nada de ella."""
    assert _categorias_de_estado(artefacto_sin_estado) == [ESTADO_RELLENO_PRUEBA]
    assert len(_categorias_de_estado(artefacto)) > 1


def test_el_modelo_real_sin_estado_da_exactamente_lo_mismo_para_cualquier_estado():
    if not (config.CARPETA_MODELOS / "modelo_actual_sin_estado.joblib").exists():
        pytest.skip("no hay modelo_actual_sin_estado.joblib")
    from modelo_ia import modelos
    art, normal = predecir.cargar_artefacto_sin_estado(), predecir.cargar_artefacto()

    def prob(a, estado):
        cols = a["meta"]["numericas"] + a["meta"]["categoricas"]
        return float(modelos.puntajes(a["pipeline"], pd.DataFrame([dict(predecir.EJEMPLO_NORMAL, estado_dispositivo=estado)])[cols])[0])
    assert len({prob(art, e) for e in ("Conocido", "Nuevo", "No reconocido")}) == 1          # el estado no mueve ni un decimal
    assert len({prob(normal, e) for e in ("Conocido", "Nuevo", "No reconocido")}) > 1        # el normal sí lo mira


def test_un_estado_invalido_sigue_dando_422(ctx):
    p = pago("E4", "CE4")
    p["dispositivo"]["estado"] = "TAL_VEZ"
    assert ctx.evaluar(p).status_code == 422


def test_el_analista_no_ve_un_estado_que_el_comercio_no_mando(tmp_path, artefacto, artefacto_sin_estado):
    c = construir_contexto(tmp_path, artefacto, artefacto_sin_estado)
    p = sin_estado("E5", "CE5")
    p["dispositivo"] = {"tipo": "IPHONE"}                       # faltan datos del equipo: queda en revisión
    assert c.evaluar(p).json()["decision"] == "EN_REVISION"
    resumen = c.cliente.get("/v1/revisiones", headers=c.h("analista")).json()["revisiones"][0]["resumen_pago"]
    assert "estado_dispositivo" not in resumen and resumen["monto_cop"] == 200000


def test_una_base_vieja_recibe_las_dos_columnas_nuevas(tmp_path):
    ruta = tmp_path / "v.sqlite3"
    Almacen(ruta, "p").inicializar()
    con = sqlite3.connect(ruta)
    con.executescript("ALTER TABLE evaluaciones RENAME TO tmp; CREATE TABLE evaluaciones AS SELECT id, comercio_id, id_transaccion, id_cliente, fecha, huella, fila_json, "
                      "solicitud_hash, decision, decision_modelo, probabilidad, motivos_json, reglas_json, modelo_nombre, modelo_version, modelo_conjunto, modo, latencia_ms, creada FROM tmp; DROP TABLE tmp;")
    con.commit(); con.close()
    Almacen(ruta, "p").inicializar()
    columnas = {r[1] for r in sqlite3.connect(ruta).execute("PRAGMA table_info(evaluaciones)")}
    assert {"red_origen", "estado_origen"} <= columnas


def test_se_entrena_y_se_registra_el_modelo_sin_estado(ruta_csv_completo, carpeta_temporal):
    df, m = entrenar.preparar_datos(ruta_csv_completo, "completo")
    r = entrenar.ajustar_y_evaluar(df, m, "logistica", "completo", sin_estado=True)
    assert r["sin_estado"] is True
    assert predecir.cargar_artefacto_sin_estado() is None                                  # todavía no hay modelo registrado
    ruta = entrenar.guardar(r, ruta_csv_completo, como_actual_sin_estado=True)
    assert "_sinestado_" in ruta.name and json.loads(ruta.with_suffix(".json").read_text(encoding="utf-8"))["sin_estado"] is True
    assert predecir.cargar_artefacto_sin_estado()["meta"]["sin_estado"] is True
    normal = entrenar.ajustar_y_evaluar(df, m, "logistica", "completo")
    with pytest.raises(ValueError, match="--sin-estado"):
        entrenar.guardar(normal, ruta_csv_completo, como_actual_sin_estado=True)             # un modelo normal no puede ocupar ese lugar
