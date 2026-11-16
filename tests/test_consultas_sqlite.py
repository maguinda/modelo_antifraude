"""Las lecturas del panel con la base REAL: la traducción de cada filtro a SQL y, sobre todo, que un comercio NUNCA vea filas de otro.

El caso peligroso se arma a propósito: dos comercios que comparten IP y dispositivo (el panel agrupa por esos códigos), y se pide cada endpoint con cada filtro.
"""

from datetime import date
from types import SimpleNamespace

import pytest

from modelo_ia.api.repositorios.filtros_sql import condiciones_de, donde_de
from modelo_ia.tests.test_api import artefacto, construir_contexto, pago  # noqa: F401  (artefacto es un fixture)


def filtros(**cambios):
    base = dict(
        comercio_id=None,
        desde=None,
        hasta=None,
        decision=None,
        modo=None,
        monto_min=None,
        monto_max=None,
        id_cliente=None,
        id_transaccion=None,
        id_seguimiento=None,
        regla=None,
        ip=None,
        dispositivo=None,
        riesgo_min=None,
        riesgo_max=None,
        q=None,
    )
    base.update(cambios)
    return SimpleNamespace(**base)


# ------------------------------------------------------------------ traducción de filtros
CASOS = [
    ({}, [], ()),
    ({"comercio_id": 3}, ["e.comercio_id = ?"], (3,)),
    ({"desde": date(2026, 11, 1)}, ["e.fecha >= ?"], ("2026-11-01",)),
    ({"hasta": date(2026, 11, 30)}, ["e.fecha < ?"], ("2026-12-01",)),  # `hasta` incluye todo ese día
    ({"decision": "RECHAZADA"}, ["e.decision = ?"], ("RECHAZADA",)),
    ({"modo": "degradado"}, ["e.modo = ?"], ("degradado",)),
    ({"monto_min": 5.0}, ["e.monto_cop >= ?"], (5.0,)),
    ({"monto_max": 9.0}, ["e.monto_cop <= ?"], (9.0,)),
    ({"id_cliente": "C1"}, ["e.id_cliente = ?"], ("C1",)),
    ({"id_transaccion": "T1"}, ["e.id_transaccion = ?"], ("T1",)),
    ({"id_seguimiento": "af-aaaa-bbbb"}, ["e.id_seguimiento = ?"], ("AF-AAAA-BBBB",)),
    (
        {"regla": "R1"},
        ["e.id IN (SELECT id_evaluacion FROM evaluacion_reglas WHERE regla = ?)"],
        ("R1",),
    ),
    ({"riesgo_min": 0.2}, ["e.probabilidad >= ?"], (0.2,)),
    ({"riesgo_max": 0.7}, ["e.probabilidad <= ?"], (0.7,)),
    ({"q": "abc"}, ["(e.id_seguimiento = ? OR e.id_transaccion = ? OR e.id_cliente = ?)"], ("ABC", "abc", "abc")),
    ({"ip": "aaaa0001"}, ["e.ip_codigo = ?"], ("AAAA0001",)),
    ({"dispositivo": "bbbb0002"}, ["e.disp_codigo = ?"], ("BBBB0002",)),
]


@pytest.mark.parametrize("pedido,cond,par", CASOS)
def test_cada_filtro_se_traduce_a_su_condicion_con_su_parametro(pedido, cond, par):
    assert condiciones_de(filtros(**pedido)) == (cond, par)


def test_varios_filtros_se_unen_con_AND_en_orden_fijo_y_nada_del_usuario_va_dentro_del_sql():
    f = filtros(ip="aaaa0001", comercio_id=3, decision="APROBADA", q="x'; DROP TABLE evaluaciones;--")
    donde, par = donde_de(f)
    assert (
        donde
        == "WHERE e.comercio_id = ? AND e.decision = ? AND (e.id_seguimiento = ? OR e.id_transaccion = ? OR e.id_cliente = ?) AND e.ip_codigo = ?"
    )
    assert par == (
        3,
        "APROBADA",
        "X'; DROP TABLE EVALUACIONES;--",
        "x'; DROP TABLE evaluaciones;--",
        "x'; DROP TABLE evaluaciones;--",
        "AAAA0001",
    )
    assert "DROP" not in donde and donde_de(filtros()) == ("", ())


# ------------------------------------------------------------------ aislamiento entre comercios
ENDPOINTS = ("/v1/panel/transacciones", "/v1/panel/resumen", "/v1/panel/vinculos", "/v1/panel/comercios")


def valores(x):
    """Todos los textos y números de un JSON, uno por uno (para comparar VALORES exactos: buscar trozos de texto da falsos positivos con ids al azar)."""
    if isinstance(x, dict):
        for k, v in x.items():
            yield from valores(k)
            yield from valores(v)
    elif isinstance(x, list):
        for v in x:
            yield from valores(v)
    else:
        yield x


def escenario(ctx):
    """A y B comparten una IP y un dispositivo; cada uno tiene además pagos propios. Devuelve (codigo_ip_compartida, codigo_dispositivo_compartido, ids de B)."""
    ids_b = []
    for i in range(4):
        for quien, prefijo in (("A", "A"), ("B", "B")):
            c = pago(f"{prefijo}-{i}", f"C{prefijo}{i}", monto_cop=100_000 + i * 1_000 + (0 if quien == "A" else 7))
            c["dispositivo"] = dict(c["dispositivo"], huella="FP-COMPARTIDO" if i < 3 else f"FP-{prefijo}{i}")
            c["red"] = {
                "ip": "192.0.2.10" if i < 3 else (f"192.0.2.{50 + i}" if quien == "A" else f"198.51.100.{50 + i}")
            }
            r = ctx.evaluar(c, quien)
            assert r.status_code == 201
            if quien == "B":
                ids_b.append((r.json()["id_seguimiento"], r.json()["id_evaluacion"], f"B-{i}", f"CB{i}"))
    todas = ctx.cliente.get("/v1/panel/transacciones?limite=200", headers=ctx.h("admin")).json()["transacciones"]
    compartida = next(t for t in todas if t["id_transaccion"] == "A-0")
    return compartida["ip"], compartida["dispositivo"], ids_b


def test_un_comercio_no_ve_filas_ni_nombres_ni_conteos_del_otro_en_ningun_endpoint_ni_filtro(tmp_path, artefacto):  # noqa: F811
    ctx = construir_contexto(tmp_path / "x", artefacto)
    A, B = ctx.comercios["A"], ctx.comercios["B"]
    ip, disp, ids_b = escenario(ctx)
    ajenos = [t for g in ids_b for t in g]
    pedidos = [
        {},
        {"ip": ip},
        {"dispositivo": disp},
        {"comercio_id": A},
        {"decision": "APROBADA"},
        {"desde": "2026-01-01"},
        {"monto_min": 1},
        {"id_cliente": "CB1"},
        {"id_transaccion": "B-1"},
        {"q": "B-2"},
        {"q": ids_b[0][0]},
        {"id_seguimiento": ids_b[1][0]},
        {"riesgo_min": 0.0},
        {"modo": "normal"},
    ]
    for params in pedidos:
        for ruta in ENDPOINTS + ("/v1/revisiones",):
            cuerpo = ctx.cliente.get(ruta, params=params, headers=ctx.h("A"))
            if ruta in ("/v1/revisiones", "/v1/panel/comercios"):
                assert (
                    cuerpo.status_code == 403 and "Tienda B" not in cuerpo.text
                )  # la cola y la lista de comercios son del personal
                continue
            assert cuerpo.status_code == 200, (ruta, params)
            vistos_ahora = list(valores(cuerpo.json()))
            for ajeno in ajenos:
                assert ajeno not in vistos_ahora, f"{ruta} {params}: un comercio ve «{ajeno}» de otro"
            assert "Tienda B" not in cuerpo.text, (ruta, params)
            assert {c["comercio_id"] for c in cuerpo.json().get("transacciones", [])} <= {A}, (ruta, params)
    # lo que SÍ debe ver: solo lo suyo, aunque comparta IP y dispositivo con el otro
    suyas = ctx.cliente.get("/v1/panel/transacciones", params={"ip": ip}, headers=ctx.h("A")).json()
    assert suyas["total"] == 3 and {t["comercio_id"] for t in suyas["transacciones"]} == {A}
    assert ctx.cliente.get("/v1/panel/resumen", headers=ctx.h("A")).json()["totales"]["pagos"] == 4
    vistos = ctx.cliente.get("/v1/panel/vinculos", params={"minimo": 2}, headers=ctx.h("A")).json()[
        "misma_ip_varios_clientes"
    ]
    assert [(v["codigo"], v["clientes"], v["comercios"]) for v in vistos] == [
        (ip, 3, 1)
    ]  # B no se cuela ni como cliente ni como comercio
    # el personal sí ve la IP compartida entre los dos comercios
    todos = ctx.cliente.get("/v1/panel/vinculos", params={"minimo": 2}, headers=ctx.h("admin")).json()[
        "misma_ip_varios_clientes"
    ]
    assert [(v["codigo"], v["clientes"], v["comercios"]) for v in todos] == [(ip, 6, 2)]
    assert {c["id"] for c in ctx.cliente.get("/v1/panel/comercios", headers=ctx.h("admin")).json()} == {
        A,
        B,
    }  # y el personal sí ve la lista completa


def test_el_detalle_ajeno_es_404_y_los_relacionados_no_cuentan_al_otro(tmp_path, artefacto):  # noqa: F811
    ctx = construir_contexto(tmp_path / "y", artefacto)
    ip, disp, ids_b = escenario(ctx)
    for seguimiento, interno, *_ in ids_b:
        for ident in (seguimiento, interno, seguimiento.lower()):
            assert ctx.cliente.get(f"/v1/panel/transacciones/{ident}", headers=ctx.h("A")).status_code == 404
    propio = ctx.cliente.get("/v1/panel/transacciones", params={"id_transaccion": "A-0"}, headers=ctx.h("A")).json()[
        "transacciones"
    ][0]
    d_a = ctx.cliente.get(f"/v1/panel/transacciones/{propio['id_evaluacion']}", headers=ctx.h("A")).json()
    d_admin = ctx.cliente.get(f"/v1/panel/transacciones/{propio['id_evaluacion']}", headers=ctx.h("admin")).json()
    assert (
        d_a["relacionados"]["misma_ip"]["otros_pagos"] == 2 and d_admin["relacionados"]["misma_ip"]["otros_pagos"] == 5
    )  # A: 2 suyos; el personal: 2 + 3 de B
    assert (
        d_a["relacionados"]["mismo_dispositivo"]["otros_pagos"] == 2
        and d_admin["relacionados"]["mismo_dispositivo"]["otros_pagos"] == 5
    )
    ajenos = {t for g in ids_b for t in g}
    assert not ajenos & set(valores(d_a["relacionados"]))  # ningún dato de B, comparando valores exactos


def test_el_repositorio_solo_acepta_las_dos_columnas_de_codigo_porque_van_dentro_del_sql(tmp_path, artefacto):  # noqa: F811
    from modelo_ia.api.almacen import Almacen

    alm = Almacen(tmp_path / "c.sqlite3", "p" * 40)
    alm.inicializar()
    with alm.lectura() as con:
        for malo in ("monto_cop", "ip_codigo = ip_codigo OR 1", "1; DROP TABLE evaluaciones", "", "IP_CODIGO"):
            with pytest.raises(ValueError):
                alm.consultas.pagos_con_codigo(con, malo, "X", "E1", None)
        assert alm.consultas.pagos_con_codigo(con, "ip_codigo", "X", "E1", None) == (0, [])
        assert alm.consultas.pagos_con_codigo(con, "disp_codigo", "X", "E1", 2) == (0, [])


# ------------------------------------------------------------------ cálculos con números conocidos (directo al repositorio)
@pytest.fixture
def alm(tmp_path):
    from modelo_ia.api.almacen import Almacen

    a = Almacen(tmp_path / "k.sqlite3", "p" * 40)
    a.inicializar()
    a.comercio, a.otro = a.crear_comercio("Tienda K"), a.crear_comercio("Tienda L")
    a.contador = 0
    return a


def poner(alm, comercio=None, **cambios):
    """Guarda un pago con valores conocidos; con `revision` pendiente si se pide."""
    alm.contador += 1
    n = alm.contador
    v = dict(
        id=f"{n:08d}-0000-4000-8000-000000000000",
        comercio_id=comercio or alm.comercio,
        id_transaccion=f"T{n}",
        id_cliente=f"C{n}",
        fecha=f"2026-11-{n:02d}T10:00:00",
        huella=None,
        fila_json="{}",
        solicitud_hash=f"h{n}",
        decision="APROBADA",
        decision_modelo="APROBADA",
        probabilidad=0.1,
        motivos_json="[]",
        reglas_json="[]",
        modelo_nombre="m",
        modelo_version="1",
        modelo_conjunto="completo",
        modo="normal",
        latencia_ms=1.0,
        creada=f"2026-11-{n:02d}T10:00:00",
        red_origen="comercio",
        estado_origen="comercio",
        id_seguimiento=f"AF-{n:04d}-ABCD",
        ip_codigo=None,
        disp_codigo=None,
        monto_cop=1000,
        no_enviados_json="[]",
        senales_json=None,
    )
    revision = cambios.pop("revision", False)
    v.update(cambios)
    with alm.transaccion() as con:
        alm.evaluaciones.insertar(con, **v)
        if revision:
            alm.revisiones.crear_pendiente(con, v["id"], v["comercio_id"], v["creada"])
    return v["id"]


def test_el_p95_de_latencia_es_el_valor_en_la_posicion_95_de_los_ordenados(alm):
    for ms in range(20, 0, -1):  # 20 pagos con latencias 1..20 (insertadas al revés)
        poner(alm, latencia_ms=float(ms))
    with alm.lectura() as con:
        r = alm.consultas.resumen(con, filtros(), False)
    assert r["p95"] == 19.0 and r["totales"]["pagos"] == 20  # posición int(0.95 * 19) = 18 → el 19.º valor


def test_las_revisiones_pendientes_del_resumen_son_las_del_comercio_pedido_y_todas_si_no_se_filtra(alm):
    for _ in range(3):
        poner(alm, revision=True)
    for _ in range(2):
        poner(alm, comercio=alm.otro, revision=True)
    poner(alm)  # sin revisión
    with alm.lectura() as con:
        assert alm.consultas.resumen(con, filtros(comercio_id=alm.comercio), False)["pendientes"] == 3
        assert alm.consultas.resumen(con, filtros(comercio_id=alm.otro), False)["pendientes"] == 2
        assert alm.consultas.resumen(con, filtros(), False)["pendientes"] == 5


def test_cada_orden_del_listado_ordena_de_verdad(alm):
    for i, (monto, prob) in enumerate(((300, 0.5), (100, 0.9), (200, 0.1), (400, 0.7))):
        poner(alm, monto_cop=monto, probabilidad=prob, fecha=f"2026-11-0{i + 1}T10:00:00")
    with alm.lectura() as con:

        def ver(orden, campo):
            return [f[campo] for f in alm.consultas.listar(con, filtros(), orden, 50, 0)[1]]

        assert ver("fecha_desc", "fecha") == sorted(ver("fecha_desc", "fecha"), reverse=True) and ver(
            "fecha_desc", "monto_cop"
        ) == [400, 200, 100, 300]
        assert ver("fecha_asc", "monto_cop") == [300, 100, 200, 400]
        assert ver("monto_desc", "monto_cop") == [400, 300, 200, 100]
        assert ver("riesgo_desc", "probabilidad") == [0.9, 0.7, 0.5, 0.1]
        assert [f["monto_cop"] for f in alm.consultas.listar(con, filtros(), "monto_desc", 2, 1)[1]] == [
            300,
            200,
        ]  # página: salta 1, trae 2


def test_las_candidatas_a_rafaga_incluyen_las_ips_con_exactamente_el_minimo_de_pagos(alm):
    for _ in range(3):
        poner(alm, ip_codigo="AAAA0001")
    for _ in range(2):
        poner(alm, ip_codigo="BBBB0002")
    with alm.lectura() as con:
        c = alm.consultas.vinculos(con, filtros(), 3)["candidatas"]
        assert [r["ip_codigo"] for r in c] == ["AAAA0001"] * 3  # justo 3 pagos con minimo 3: entra; la de 2 no
        assert {r["ip_codigo"] for r in alm.consultas.vinculos(con, filtros(), 2)["candidatas"]} == {
            "AAAA0001",
            "BBBB0002",
        }
