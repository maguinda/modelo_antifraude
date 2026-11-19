"""Los modelos de respuesta del panel NO cambian lo que responde la API.

La idea: se espía cada método de `ConsultasPanel` (lo que la ruta devolvía ANTES de tener modelo de respuesta) y se exige que la respuesta HTTP sea
idéntica, valor por valor y clave por clave. Si el servidor agrega un campo y el modelo no lo declara, esta prueba falla (el campo se perdería en silencio);
si el modelo hace aparecer un `null` donde la clave no venía, también.
"""

import json

import pytest

from modelo_ia.api import esquemas_panel
from modelo_ia.tests.test_api import artefacto, construir_contexto, pago, pago_ip, pago_minimo  # noqa: F401  (artefacto es un fixture)


def _plano(x):
    return json.loads(json.dumps(x, default=str))


def _espiar(ctx, metodo):
    """Envuelve `ConsultasPanel.<metodo>` y guarda lo que devuelve, para compararlo con la respuesta HTTP."""
    consultas = ctx.app.state.consultas
    original = getattr(consultas, metodo)
    vistos = []

    def espia(*a, **kw):
        r = original(*a, **kw)
        vistos.append(_plano(r))
        return r

    setattr(consultas, metodo, espia)
    return vistos


def _igual(ctx, metodo, ruta, quien, **params):
    vistos = _espiar(ctx, metodo)
    r = ctx.cliente.get("/v1/panel/" + ruta, params=params, headers=ctx.h(quien))
    assert r.status_code == 200, r.text
    assert len(vistos) == 1
    assert r.json() == vistos[0], f"{ruta} ({quien}): la respuesta HTTP difiere de lo que devolvió la consulta"
    return r.json()


@pytest.fixture
def ctx_con_datos(tmp_path, artefacto):  # noqa: F811  (se pide el fixture por su nombre)
    ctx = construir_contexto(tmp_path, artefacto)
    for i in range(4):
        for quien, pref in (("A", "A"), ("B", "B")):
            c = pago(f"{pref}-{i}", f"C{pref}{i}", monto_cop=100_000 + i * 1_000)
            c["dispositivo"] = dict(c["dispositivo"], huella="FP-COMPARTIDO" if i < 3 else f"FP-{pref}{i}")
            c["red"] = {"ip": "192.0.2.10" if i < 3 else f"192.0.2.{50 + i}"}
            assert ctx.evaluar(c, quien).status_code == 201
    # un cliente con varias IPs
    for j in range(3):
        assert ctx.evaluar(pago_ip(f"V-{j}", "CVIAJERO", f"198.51.100.{10 + j}"), "A").status_code == 201
    # un pago en revisión, resuelto y con resultado informado (para el detalle completo y la tabla de analistas)
    rv = ctx.evaluar(pago_minimo("R1"), "A").json()
    ctx.id_revisado = rv["id_evaluacion"]
    assert (
        ctx.post(
            f"/v1/revisiones/{ctx.id_revisado}/resolver", {"decision": "APROBADA", "nota": "ok"}, "analista"
        ).status_code
        == 200
    )
    assert ctx.post(f"/v1/evaluaciones/{ctx.id_revisado}/resultado", {"resultado": "LEGITIMA"}, "A").status_code == 200
    # otro en revisión sin resolver
    ctx.id_pendiente = ctx.evaluar(pago_minimo("R2"), "A").json()["id_evaluacion"]
    return ctx


@pytest.mark.parametrize("quien", ["admin", "A"])
def test_transacciones_y_resumen_y_vinculos_y_comercios_responden_igual_que_la_consulta(ctx_con_datos, quien):
    ctx = ctx_con_datos
    t = _igual(ctx, "transacciones", "transacciones", quien, limite=50)
    assert t["total"] >= 5 and t["transacciones"]
    r = _igual(ctx, "resumen", "resumen", quien)
    assert r["totales"]["pagos"] >= 5
    v = _igual(ctx, "vinculos", "vinculos", quien, minimo=2)
    assert v["cliente_con_varias_ips"], "el escenario debe incluir un cliente con varias IPs"
    if quien == "admin":
        assert v["misma_ip_varios_clientes"] and v["mismo_dispositivo_varios_clientes"]
        _igual(ctx, "comercios", "comercios", "analista")


@pytest.mark.parametrize("quien", ["admin", "analista", "A"])
@pytest.mark.parametrize("cual", ["id_revisado", "id_pendiente"])
def test_detalle_responde_igual_que_la_consulta_para_personal_y_para_comercio(ctx_con_datos, quien, cual):
    ctx = ctx_con_datos
    d = _igual(ctx, "detalle", "transacciones/" + getattr(ctx, cual), quien)
    if quien == "A":  # un comercio no recibe estos campos: tienen que seguir AUSENTES, no volverse null
        assert d["senales"] is None
        if d["revision"] is not None:
            assert "resuelta_por" not in d["revision"]


def test_detalle_de_un_pago_normal_tambien(ctx_con_datos):
    ctx = ctx_con_datos
    primero = ctx.cliente.get("/v1/panel/transacciones", params={"limite": 1}, headers=ctx.h("admin")).json()[
        "transacciones"
    ][0]
    _igual(ctx, "detalle", "transacciones/" + primero["id_evaluacion"], "admin")


def test_deriva_y_modelos_responden_igual(ctx_con_datos):
    ctx = ctx_con_datos
    _igual(ctx, "deriva", "deriva", "analista")
    ctx.app.state.almacen.registro_modelos.registrar(
        "m.joblib", "a" * 64, "gb", "0.3.0", "completo", "principal", {"n": 3, "proporciones": [0.1] * 10}
    )
    ctx.app.state.almacen.registro_modelos.promover(1)
    m = _igual(ctx, "modelos", "modelos", "analista")
    assert m and m[0]["estado"]


def test_el_openapi_ya_no_dice_objeto_libre_en_el_panel(ctx_con_datos):
    spec = ctx_con_datos.app.openapi()
    libres = []
    for ruta, ops in spec["paths"].items():
        if not ruta.startswith("/v1/panel/"):
            continue
        esquema = ops["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        if esquema.get("additionalProperties") is True or esquema.get("items", {}).get("additionalProperties") is True:
            libres.append(ruta)
    assert not libres, f"siguen como objeto libre: {libres}"


def test_los_modelos_no_aceptan_campos_nuevos_en_silencio():
    """Guarda contra `extra="allow"`: si alguien lo agrega, los tipos generados pierden precisión (índice `[k: string]: unknown`)."""
    for nombre in dir(esquemas_panel):
        clase = getattr(esquemas_panel, nombre)
        if (
            isinstance(clase, type)
            and issubclass(clase, esquemas_panel.BaseModel)
            and clase is not esquemas_panel.BaseModel
        ):
            assert clase.model_config.get("extra") != "allow", nombre


def test_el_escenario_si_incluye_pagos_en_modo_degradado_sin_puntaje_ni_decision_del_modelo(ctx_con_datos):
    """Sin esto, la igualdad de arriba podría pasar sin haber visto nunca un puntaje nulo (un campo `Optional` que alguien volviera obligatorio)."""
    ctx = ctx_con_datos
    filas = ctx.cliente.get(
        "/v1/panel/transacciones", params={"modo": "degradado", "limite": 50}, headers=ctx.h("admin")
    ).json()
    assert filas["total"] >= 2
    assert all(f["puntaje_riesgo"] is None and f["decision_modelo"] is None for f in filas["transacciones"])
    _igual(ctx, "transacciones", "transacciones", "admin", modo="degradado")
    d = _igual(ctx, "detalle", "transacciones/" + ctx.id_pendiente, "admin")  # degradado y pendiente de analista
    assert d["puntaje_riesgo"] is None and d["decision"]["del_modelo"] is None and d["decision"]["final"] is None


@pytest.mark.solo_sqlite
def test_un_pago_con_monto_nulo_en_la_base_no_rompe_ni_el_listado_ni_el_detalle_ni_el_resumen(ctx_con_datos):
    """El API exige monto, pero la columna acepta NULL (filas viejas o cargadas a mano): el panel debe seguir respondiendo 200, no 500."""
    import sqlite3

    ctx = ctx_con_datos
    con = sqlite3.connect(ctx.bd)
    con.execute("UPDATE evaluaciones SET monto_cop = NULL WHERE id = ?", (ctx.id_pendiente,))
    con.commit()
    con.close()
    lista = _igual(ctx, "transacciones", "transacciones", "admin", limite=200)
    assert any(f["monto_cop"] is None for f in lista["transacciones"])
    d = _igual(ctx, "detalle", "transacciones/" + ctx.id_pendiente, "admin")
    assert d["identificadores"]["id_evaluacion"] == ctx.id_pendiente
    _igual(ctx, "resumen", "resumen", "admin")
    _igual(ctx, "vinculos", "vinculos", "admin", minimo=2)
